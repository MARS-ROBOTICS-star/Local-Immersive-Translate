from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from local_babeldoc_server.translation_audit import ApiCallRecord
from local_babeldoc_server.translation_audit import TranslationAuditWriter
from local_babeldoc_server.translation_audit import replay_api_calls
from local_babeldoc_server.translation_types import BillingStatus
from local_babeldoc_server.translation_types import NormalizedUsage
from local_babeldoc_server.translation_types import PricingSnapshot
from local_babeldoc_server.translation_types import TransportOutcome


PRICING = PricingSnapshot(
    provider="google",
    model="gemini-3.6-flash",
    api_surface="interactions-v1",
    service_tier="standard",
    input_usd_per_million=Decimal("1.5"),
    output_usd_per_million=Decimal("7.5"),
    cached_input_usd_per_million=Decimal("0.15"),
    usd_to_jpy=Decimal("150"),
    tax_included=False,
    captured_at="2026-08-02T00:00:00+08:00",
)


def api_record(
    *,
    request_id: str,
    category: str,
    billing_status: BillingStatus,
    outcome: TransportOutcome,
    usage: NormalizedUsage | None,
    settled_cost: str,
) -> ApiCallRecord:
    return ApiCallRecord(
        request_id=request_id,
        provider_request_id=f"provider-{request_id}",
        request_category=category,
        document_id="doc-1",
        part_index=2,
        paragraph_ids=("part-002/page-041/table-000/block-012",),
        paragraph_count=1,
        source_token_estimate=183,
        semantic_attempt_number=1,
        transport_attempt_number=1,
        billing_status=billing_status,
        transport_outcome=outcome,
        reservation_usd=Decimal("0.020000"),
        settled_cost_usd=Decimal(settled_cost),
        cost_accuracy=(
            "estimated_unknown_billing"
            if billing_status is BillingStatus.UNKNOWN
            else "provider_usage"
        ),
        usage=usage,
        started_at="2026-08-02T10:00:00+08:00",
        finished_at="2026-08-02T10:00:01+08:00",
        error_code=(
            "transport_timeout_unknown_billing"
            if billing_status is BillingStatus.UNKNOWN
            else None
        ),
        text_hashes=("sha256-8f13",),
    )


class TranslationAuditTest(unittest.TestCase):
    def test_records_are_jsonl_safe_and_summary_distinguishes_call_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = TranslationAuditWriter(Path(directory), PRICING)
            writer.record(
                api_record(
                    request_id="req-1",
                    category="batch",
                    billing_status=BillingStatus.CONFIRMED,
                    outcome=TransportOutcome.COMPLETED,
                    usage=NormalizedUsage(
                        prompt_tokens=100,
                        visible_completion_tokens=20,
                        reasoning_tokens=5,
                        cached_tokens=40,
                        tool_use_tokens=0,
                        total_tokens=125,
                    ),
                    settled_cost="0.001",
                )
            )
            writer.record(
                api_record(
                    request_id="req-2",
                    category="fallback",
                    billing_status=BillingStatus.UNKNOWN,
                    outcome=TransportOutcome.RESPONSE_TIMEOUT,
                    usage=None,
                    settled_cost="0.020",
                )
            )
            writer.record(
                api_record(
                    request_id="req-3",
                    category="terminology",
                    billing_status=BillingStatus.NOT_BILLABLE,
                    outcome=TransportOutcome.RATE_LIMITED,
                    usage=None,
                    settled_cost="0",
                )
            )

            summary = writer.checkpoint(
                {
                    "native_paragraphs": 400,
                    "table_ocr_paragraphs": 2,
                    "image_ocr_paragraphs": 0,
                }
            )
            rows = [
                json.loads(line)
                for line in writer.jsonl_path.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(len(rows), 3)
            self.assertEqual(summary.request_count, 3)
            self.assertEqual(summary.billable_request_count, 2)
            self.assertEqual(summary.unknown_billing_count, 1)
            self.assertEqual(summary.batch_request_count, 1)
            self.assertEqual(summary.fallback_request_count, 1)
            self.assertEqual(summary.terminology_request_count, 1)
            self.assertEqual(summary.prompt_tokens, 100)
            self.assertEqual(summary.visible_completion_tokens, 20)
            self.assertIsNone(summary.reasoning_tokens)
            self.assertFalse(summary.reasoning_usage_complete)
            self.assertEqual(summary.cached_tokens, 40)
            self.assertEqual(summary.actual_cost_usd, Decimal("0.021"))
            self.assertEqual(summary.estimated_cost_jpy, Decimal("3.150"))
            self.assertEqual(summary.native_paragraphs, 400)
            self.assertEqual(summary.table_ocr_paragraphs, 2)

    def test_serialized_call_does_not_expose_prompts_keys_or_text(self) -> None:
        record = api_record(
            request_id="req-safe",
            category="batch",
            billing_status=BillingStatus.CONFIRMED,
            outcome=TransportOutcome.COMPLETED,
            usage=NormalizedUsage(total_tokens=1),
            settled_cost="0",
        )

        serialized = json.dumps(record.to_dict(), ensure_ascii=False)

        for forbidden in (
            '"prompt"',
            '"api_key"',
            '"source_text"',
            '"translation"',
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("sha256-8f13", serialized)

    def test_replay_ignores_only_an_incomplete_trailing_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = TranslationAuditWriter(Path(directory), PRICING)
            writer.record(
                api_record(
                    request_id="req-1",
                    category="batch",
                    billing_status=BillingStatus.CONFIRMED,
                    outcome=TransportOutcome.COMPLETED,
                    usage=NormalizedUsage(total_tokens=7),
                    settled_cost="0.001",
                )
            )
            with writer.jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write('{"request_id":"incomplete"')

            summary = replay_api_calls(writer.jsonl_path, PRICING)

            self.assertEqual(summary.request_count, 1)
            self.assertEqual(summary.total_tokens, 7)

    def test_publish_copies_both_audit_files(self) -> None:
        with tempfile.TemporaryDirectory() as working:
            with tempfile.TemporaryDirectory() as output:
                writer = TranslationAuditWriter(Path(working), PRICING)
                writer.record(
                    api_record(
                        request_id="req-1",
                        category="batch",
                        billing_status=BillingStatus.CONFIRMED,
                        outcome=TransportOutcome.COMPLETED,
                        usage=NormalizedUsage(total_tokens=1),
                        settled_cost="0",
                    )
                )
                writer.checkpoint()

                writer.publish(Path(output))

                self.assertTrue((Path(output) / "api_calls.jsonl").is_file())
                self.assertTrue((Path(output) / "usage_summary.json").is_file())


if __name__ == "__main__":
    unittest.main()
