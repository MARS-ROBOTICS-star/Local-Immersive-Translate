from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from typing import Any
from typing import Mapping

from local_babeldoc_server.translation_audit import ApiCallRecord
from local_babeldoc_server.translation_audit import TranslationAuditWriter
from local_babeldoc_server.translation_batching import build_recovery_batches
from local_babeldoc_server.translation_batching import rewritten_batch_response_for_babeldoc
from local_babeldoc_server.translation_batching import rewrite_babeldoc_batch_prompt
from local_babeldoc_server.translation_batching import validate_rewritten_batch_response
from local_babeldoc_server.translation_budget import BudgetExceeded
from local_babeldoc_server.translation_budget import DocumentBudget
from local_babeldoc_server.translation_types import AdapterRequest
from local_babeldoc_server.translation_types import AdapterResponse
from local_babeldoc_server.translation_types import BillingStatus
from local_babeldoc_server.translation_types import PricingSnapshot
from local_babeldoc_server.translation_types import RequestContext
from local_babeldoc_server.translation_types import TransportOutcome
from local_babeldoc_server.translation_types import calculate_usage_cost
from local_babeldoc_server.translator_adapters import ModelConfigurationError
from local_babeldoc_server.translator_adapters import ProviderAdapter


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_hashes(request: AdapterRequest) -> tuple[str, ...]:
    serialized = json.dumps(
        request.input,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return (f"sha256-{hashlib.sha256(serialized).hexdigest()[:16]}",)


def _serialized_bytes(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    )


class TranslationRuntime:
    def __init__(
        self,
        *,
        adapter: ProviderAdapter,
        budget: DocumentBudget,
        audit_writer: TranslationAuditWriter,
        pricing: PricingSnapshot,
        explicit_transport_retries: int = 1,
    ) -> None:
        if explicit_transport_retries < 0:
            raise ValueError("explicit_transport_retries cannot be negative")
        self.adapter = adapter
        self.budget = budget
        self.audit_writer = audit_writer
        self.pricing = pricing
        self.explicit_transport_retries = int(explicit_transport_retries)

    def abort(self, reason: str) -> None:
        self.budget.abort(reason)
        self._checkpoint()

    def close(self, timeout_seconds: float = 30.0) -> None:
        if not self.budget.wait_for_idle(timeout_seconds):
            self.abort("runtime_close_timeout")
            raise BudgetExceeded(
                "translation runtime still has in-flight requests at close"
            )
        self._checkpoint()

    def request(
        self,
        context: RequestContext,
        request: AdapterRequest,
    ) -> AdapterResponse:
        try:
            self.adapter.validate_request(request)
        except ModelConfigurationError:
            self.abort("model_configuration_error")
            raise
        self.budget.assert_semantic_attempt_available(context.paragraph_ids)
        input_token_bound = max(0, int(self.adapter.count_tokens(request)))
        worst_case_cost = self._worst_case_cost(
            input_token_bound,
            request.max_output_tokens,
        )
        attempt_context = context
        retries_used = 0
        while True:
            reservation = self.budget.reserve(
                attempt_context,
                worst_case_cost,
            )
            request_id = uuid.uuid4().hex
            started_at = _utc_now()
            try:
                response = self.adapter.send(request)
            except ModelConfigurationError:
                self.budget.settle_not_billable(
                    reservation,
                    TransportOutcome.CONFIGURATION_ERROR.value,
                )
                self._record(
                    request_id=request_id,
                    context=attempt_context,
                    reservation_usd=reservation.reserved_cost,
                    settled_cost_usd=Decimal("0"),
                    billing_status=BillingStatus.NOT_BILLABLE,
                    transport_outcome=TransportOutcome.CONFIGURATION_ERROR,
                    cost_accuracy="confirmed_not_billable",
                    usage=None,
                    provider_request_id=None,
                    error_code="model_configuration_error",
                    request=request,
                    started_at=started_at,
                )
                self.abort("model_configuration_error")
                raise
            except Exception as error:
                failure = self.adapter.classify_error(error)
                if failure.billing_status is BillingStatus.UNKNOWN:
                    self.budget.settle_unknown(
                        reservation,
                        failure.outcome.value,
                    )
                    settled_cost = reservation.reserved_cost
                    accuracy = "estimated_unknown_billing"
                else:
                    self.budget.settle_not_billable(
                        reservation,
                        failure.outcome.value,
                    )
                    settled_cost = Decimal("0")
                    accuracy = "confirmed_not_billable"
                self._record(
                    request_id=request_id,
                    context=attempt_context,
                    reservation_usd=reservation.reserved_cost,
                    settled_cost_usd=settled_cost,
                    billing_status=failure.billing_status,
                    transport_outcome=failure.outcome,
                    cost_accuracy=accuracy,
                    usage=None,
                    provider_request_id=None,
                    error_code=failure.error_code,
                    request=request,
                    started_at=started_at,
                )
                if failure.retryable and retries_used < self.explicit_transport_retries:
                    retries_used += 1
                    attempt_context = replace(
                        attempt_context,
                        transport_attempt_number=(
                            attempt_context.transport_attempt_number + 1
                        ),
                    )
                    continue
                self.abort(failure.error_code)
                raise

            usage_complete = (
                response.usage.prompt_tokens is not None
                and response.usage.visible_completion_tokens is not None
            )
            if usage_complete:
                settled_cost = calculate_usage_cost(
                    response.usage,
                    self.pricing,
                )
                self.budget.settle_success(reservation, settled_cost)
                accuracy = "provider_usage"
            else:
                settled_cost = reservation.reserved_cost
                self.budget.settle_unknown(
                    reservation,
                    "completed_missing_usage",
                )
                accuracy = "estimated_missing_usage"
            if attempt_context.paragraph_ids:
                self.budget.record_semantic_attempt(
                    attempt_context.paragraph_ids
                )
            self._record(
                request_id=request_id,
                context=attempt_context,
                reservation_usd=reservation.reserved_cost,
                settled_cost_usd=settled_cost,
                billing_status=BillingStatus.CONFIRMED,
                transport_outcome=TransportOutcome.COMPLETED,
                cost_accuracy=accuracy,
                usage=response.usage,
                provider_request_id=response.provider_request_id,
                error_code=None,
                request=request,
                started_at=started_at,
            )
            return response

    def _worst_case_cost(
        self,
        input_tokens: int,
        max_output_tokens: int,
    ) -> Decimal:
        return (
            Decimal(input_tokens) * self.pricing.input_usd_per_million
            + Decimal(max(0, max_output_tokens))
            * self.pricing.output_usd_per_million
        ) / Decimal(1_000_000)

    def _record(
        self,
        *,
        request_id: str,
        context: RequestContext,
        reservation_usd: Decimal,
        settled_cost_usd: Decimal,
        billing_status: BillingStatus,
        transport_outcome: TransportOutcome,
        cost_accuracy: str,
        usage: Any,
        provider_request_id: str | None,
        error_code: str | None,
        request: AdapterRequest,
        started_at: str,
    ) -> None:
        serialized_input_bytes = _serialized_bytes(request.input)
        schema_bytes = (
            _serialized_bytes(request.response_format)
            if request.response_format is not None
            else 0
        )
        protocol_overhead_bytes = max(
            0,
            serialized_input_bytes
            + schema_bytes
            - context.source_text_bytes,
        )
        self.audit_writer.record(
            ApiCallRecord(
                request_id=request_id,
                provider_request_id=provider_request_id,
                request_category=context.request_category,
                document_id=context.document_id,
                part_index=context.part_index,
                paragraph_ids=context.paragraph_ids,
                paragraph_count=context.paragraph_count,
                source_token_estimate=context.source_token_estimate,
                semantic_attempt_number=context.semantic_attempt_number,
                transport_attempt_number=context.transport_attempt_number,
                billing_status=billing_status,
                transport_outcome=transport_outcome,
                reservation_usd=reservation_usd,
                settled_cost_usd=settled_cost_usd,
                cost_accuracy=cost_accuracy,
                usage=usage,
                started_at=started_at,
                finished_at=_utc_now(),
                error_code=error_code,
                text_hashes=_text_hashes(request),
                request_phase=context.request_phase,
                serialized_input_bytes=serialized_input_bytes,
                schema_bytes=schema_bytes,
                source_text_bytes=context.source_text_bytes,
                protocol_overhead_bytes=protocol_overhead_bytes,
                batch_fill_ratio=context.batch_fill_ratio,
                recovery_reason_counts=context.recovery_reason_counts,
                remote_token_count_request=False,
            )
        )
        self._checkpoint()

    def _checkpoint(self) -> None:
        snapshot = self.budget.snapshot()
        self.audit_writer.checkpoint(
            {
                "reserved_cost_usd": snapshot.reserved_cost,
                "committed_cost_usd": snapshot.committed_cost,
                "in_flight_requests": snapshot.in_flight_requests,
                "completed_requests": snapshot.completed_requests,
                "abort_reason": snapshot.abort_reason,
            }
        )


class _AtomicCounter:
    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self._value += int(amount)


class RuntimeBackedTranslator:
    name = "runtime"

    def __init__(
        self,
        *,
        runtime: TranslationRuntime,
        lang_in: str,
        lang_out: str,
        model: str,
        ignore_cache: bool = False,
        default_llm_request_category: str = "batch",
    ) -> None:
        self.runtime = runtime
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.model = model
        self.ignore_cache = bool(ignore_cache)
        self.default_llm_request_category = str(default_llm_request_category)
        self.translate_call_count = 0
        self.translate_cache_call_count = 0
        self.token_count = _AtomicCounter()
        self.prompt_token_count = _AtomicCounter()
        self.completion_token_count = _AtomicCounter()
        self.cache_hit_prompt_token_count = _AtomicCounter()
        self._cache: dict[tuple[str, str], str] = {}
        self._cache_lock = threading.RLock()
        self._batch_local = threading.local()

    @contextmanager
    def batch_context(self, stable_ids: tuple[str, ...] = ()):
        previous = getattr(self._batch_local, "stable_ids", None)
        previous_outcomes = getattr(self._batch_local, "item_outcomes", None)
        self._batch_local.stable_ids = list(stable_ids)
        self._batch_local.item_outcomes = {}
        try:
            yield
        finally:
            if previous is None:
                if hasattr(self._batch_local, "stable_ids"):
                    del self._batch_local.stable_ids
            else:
                self._batch_local.stable_ids = previous
            if previous_outcomes is None:
                if hasattr(self._batch_local, "item_outcomes"):
                    del self._batch_local.item_outcomes
            else:
                self._batch_local.item_outcomes = previous_outcomes

    def collect_batch_stable_id(self, stable_id: str) -> None:
        stable_ids = getattr(self._batch_local, "stable_ids", None)
        if stable_ids is not None:
            stable_ids.append(str(stable_id))

    def is_batch_context_active(self) -> bool:
        return getattr(self._batch_local, "stable_ids", None) is not None

    def batch_item_outcome(self, stable_id: str) -> str | None:
        outcomes = getattr(self._batch_local, "item_outcomes", None)
        if outcomes is None:
            return None
        return outcomes.get(str(stable_id))

    @contextmanager
    def paragraph_context(
        self,
        stable_id: str,
        request_category: str = "fallback",
    ):
        previous_id = getattr(self._batch_local, "paragraph_stable_id", None)
        previous_category = getattr(
            self._batch_local,
            "paragraph_request_category",
            None,
        )
        self._batch_local.paragraph_stable_id = str(stable_id)
        self._batch_local.paragraph_request_category = str(request_category)
        try:
            yield
        finally:
            if previous_id is None:
                if hasattr(self._batch_local, "paragraph_stable_id"):
                    del self._batch_local.paragraph_stable_id
            else:
                self._batch_local.paragraph_stable_id = previous_id
            if previous_category is None:
                if hasattr(self._batch_local, "paragraph_request_category"):
                    del self._batch_local.paragraph_request_category
            else:
                self._batch_local.paragraph_request_category = previous_category

    def add_cache_impact_parameters(self, _key: str, _value: Any) -> None:
        return None

    def translate(
        self,
        text: str,
        ignore_cache: bool = False,
        rate_limit_params: dict | None = None,
    ) -> str:
        return self._translate(
            "simple",
            text,
            ignore_cache,
            rate_limit_params,
        )

    def llm_translate(
        self,
        text: str | None,
        ignore_cache: bool = False,
        rate_limit_params: dict | None = None,
    ) -> str | None:
        if text is None:
            return None
        return self._translate(
            "llm",
            text,
            ignore_cache,
            rate_limit_params,
        )

    def do_translate(
        self,
        text: str,
        rate_limit_params: dict | None = None,
    ) -> str:
        return self._request("simple", text, rate_limit_params)

    def do_llm_translate(
        self,
        text: str | None,
        rate_limit_params: dict | None = None,
    ) -> str | None:
        if text is None:
            return None
        return self._request("llm", text, rate_limit_params)

    def _translate(
        self,
        mode: str,
        text: str,
        ignore_cache: bool,
        rate_limit_params: dict | None,
    ) -> str:
        self.translate_call_count += 1
        cache_key = (mode, text)
        if not (self.ignore_cache or ignore_cache):
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                self.translate_cache_call_count += 1
                return cached
        translated = self._request(mode, text, rate_limit_params)
        if not (self.ignore_cache or ignore_cache):
            with self._cache_lock:
                self._cache[cache_key] = translated
        return translated

    def _request(
        self,
        mode: str,
        text: str,
        rate_limit_params: dict | None,
    ) -> str:
        params: Mapping[str, Any] = rate_limit_params or {}
        collected_batch_ids = getattr(self._batch_local, "stable_ids", None)
        batch_ids = (
            tuple(collected_batch_ids)
            if collected_batch_ids is not None
            else None
        )
        rewritten_batch = None
        if mode == "llm" and batch_ids and params.get("request_json_mode"):
            rewritten_batch = rewrite_babeldoc_batch_prompt(text, batch_ids)
            text = rewritten_batch.prompt
        paragraph_stable_id = getattr(
            self._batch_local,
            "paragraph_stable_id",
            None,
        )
        stable_id_source = (
            (paragraph_stable_id,)
            if mode == "simple" and paragraph_stable_id
            else (
                batch_ids
                or params.get("stable_ids", ())
                or ((paragraph_stable_id,) if paragraph_stable_id else ())
            )
        )
        stable_ids = tuple(
            str(value)
            for value in stable_id_source
        )
        category = str(
            params.get("request_category")
            or getattr(
                self._batch_local,
                "paragraph_request_category",
                None,
            )
            or (
                "fallback"
                if mode == "simple"
                else self.default_llm_request_category
            )
        )
        if not stable_ids and category not in {"terminology", "model_test"}:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            stable_ids = (f"unregistered/sha256-{digest}",)
        response_format = params.get("response_format")
        if (
            response_format is None
            and category == "terminology"
            and params.get("request_json_mode")
        ):
            response_format = {
                "type": "text",
                "mime_type": "application/json",
                "schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "src": {"type": "string"},
                            "tgt": {"type": "string"},
                        },
                        "required": ["src", "tgt"],
                        "additionalProperties": False,
                    },
                },
            }
        snapshot = self.runtime.budget.snapshot()
        next_semantic = max(
            (snapshot.semantic_attempts.get(item, 0) for item in stable_ids),
            default=0,
        ) + 1
        source_token_estimate = int(
            params.get("paragraph_token_count") or len(text)
        )
        source_text_bytes = (
            sum(
                len(source.encode("utf-8"))
                for source in rewritten_batch.source_by_stable_id.values()
            )
            if rewritten_batch is not None
            else len(text.encode("utf-8"))
        )
        request_phase = str(
            params.get("request_phase")
            or (
                "recovery"
                if category in {"fallback", "quality_retry"}
                else "initial"
            )
        )
        context = RequestContext(
            document_id=str(params.get("document_id") or "document"),
            part_index=params.get("part_index"),
            request_category=category,
            paragraph_ids=stable_ids,
            semantic_attempt_number=next_semantic,
            transport_attempt_number=1,
            source_token_estimate=source_token_estimate,
            paragraph_count=len(stable_ids),
            request_phase=request_phase,
            source_text_bytes=source_text_bytes,
            batch_fill_ratio=(
                min(1.0, source_token_estimate / 2400.0)
                if rewritten_batch is not None
                else 0.0
            ),
        )
        response = self.runtime.request(
            context,
            AdapterRequest(
                model=self.model,
                input=(self._simple_prompt(text) if mode == "simple" else text),
                max_output_tokens=int(params.get("max_output_tokens") or 4096),
                response_format=(
                    rewritten_batch.response_format
                    if rewritten_batch is not None
                    else response_format
                ),
                reasoning_level="minimal",
                extra_headers=params.get("extra_headers") or {},
            ),
        )
        self._track_usage(response)
        output_text = response.output_text.strip()
        if rewritten_batch is not None:
            validation = validate_rewritten_batch_response(
                output_text,
                rewritten_batch,
                self.lang_out,
            )
            translations = dict(validation.valid_translations)
            recovery_groups = build_recovery_batches(
                rewritten_batch,
                validation.recovery_ids,
                split_full_batch=validation.parse_error is not None,
            )
            for recovery_group in recovery_groups:
                recovery_ids = recovery_group.expected_stable_ids
                recovery_snapshot = self.runtime.budget.snapshot()
                recovery_attempt = max(
                    (
                        recovery_snapshot.semantic_attempts.get(item, 0)
                        for item in recovery_ids
                    ),
                    default=0,
                ) + 1
                recovery_source_tokens = sum(
                    max(
                        1,
                        (
                            len(
                                recovery_group.source_by_stable_id[
                                    stable_id
                                ].encode("utf-8")
                            )
                            + 3
                        )
                        // 4,
                    )
                    for stable_id in recovery_ids
                )
                recovery_response = self.runtime.request(
                    RequestContext(
                        document_id=str(params.get("document_id") or "document"),
                        part_index=params.get("part_index"),
                        request_category="fallback",
                        paragraph_ids=recovery_ids,
                        semantic_attempt_number=recovery_attempt,
                        transport_attempt_number=1,
                        source_token_estimate=recovery_source_tokens,
                        paragraph_count=len(recovery_ids),
                        request_phase="recovery",
                        source_text_bytes=sum(
                            len(
                                recovery_group.source_by_stable_id[
                                    stable_id
                                ].encode("utf-8")
                            )
                            for stable_id in recovery_ids
                        ),
                        batch_fill_ratio=min(
                            1.0,
                            recovery_source_tokens / 2400.0,
                        ),
                        recovery_reason_counts=dict(
                            Counter(
                                reason
                                for stable_id in recovery_ids
                                for reason in validation.reason_by_stable_id.get(
                                    stable_id,
                                    (),
                                )
                            )
                        ),
                    ),
                    AdapterRequest(
                        model=self.model,
                        input=recovery_group.prompt,
                        max_output_tokens=int(
                            params.get("max_output_tokens") or 4096
                        ),
                        response_format=recovery_group.response_format,
                        reasoning_level="minimal",
                        extra_headers=params.get("extra_headers") or {},
                    ),
                )
                self._track_usage(recovery_response)
                recovery_validation = validate_rewritten_batch_response(
                    recovery_response.output_text.strip(),
                    recovery_group,
                    self.lang_out,
                )
                translations.update(recovery_validation.valid_translations)
            unresolved_ids = tuple(
                stable_id
                for stable_id in rewritten_batch.expected_stable_ids
                if stable_id not in translations
            )
            outcomes = getattr(self._batch_local, "item_outcomes", None)
            if outcomes is not None:
                outcomes.update(
                    {
                        stable_id: (
                            "source_preserved"
                            if stable_id in unresolved_ids
                            else "translated"
                        )
                        for stable_id in rewritten_batch.expected_stable_ids
                    }
                )
            return rewritten_batch_response_for_babeldoc(
                rewritten_batch,
                translations,
                unresolved_ids,
            )
        return output_text

    def _track_usage(self, response: AdapterResponse) -> None:
        usage = response.usage
        self.token_count.inc(usage.total_tokens or 0)
        self.prompt_token_count.inc(usage.prompt_tokens or 0)
        self.completion_token_count.inc(
            (usage.visible_completion_tokens or 0)
            + (usage.reasoning_tokens or 0)
        )
        self.cache_hit_prompt_token_count.inc(usage.cached_tokens or 0)

    def _simple_prompt(self, text: str) -> str:
        return (
            "You are a professional, authentic machine translation engine. "
            f"Translate the following plain text into {self.lang_out}. "
            "Preserve every number and unit exactly as written; do not "
            "convert numeric notation into words or different units. "
            "Preserve every placeholder token such as {v1} exactly as written. "
            "Do not omit or summarize any source text. "
            "Return only translated plain text without JSON, Markdown, "
            "labels, or explanations.\n\n"
            f"{text}"
        )

    def get_formular_placeholder(self, placeholder_id: int | str):
        return (
            "{v" + str(placeholder_id) + "}",
            rf"{{\s*v\s*{placeholder_id}\s*}}",
        )

    def get_rich_text_left_placeholder(self, placeholder_id: int | str):
        return (
            f"<style id='{placeholder_id}'>",
            rf"<\s*style\s*id\s*=\s*'\s*{placeholder_id}\s*'\s*>",
        )

    def get_rich_text_right_placeholder(self, _placeholder_id: int | str):
        return "</style>", r"<\s*\/\s*style\s*>"

    def __str__(self) -> str:
        return f"{self.name} {self.lang_in} {self.lang_out} {self.model}"
