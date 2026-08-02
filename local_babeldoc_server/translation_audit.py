from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import Mapping

from local_babeldoc_server.translation_types import BillingStatus
from local_babeldoc_server.translation_types import NormalizedUsage
from local_babeldoc_server.translation_types import PricingSnapshot
from local_babeldoc_server.translation_types import TransportOutcome


class AuditCorruptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApiCallRecord:
    request_id: str
    provider_request_id: str | None
    request_category: str
    document_id: str
    part_index: int | None
    paragraph_ids: tuple[str, ...]
    paragraph_count: int
    source_token_estimate: int
    semantic_attempt_number: int | None
    transport_attempt_number: int
    billing_status: BillingStatus
    transport_outcome: TransportOutcome
    reservation_usd: Decimal
    settled_cost_usd: Decimal
    cost_accuracy: str
    usage: NormalizedUsage | None
    started_at: str
    finished_at: str
    error_code: str | None = None
    text_hashes: tuple[str, ...] = ()
    request_phase: str = "initial"
    serialized_input_bytes: int = 0
    schema_bytes: int = 0
    source_text_bytes: int = 0
    protocol_overhead_bytes: int = 0
    batch_fill_ratio: float = 0.0
    recovery_reason_counts: Mapping[str, int] = field(default_factory=dict)
    remote_token_count_request: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider_request_id": self.provider_request_id,
            "request_category": self.request_category,
            "document_id": self.document_id,
            "part_index": self.part_index,
            "paragraph_ids": list(self.paragraph_ids),
            "paragraph_count": self.paragraph_count,
            "source_token_estimate": self.source_token_estimate,
            "semantic_attempt_number": self.semantic_attempt_number,
            "transport_attempt_number": self.transport_attempt_number,
            "billing_status": self.billing_status.value,
            "transport_outcome": self.transport_outcome.value,
            "reservation_usd": str(self.reservation_usd),
            "settled_cost_usd": str(self.settled_cost_usd),
            "cost_accuracy": self.cost_accuracy,
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
            "text_hashes": list(self.text_hashes),
            "request_phase": self.request_phase,
            "serialized_input_bytes": self.serialized_input_bytes,
            "schema_bytes": self.schema_bytes,
            "source_text_bytes": self.source_text_bytes,
            "protocol_overhead_bytes": self.protocol_overhead_bytes,
            "batch_fill_ratio": self.batch_fill_ratio,
            "recovery_reason_counts": dict(self.recovery_reason_counts),
            "remote_token_count_request": self.remote_token_count_request,
        }


@dataclass(frozen=True, slots=True)
class UsageSummary:
    request_count: int = 0
    transport_attempt_count: int = 0
    billable_request_count: int = 0
    unknown_billing_count: int = 0
    batch_request_count: int = 0
    fallback_request_count: int = 0
    quality_retry_count: int = 0
    terminology_request_count: int = 0
    initial_batch_request_count: int = 0
    recovery_batch_request_count: int = 0
    protocol_overhead_bytes: int = 0
    prompt_tokens: int = 0
    visible_completion_tokens: int = 0
    reasoning_tokens: int | None = 0
    reasoning_usage_complete: bool = True
    cached_tokens: int = 0
    total_tokens: int = 0
    native_paragraphs: int = 0
    table_ocr_paragraphs: int = 0
    image_ocr_paragraphs: int = 0
    reserved_cost_usd: Decimal = Decimal("0")
    actual_cost_usd: Decimal = Decimal("0")
    committed_cost_usd: Decimal = Decimal("0")
    estimated_cost_jpy: Decimal = Decimal("0")
    in_flight_requests: int = 0
    completed_requests: int = 0
    abort_reason: str | None = None
    pricing_snapshot: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "transport_attempt_count": self.transport_attempt_count,
            "billable_request_count": self.billable_request_count,
            "unknown_billing_count": self.unknown_billing_count,
            "batch_request_count": self.batch_request_count,
            "fallback_request_count": self.fallback_request_count,
            "quality_retry_count": self.quality_retry_count,
            "terminology_request_count": self.terminology_request_count,
            "initial_batch_request_count": self.initial_batch_request_count,
            "recovery_batch_request_count": self.recovery_batch_request_count,
            "protocol_overhead_bytes": self.protocol_overhead_bytes,
            "prompt_tokens": self.prompt_tokens,
            "visible_completion_tokens": self.visible_completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "reasoning_usage_complete": self.reasoning_usage_complete,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "native_paragraphs": self.native_paragraphs,
            "table_ocr_paragraphs": self.table_ocr_paragraphs,
            "image_ocr_paragraphs": self.image_ocr_paragraphs,
            "reserved_cost_usd": str(self.reserved_cost_usd),
            "actual_cost_usd": str(self.actual_cost_usd),
            "committed_cost_usd": str(self.committed_cost_usd),
            "estimated_cost_jpy": str(self.estimated_cost_jpy),
            "in_flight_requests": self.in_flight_requests,
            "completed_requests": self.completed_requests,
            "abort_reason": self.abort_reason,
            "pricing_snapshot": dict(self.pricing_snapshot or {}),
        }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            if index == len(lines) - 1:
                break
            raise AuditCorruptionError(
                f"invalid JSONL record at line {index + 1}"
            ) from exc
        if not isinstance(row, dict):
            raise AuditCorruptionError(
                f"JSONL record at line {index + 1} is not an object"
            )
        rows.append(row)
    return rows


def replay_api_calls(
    path: Path,
    pricing: PricingSnapshot,
    additional: Mapping[str, Any] | None = None,
) -> UsageSummary:
    rows = _read_rows(path)
    categories: dict[str, int] = {}
    prompt_tokens = 0
    visible_completion_tokens = 0
    reasoning_tokens = 0
    cached_tokens = 0
    total_tokens = 0
    reasoning_usage_complete = True
    actual_cost = Decimal("0")
    billable_count = 0
    unknown_count = 0
    initial_batch_count = 0
    recovery_batch_count = 0
    protocol_overhead_bytes = 0
    for row in rows:
        category = str(row.get("request_category") or "")
        categories[category] = categories.get(category, 0) + 1
        request_phase = str(row.get("request_phase") or "initial")
        if category == "batch" and request_phase == "initial":
            initial_batch_count += 1
        if request_phase == "recovery":
            recovery_batch_count += 1
        protocol_overhead_bytes += int(
            row.get("protocol_overhead_bytes") or 0
        )
        status = row.get("billing_status")
        is_billable = status in {
            BillingStatus.CONFIRMED.value,
            BillingStatus.UNKNOWN.value,
        }
        if is_billable:
            billable_count += 1
        if status == BillingStatus.UNKNOWN.value:
            unknown_count += 1
        actual_cost += Decimal(str(row.get("settled_cost_usd") or "0"))
        usage = row.get("usage")
        if not isinstance(usage, dict):
            if is_billable:
                reasoning_usage_complete = False
            continue
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        visible_completion_tokens += int(
            usage.get("visible_completion_tokens") or 0
        )
        cached_tokens += int(usage.get("cached_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        reasoning_value = usage.get("reasoning_tokens")
        if reasoning_value is None:
            if is_billable:
                reasoning_usage_complete = False
        else:
            reasoning_tokens += int(reasoning_value)

    additional = dict(additional or {})
    reserved_cost = Decimal(str(additional.get("reserved_cost_usd") or "0"))
    committed_cost = Decimal(
        str(additional.get("committed_cost_usd") or actual_cost + reserved_cost)
    )
    return UsageSummary(
        request_count=len(rows),
        transport_attempt_count=len(rows),
        billable_request_count=billable_count,
        unknown_billing_count=unknown_count,
        batch_request_count=categories.get("batch", 0),
        fallback_request_count=categories.get("fallback", 0),
        quality_retry_count=categories.get("quality_retry", 0),
        terminology_request_count=categories.get("terminology", 0),
        initial_batch_request_count=initial_batch_count,
        recovery_batch_request_count=recovery_batch_count,
        protocol_overhead_bytes=protocol_overhead_bytes,
        prompt_tokens=prompt_tokens,
        visible_completion_tokens=visible_completion_tokens,
        reasoning_tokens=(
            reasoning_tokens if reasoning_usage_complete else None
        ),
        reasoning_usage_complete=reasoning_usage_complete,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        native_paragraphs=int(additional.get("native_paragraphs") or 0),
        table_ocr_paragraphs=int(
            additional.get("table_ocr_paragraphs") or 0
        ),
        image_ocr_paragraphs=int(
            additional.get("image_ocr_paragraphs") or 0
        ),
        reserved_cost_usd=reserved_cost,
        actual_cost_usd=actual_cost,
        committed_cost_usd=committed_cost,
        estimated_cost_jpy=actual_cost * pricing.usd_to_jpy,
        in_flight_requests=int(additional.get("in_flight_requests") or 0),
        completed_requests=int(
            additional.get("completed_requests") or len(rows)
        ),
        abort_reason=additional.get("abort_reason"),
        pricing_snapshot=pricing.to_dict(),
    )


class TranslationAuditWriter:
    def __init__(
        self,
        directory: Path,
        pricing: PricingSnapshot,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.directory / "api_calls.jsonl"
        self.summary_path = self.directory / "usage_summary.json"
        self.pricing = pricing
        self._lock = threading.RLock()

    def record(self, record: ApiCallRecord) -> None:
        encoded = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        with self._lock:
            with self.jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

    def checkpoint(
        self,
        additional: Mapping[str, Any] | None = None,
    ) -> UsageSummary:
        with self._lock:
            summary = replay_api_calls(
                self.jsonl_path,
                self.pricing,
                additional,
            )
            temporary = self.summary_path.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(
                    summary.to_dict(),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.summary_path)
            return summary

    def publish(self, output_directory: Path) -> None:
        destination = Path(output_directory)
        destination.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self.jsonl_path.exists():
                shutil.copy2(
                    self.jsonl_path,
                    destination / self.jsonl_path.name,
                )
            if self.summary_path.exists():
                shutil.copy2(
                    self.summary_path,
                    destination / self.summary_path.name,
                )
