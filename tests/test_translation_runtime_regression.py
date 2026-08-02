from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from local_babeldoc_server.ocr_safety import OcrSafetySnapshot
from local_babeldoc_server.ocr_safety import PageTextStats
from local_babeldoc_server.ocr_safety import evaluate_ocr_safety
from local_babeldoc_server.translation_batching import partition_batch_indices
from local_babeldoc_server.translation_batching import validate_translation_set


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "translation_runtime"
    / "borges_61_page_profile.json"
)


class TranslationRuntimeRegressionTest(unittest.TestCase):
    def load_profile(self):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_borges_profile_stays_below_document_request_budget(self) -> None:
        profile = self.load_profile()
        self.assertEqual(profile["page_count"], 61)
        paragraphs = profile["paragraphs"]
        self.assertEqual(len(paragraphs), 600)
        self.assertEqual(len({item["id"] for item in paragraphs}), 600)
        self.assertNotIn("text", json.dumps(profile))
        self.assertTrue(
            all(item["source_hash"].startswith("sha256-") for item in paragraphs)
        )
        table_counts = Counter(
            item["page_number"]
            for item in paragraphs
            if item["region_type"] == "native_table"
        )
        self.assertEqual(
            table_counts,
            Counter({page: 4 for page in profile["native_table_pages"]}),
        )
        token_counts = [item["source_tokens"] for item in paragraphs]

        batches = partition_batch_indices(token_counts)
        exposures = Counter()
        for start, end in batches:
            expected_ids = tuple(
                item["id"] for item in paragraphs[start:end]
            )
            expected_aliases = tuple(
                str(index) for index in range(len(expected_ids))
            )
            fake_output = json.dumps(
                {
                    "t": [
                        {"i": alias, "t": f"translated-{index}"}
                        for index, alias in enumerate(expected_aliases)
                    ]
                }
            )
            validation = validate_translation_set(fake_output, expected_aliases)
            self.assertEqual(validation.fallback_ids, ())
            exposures.update(expected_ids)

        self.assertLessEqual(len(batches), profile["max_requests"])
        self.assertTrue(all(end - start <= 40 for start, end in batches))
        self.assertTrue(all(value <= 2 for value in exposures.values()))

    def test_borges_profile_table_ocr_anomaly_blocks_before_any_send(self) -> None:
        profile = self.load_profile()
        native_counts = Counter(
            item["page_number"] for item in profile["paragraphs"]
        )
        page_stats = tuple(
            PageTextStats(
                page_number=index,
                native_paragraphs=native_counts[index],
                ocr_paragraphs=(
                    profile["anomalous_table_ocr_blocks"]
                    if index == profile["anomalous_table_page"]
                    else 0
                ),
            )
            for index in range(1, profile["page_count"] + 1)
        )
        snapshot = OcrSafetySnapshot(
            page_stats=page_stats,
            native_paragraphs=len(profile["paragraphs"]),
            table_ocr_paragraphs=profile["anomalous_table_ocr_blocks"],
            image_ocr_paragraphs=0,
            ocr_blocks_per_table={
                "part-000/page-011/table-000": profile[
                    "anomalous_table_ocr_blocks"
                ]
            },
            duplicate_pairs=(),
        )
        adapter_send_count = 0

        report = evaluate_ocr_safety(snapshot)
        if report.safe:
            adapter_send_count += 1

        self.assertFalse(report.safe)
        self.assertEqual(adapter_send_count, 0)
        self.assertIn("max_table_ocr_paragraphs", report.reasons)
        self.assertIn("max_ocr_blocks_per_table", report.reasons)


if __name__ == "__main__":
    unittest.main()
