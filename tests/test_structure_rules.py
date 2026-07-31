import unittest
from types import SimpleNamespace

from local_babeldoc_server.structure_rules import REFERENCE_LABEL
from local_babeldoc_server.structure_rules import TOC_LABEL
from local_babeldoc_server.structure_rules import display_width
from local_babeldoc_server.structure_rules import mark_document_structure
from local_babeldoc_server.structure_rules import parse_toc_entry
from local_babeldoc_server.structure_rules import rebuild_toc_entry


def paragraph(text: str, y: float, y2: float, *, label: str | None = None):
    return SimpleNamespace(
        unicode=text,
        layout_label=label,
        box=SimpleNamespace(x=40.0, y=y, x2=520.0, y2=y2),
    )


def document(*pages):
    return SimpleNamespace(
        page=[SimpleNamespace(pdf_paragraph=list(items)) for items in pages]
    )


class ReferenceStructureRulesTest(unittest.TestCase):
    def test_preserves_reference_heading_and_entries_but_not_prior_conclusion(self):
        conclusion = paragraph("9 Conclusion", 720, 740)
        heading = paragraph("REFERENCES", 680, 700, label="title")
        heading.box.x2 = 160.0
        first_left = paragraph("[1] D. Di Paola, A. Milella", 640, 660)
        first_right = paragraph("[26] J. A. Cetto and A. Sanfeliu", 720, 740)
        first_right.box.x = 300.0
        first_right.box.x2 = 540.0
        doc = document(
            [conclusion, first_right, heading, first_left],
            [paragraph("[27] J. Crespo, R. Barber", 690, 710)],
        )

        stats = mark_document_structure(doc)

        self.assertIsNone(conclusion.layout_label)
        self.assertEqual(heading.layout_label, REFERENCE_LABEL)
        self.assertEqual(first_left.layout_label, REFERENCE_LABEL)
        self.assertEqual(first_right.layout_label, REFERENCE_LABEL)
        self.assertEqual(doc.page[1].pdf_paragraph[0].layout_label, REFERENCE_LABEL)
        self.assertEqual(stats.reference_paragraphs, 4)

    def test_resumes_translation_at_explicit_appendix_heading(self):
        appendix = paragraph("Appendix A", 700, 720, label="title")
        proof = paragraph("Proof details", 660, 680)
        doc = document(
            [
                paragraph("Bibliography", 700, 720, label="title"),
                paragraph("[1] A. Author, Paper", 660, 680),
            ],
            [appendix, proof],
        )

        mark_document_structure(doc)

        self.assertEqual(appendix.layout_label, "title")
        self.assertIsNone(proof.layout_label)


class TocStructureRulesTest(unittest.TestCase):
    def test_parses_spaced_dot_leader(self):
        entry = parse_toc_entry(
            "9.3.2  Interactive Long Video Generation . . . . . . 244"
        )

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.prefix, "9.3.2")
        self.assertEqual(entry.title, "Interactive Long Video Generation")
        self.assertEqual(entry.page_number, "244")

    def test_parses_page_number_after_invisible_format_character(self):
        entry = parse_toc_entry(
            "1. Roles of Stakeholders . . . . . . . . \u20605"
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.title, "Roles of Stakeholders")
        self.assertEqual(entry.page_number, "5")

    def test_rebuilds_translated_title_with_page_number_at_target_width(self):
        entry = parse_toc_entry(
            "9.3.2  Interactive Long Video Generation .......... 244"
        )
        assert entry is not None

        rebuilt = rebuild_toc_entry(entry, "交互式长视频生成", target_columns=64)

        self.assertTrue(rebuilt.startswith("9.3.2  交互式长视频生成"))
        self.assertTrue(rebuilt.endswith("244"))
        self.assertIn("...", rebuilt)
        self.assertGreaterEqual(display_width(rebuilt), 62)
        self.assertLessEqual(display_width(rebuilt), 64)

    def test_marks_only_entries_on_confirmed_contents_page(self):
        heading = paragraph("Contents", 730, 750, label="title")
        entry1 = paragraph("1 Introduction .......... 5", 700, 720)
        entry2 = paragraph("1.1 Background . . . . . 6", 670, 690)
        footer = paragraph("3", 20, 30)
        doc = document([heading, entry1, entry2, footer])

        stats = mark_document_structure(doc)

        self.assertEqual(entry1.layout_label, TOC_LABEL)
        self.assertEqual(entry2.layout_label, TOC_LABEL)
        self.assertNotEqual(footer.layout_label, TOC_LABEL)
        self.assertEqual(stats.toc_entries, 2)

    def test_splits_multiple_toc_lines_merged_into_one_paragraph(self):
        def composition(text, y):
            line = SimpleNamespace(
                pdf_character=[SimpleNamespace(char_unicode=char) for char in text],
                box=SimpleNamespace(x=58.0, y=y, x2=538.0, y2=y + 12.0),
            )
            return SimpleNamespace(pdf_line=line)

        heading = paragraph("Contents", 780, 800, label="title")
        first = "1.1. Stakeholders . . . . . . . . . . . 5"
        second = "1.2. Interaction between Student and Supervisor . . . . . 6"
        merged = paragraph(f"{first} {second}", 730, 770)
        merged.pdf_paragraph_composition = [
            composition(first, 750),
            composition(second, 735),
        ]
        third = paragraph("2. Programmes and Courses . . . . . . 8", 710, 725)
        doc = document([heading, merged, third])

        stats = mark_document_structure(doc)

        entries = [
            item
            for item in doc.page[0].pdf_paragraph
            if item.layout_label == TOC_LABEL
        ]
        self.assertEqual(stats.toc_entries, 3)
        self.assertEqual([item.unicode for item in entries], [first, second, third.unicode])
        self.assertEqual(entries[0].box.y, 750)
        self.assertEqual(entries[1].box.y, 735)


if __name__ == "__main__":
    unittest.main()
