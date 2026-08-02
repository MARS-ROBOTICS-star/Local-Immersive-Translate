from __future__ import annotations

import json
import unittest

from local_babeldoc_server.ocr_safety import OcrRegion
from local_babeldoc_server.ocr_safety import OcrSafetySnapshot
from local_babeldoc_server.ocr_safety import PageTextStats
from local_babeldoc_server.ocr_safety import ParagraphOriginRegistry
from local_babeldoc_server.ocr_safety import classify_document_text_mode
from local_babeldoc_server.ocr_safety import evaluate_ocr_safety
from local_babeldoc_server.ocr_safety import is_duplicate_text_region


class Paragraph:
    pass


class OcrSafetyTest(unittest.TestCase):
    def test_stable_id_is_primary_and_object_index_is_cleared_by_part(self) -> None:
        registry = ParagraphOriginRegistry()
        paragraph = Paragraph()

        record = registry.register_table_ocr(
            paragraph,
            part_index=2,
            page_number=41,
            table_index=0,
            block_index=12,
            bbox=(120.2, 340.1, 760.4, 510.2),
            text="terrain",
        )

        self.assertEqual(
            record.stable_id,
            "part-002/page-041/table-000/block-012",
        )
        self.assertEqual(registry.stable_id(paragraph), record.stable_id)
        self.assertNotIn(str(id(paragraph)), json.dumps(record.to_dict()))
        registry.clear_part(2)
        self.assertIsNone(registry.stable_id(paragraph))
        self.assertNotIn(record.stable_id, registry.origin_by_stable_id)

    def test_document_mode_distinguishes_digital_scanned_and_hybrid(self) -> None:
        digital = [
            PageTextStats(page_number=index, native_paragraphs=5, ocr_paragraphs=0)
            for index in range(10)
        ]
        scanned = [
            PageTextStats(page_number=index, native_paragraphs=0, ocr_paragraphs=8)
            for index in range(10)
        ]
        hybrid = digital[:5] + scanned[5:]

        self.assertEqual(classify_document_text_mode(digital), "born_digital")
        self.assertEqual(classify_document_text_mode(scanned), "scanned")
        self.assertEqual(classify_document_text_mode(hybrid), "hybrid")

    def test_duplicate_requires_both_bbox_overlap_and_text_similarity(self) -> None:
        native = OcrRegion("Terrain Cost", (0, 0, 100, 20))
        same_text_elsewhere = OcrRegion("Terrain Cost", (0, 50, 100, 70))
        overlap_different_text = OcrRegion("Vehicle Speed", (0, 0, 100, 20))
        overlap_similar_text = OcrRegion("terrain  cost", (5, 0, 95, 20))

        self.assertFalse(is_duplicate_text_region(native, same_text_elsewhere))
        self.assertFalse(is_duplicate_text_region(native, overlap_different_text))
        self.assertTrue(is_duplicate_text_region(native, overlap_similar_text))

    def test_scanned_document_does_not_fail_only_for_high_ocr_native_ratio(self) -> None:
        snapshot = OcrSafetySnapshot(
            page_stats=tuple(
                PageTextStats(index, native_paragraphs=0, ocr_paragraphs=50)
                for index in range(10)
            ),
            native_paragraphs=0,
            table_ocr_paragraphs=150,
            image_ocr_paragraphs=350,
            ocr_blocks_per_table={"table-1": 50},
            duplicate_pairs=(),
        )

        report = evaluate_ocr_safety(snapshot)

        self.assertEqual(report.document_mode, "scanned")
        self.assertTrue(report.safe)
        self.assertNotIn("ocr_native_ratio", report.reasons)

    def test_born_digital_ratio_and_absolute_limits_fail(self) -> None:
        ratio_snapshot = OcrSafetySnapshot(
            page_stats=(PageTextStats(1, 100, 60),),
            native_paragraphs=100,
            table_ocr_paragraphs=60,
            image_ocr_paragraphs=0,
            ocr_blocks_per_table={"table-1": 60},
            duplicate_pairs=(),
        )
        absolute_snapshot = OcrSafetySnapshot(
            page_stats=(PageTextStats(1, 400, 201),),
            native_paragraphs=400,
            table_ocr_paragraphs=201,
            image_ocr_paragraphs=0,
            ocr_blocks_per_table={"table-1": 100, "table-2": 101},
            duplicate_pairs=(),
        )

        ratio_report = evaluate_ocr_safety(ratio_snapshot)
        absolute_report = evaluate_ocr_safety(absolute_snapshot)

        self.assertFalse(ratio_report.safe)
        self.assertIn("ocr_native_ratio", ratio_report.reasons)
        self.assertFalse(absolute_report.safe)
        self.assertIn("max_table_ocr_paragraphs", absolute_report.reasons)
        self.assertIn("max_ocr_blocks_per_table", absolute_report.reasons)

    def test_hybrid_ratio_is_checked_per_page_with_minimum_native_guard(self) -> None:
        snapshot = OcrSafetySnapshot(
            page_stats=(
                PageTextStats(1, native_paragraphs=10, ocr_paragraphs=9),
                PageTextStats(2, native_paragraphs=40, ocr_paragraphs=25),
                PageTextStats(3, native_paragraphs=0, ocr_paragraphs=30),
            ),
            native_paragraphs=50,
            table_ocr_paragraphs=64,
            image_ocr_paragraphs=0,
            ocr_blocks_per_table={"table-1": 64},
            duplicate_pairs=(),
            document_mode="hybrid",
        )

        report = evaluate_ocr_safety(snapshot)

        self.assertFalse(report.safe)
        self.assertEqual(report.ratio_failure_pages, (2,))

    def test_duplicate_injection_is_a_fail_fast_reason(self) -> None:
        snapshot = OcrSafetySnapshot(
            page_stats=(PageTextStats(1, 40, 1),),
            native_paragraphs=40,
            table_ocr_paragraphs=1,
            image_ocr_paragraphs=0,
            ocr_blocks_per_table={"table-1": 1},
            duplicate_pairs=(("native-p001", "ocr-p001"),),
        )

        report = evaluate_ocr_safety(snapshot)

        self.assertFalse(report.safe)
        self.assertIn("duplicate_ocr_injection", report.reasons)


if __name__ == "__main__":
    unittest.main()
