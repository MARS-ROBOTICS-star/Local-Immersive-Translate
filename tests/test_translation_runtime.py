from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from local_babeldoc_server.translation_audit import TranslationAuditWriter
from local_babeldoc_server.translation_budget import BudgetExceeded
from local_babeldoc_server.translation_budget import DocumentBudget
from local_babeldoc_server.translation_budget import AttemptLimitExceeded
from local_babeldoc_server.translation_runtime import RuntimeBackedTranslator
from local_babeldoc_server.translation_runtime import TranslationRuntime
from local_babeldoc_server.translation_types import AdapterRequest
from local_babeldoc_server.translation_types import AdapterResponse
from local_babeldoc_server.translation_types import BillingStatus
from local_babeldoc_server.translation_types import NormalizedUsage
from local_babeldoc_server.translation_types import PricingSnapshot
from local_babeldoc_server.translation_types import ProviderCapabilities
from local_babeldoc_server.translation_types import RequestContext
from local_babeldoc_server.translation_types import TransportOutcome
from local_babeldoc_server.translator_adapters import ModelConfigurationError
from local_babeldoc_server.translator_adapters import TransportFailure


PRICING = PricingSnapshot(
    provider="google",
    model="fake-model",
    api_surface="fake",
    service_tier="standard",
    input_usd_per_million=Decimal("1.5"),
    output_usd_per_million=Decimal("7.5"),
    cached_input_usd_per_million=Decimal("0.15"),
    usd_to_jpy=Decimal("150"),
    tax_included=False,
    captured_at="2026-08-02T00:00:00+08:00",
)

CAPABILITIES = ProviderCapabilities(
    supports_structured_output=True,
    supports_reasoning_control=True,
    supported_reasoning_levels=frozenset({"minimal"}),
    exposes_reasoning_usage=True,
    supports_cached_usage=True,
    supports_count_tokens=True,
    supports_request_id=True,
)


class Fake503(Exception):
    status_code = 503


class FakeReadTimeout(Exception):
    pass


class FakeAdapter:
    model = "fake-model"
    capabilities = CAPABILITIES

    def __init__(self, outcomes, budget=None):
        self.outcomes = list(outcomes)
        self.budget = budget
        self.send_count = 0
        self.validate_count = 0
        self.in_flight_seen: list[int] = []
        self.sent_requests = []

    def validate_request(self, request):
        self.validate_count += 1

    def count_tokens(self, request):
        return 100

    def send(self, request):
        self.send_count += 1
        self.sent_requests.append(request)
        if self.budget is not None:
            self.in_flight_seen.append(
                self.budget.snapshot().in_flight_requests
            )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def classify_error(self, error):
        if isinstance(error, Fake503):
            return TransportFailure(
                outcome=TransportOutcome.UNAVAILABLE,
                billing_status=BillingStatus.NOT_BILLABLE,
                retryable=True,
                error_code="transport_unavailable",
            )
        return TransportFailure(
            outcome=TransportOutcome.RESPONSE_TIMEOUT,
            billing_status=BillingStatus.UNKNOWN,
            retryable=False,
            error_code="transport_timeout_unknown_billing",
        )


class RejectingAdapter(FakeAdapter):
    def validate_request(self, request):
        raise ModelConfigurationError("strict schema unsupported")


def response(text="译文"):
    return AdapterResponse(
        output_text=text,
        provider_request_id="provider-1",
        finish_reason="completed",
        usage=NormalizedUsage(
            prompt_tokens=100,
            visible_completion_tokens=20,
            reasoning_tokens=5,
            cached_tokens=0,
            tool_use_tokens=0,
            total_tokens=125,
        ),
    )


def context(transport_attempt=1):
    return RequestContext(
        document_id="doc-1",
        part_index=0,
        request_category="batch",
        paragraph_ids=("p001",),
        semantic_attempt_number=1,
        transport_attempt_number=transport_attempt,
        source_token_estimate=100,
        paragraph_count=1,
    )


class TranslationRuntimeTest(unittest.TestCase):
    def make_runtime(self, directory, adapter, *, retries=1):
        budget = DocumentBudget(
            max_requests=20,
            max_cost_usd=Decimal("1"),
        )
        adapter.budget = budget
        writer = TranslationAuditWriter(Path(directory), PRICING)
        runtime = TranslationRuntime(
            adapter=adapter,
            budget=budget,
            audit_writer=writer,
            pricing=PRICING,
            explicit_transport_retries=retries,
        )
        return runtime, budget, writer

    def test_success_reserves_before_send_then_settles_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([response()])
            runtime, budget, writer = self.make_runtime(directory, adapter)

            result = runtime.request(
                context(),
                AdapterRequest(model="fake-model", input="translate"),
            )

            self.assertEqual(result.output_text, "译文")
            self.assertEqual(adapter.in_flight_seen, [1])
            snapshot = budget.snapshot()
            self.assertEqual(snapshot.in_flight_requests, 0)
            self.assertEqual(snapshot.completed_requests, 1)
            self.assertEqual(snapshot.semantic_attempts["p001"], 1)
            self.assertEqual(snapshot.billable_exposures["p001"], 1)
            rows = writer.jsonl_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            self.assertTrue(writer.summary_path.is_file())

    def test_503_retry_is_two_transport_attempts_but_one_semantic_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([Fake503(), response()])
            runtime, budget, writer = self.make_runtime(directory, adapter)

            runtime.request(
                context(),
                AdapterRequest(model="fake-model", input="translate"),
            )

            snapshot = budget.snapshot()
            self.assertEqual(adapter.send_count, 2)
            self.assertEqual(snapshot.request_count, 2)
            self.assertEqual(snapshot.completed_requests, 2)
            self.assertEqual(snapshot.semantic_attempts["p001"], 1)
            self.assertEqual(snapshot.billable_exposures["p001"], 1)
            self.assertEqual(
                len(writer.jsonl_path.read_text(encoding="utf-8").splitlines()),
                2,
            )

    def test_read_timeout_keeps_worst_case_cost_and_unknown_billing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([FakeReadTimeout()])
            runtime, budget, writer = self.make_runtime(directory, adapter)

            with self.assertRaises(FakeReadTimeout):
                runtime.request(
                    context(),
                    AdapterRequest(model="fake-model", input="translate"),
                )

            snapshot = budget.snapshot()
            self.assertEqual(snapshot.request_count, 1)
            self.assertGreater(snapshot.actual_cost, Decimal("0"))
            self.assertEqual(snapshot.billable_exposures["p001"], 1)
            summary = writer.checkpoint()
            self.assertEqual(summary.unknown_billing_count, 1)
            self.assertIsNone(summary.reasoning_tokens)

    def test_local_configuration_failure_does_not_reserve_or_send(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = RejectingAdapter([])
            runtime, budget, writer = self.make_runtime(directory, adapter)

            with self.assertRaises(ModelConfigurationError):
                runtime.request(
                    context(),
                    AdapterRequest(
                        model="fake-model",
                        input="translate",
                        response_format={"type": "json_schema"},
                    ),
                )

            self.assertEqual(budget.snapshot().request_count, 0)
            self.assertEqual(
                budget.snapshot().abort_reason,
                "model_configuration_error",
            )
            self.assertEqual(adapter.send_count, 0)
            self.assertFalse(writer.jsonl_path.exists())

    def test_aborted_runtime_refuses_adapter_send(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([response()])
            runtime, budget, _writer = self.make_runtime(directory, adapter)
            runtime.abort("ocr_anomaly")

            with self.assertRaises(BudgetExceeded):
                runtime.request(
                    context(),
                    AdapterRequest(model="fake-model", input="translate"),
                )

            self.assertEqual(adapter.send_count, 0)
            self.assertEqual(budget.snapshot().abort_reason, "ocr_anomaly")

    def test_runtime_backed_translator_routes_simple_and_batch_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([response("单段"), response("批量")])
            runtime, budget, _writer = self.make_runtime(directory, adapter)
            translator = RuntimeBackedTranslator(
                runtime=runtime,
                lang_in="en",
                lang_out="zh",
                model="fake-model",
            )

            simple = translator.translate(
                "source",
                rate_limit_params={"stable_ids": ("p001",)},
            )
            batch = translator.llm_translate(
                "batch prompt",
                rate_limit_params={
                    "stable_ids": ("p002",),
                    "request_category": "batch",
                    "request_json_mode": True,
                    "response_format": {"type": "json_schema"},
                },
            )

            self.assertEqual(simple, "单段")
            self.assertEqual(batch, "批量")
            self.assertEqual(budget.snapshot().request_count, 2)
            self.assertEqual(translator.translate_call_count, 2)
            self.assertEqual(translator.token_count.value, 250)

    def test_terminology_translator_uses_its_own_category_without_paragraph_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([response('{"terms": []}')])
            runtime, budget, writer = self.make_runtime(directory, adapter)
            translator = RuntimeBackedTranslator(
                runtime=runtime,
                lang_in="en",
                lang_out="zh",
                model="fake-model",
                default_llm_request_category="terminology",
            )

            translator.llm_translate(
                "extract terminology",
                rate_limit_params={"request_json_mode": True},
            )

            snapshot = budget.snapshot()
            self.assertEqual(snapshot.semantic_attempts, {})
            self.assertEqual(snapshot.billable_exposures, {})
            record = json.loads(
                writer.jsonl_path.read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(record["request_category"], "terminology")
            self.assertEqual(record["paragraph_ids"], [])
            sent = adapter.sent_requests[0]
            self.assertEqual(sent.response_format["mime_type"], "application/json")
            self.assertEqual(sent.response_format["schema"]["type"], "array")

    def test_batch_context_rewrites_stable_request_and_maps_response_to_indices(self) -> None:
        stable_ids = ("part-001/page-003/paragraph-007", "part-001/page-003/paragraph-008")
        stable_response = AdapterResponse(
            output_text=(
                '{"translations":['
                f'{{"id":"{stable_ids[1]}","translation":"第二段"}},'
                f'{{"id":"{stable_ids[0]}","translation":"第一段"}}]}}'
            ),
            provider_request_id="provider-batch",
            finish_reason="completed",
            usage=response().usage,
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([stable_response])
            runtime, _budget, _writer = self.make_runtime(directory, adapter)
            translator = RuntimeBackedTranslator(
                runtime=runtime,
                lang_in="en",
                lang_out="zh",
                model="fake-model",
            )
            upstream_prompt = (
                "rules\n\n## Here is the input:\n\n"
                '[{"id":0,"input":"First"},{"id":1,"input":"Second"}]'
            )

            with translator.batch_context(stable_ids):
                translated = translator.llm_translate(
                    upstream_prompt,
                    rate_limit_params={"request_json_mode": True},
                )

            self.assertEqual(
                translated,
                '[{"id": 0, "output": "第一段"}, {"id": 1, "output": "第二段"}]',
            )
            sent = adapter.sent_requests[0]
            self.assertIn(stable_ids[0], sent.input)
            self.assertEqual(
                sent.response_format["schema"]["properties"]["translations"]
                ["items"]["properties"]["id"]["enum"],
                list(stable_ids),
            )

    def test_batch_and_fallback_share_stable_attempt_limit(self) -> None:
        stable_id = "part-000/page-001/paragraph-004"
        batch_response = AdapterResponse(
            output_text=(
                '{"translations":['
                f'{{"id":"{stable_id}","translation":"批量"}}]}}'
            ),
            provider_request_id="provider-batch",
            finish_reason="completed",
            usage=response().usage,
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter(
                [batch_response, response("单段"), response("不应发送")]
            )
            runtime, budget, _writer = self.make_runtime(directory, adapter)
            translator = RuntimeBackedTranslator(
                runtime=runtime,
                lang_in="en",
                lang_out="zh",
                model="fake-model",
            )
            upstream_prompt = (
                "rules\n\n## Here is the input:\n\n"
                '[{"id":0,"input":"First"}]'
            )

            with translator.batch_context((stable_id,)):
                translator.llm_translate(
                    upstream_prompt,
                    rate_limit_params={"request_json_mode": True},
                )
            with translator.paragraph_context(stable_id, "fallback"):
                translator.translate("First", ignore_cache=True)
            with self.assertRaises(AttemptLimitExceeded):
                with translator.paragraph_context(stable_id, "quality_retry"):
                    translator.translate("First", ignore_cache=True)

            self.assertEqual(adapter.send_count, 2)
            self.assertEqual(budget.snapshot().semantic_attempts[stable_id], 2)

    def test_close_refuses_to_hide_unsettled_in_flight_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter([])
            runtime, budget, writer = self.make_runtime(directory, adapter)
            reservation = budget.reserve(context(), Decimal("0.01"))

            with self.assertRaisesRegex(BudgetExceeded, "in-flight"):
                runtime.close(timeout_seconds=0)

            self.assertEqual(
                budget.snapshot().abort_reason,
                "runtime_close_timeout",
            )
            self.assertTrue(writer.summary_path.is_file())
            budget.settle_not_billable(reservation, "test_cleanup")


if __name__ == "__main__":
    unittest.main()
