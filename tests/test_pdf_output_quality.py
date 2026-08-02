import importlib.util
import tempfile
import unittest
from pathlib import Path

from local_babeldoc_server.pdf_output_quality import PdfOutputIntegrityError
from local_babeldoc_server.pdf_output_quality import audit_side_by_side_source
from local_babeldoc_server.pdf_output_quality import ensure_side_by_side_source_preserved


PYMUPDF_AVAILABLE = importlib.util.find_spec("pymupdf") is not None


@unittest.skipUnless(PYMUPDF_AVAILABLE, "PyMuPDF is installed with BabelDOC")
class PdfOutputQualityTest(unittest.TestCase):
    def _write_source(self, path: Path, page_count: int = 2) -> None:
        import pymupdf

        document = pymupdf.open()
        for page_number in range(page_count):
            page = document.new_page(width=300, height=400)
            page.insert_text(
                (30, 50),
                f"Source page {page_number + 1}",
                fontsize=18,
            )
            page.draw_rect(
                pymupdf.Rect(25, 80, 275, 320),
                color=(0, 0, 0),
                width=2,
            )
        document.save(path)

    def _write_dual(
        self,
        source_path: Path,
        dual_path: Path,
        *,
        mode: str,
        corrupt_page: int | None = None,
        page_count: int | None = None,
    ) -> None:
        import pymupdf

        source = pymupdf.open(source_path)
        dual = pymupdf.open()
        count = len(source) if page_count is None else page_count
        for page_number in range(count):
            page = dual.new_page(width=600, height=400)
            source_rect = (
                pymupdf.Rect(0, 0, 300, 400)
                if mode == "lort"
                else pymupdf.Rect(300, 0, 600, 400)
            )
            target_rect = (
                pymupdf.Rect(300, 0, 600, 400)
                if mode == "lort"
                else pymupdf.Rect(0, 0, 300, 400)
            )
            if page_number != corrupt_page and page_number < len(source):
                page.show_pdf_page(source_rect, source, page_number)
            elif page_number < len(source):
                page.insert_text(
                    source_rect.top_left + (30, 50),
                    "Scrambled replacement",
                    fontsize=18,
                )
            page.insert_text(
                target_rect.top_left + (30, 50),
                f"Translated page {page_number + 1}",
                fontsize=18,
            )
        dual.save(dual_path)

    def test_faithful_source_half_passes_in_both_side_by_side_modes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.pdf"
            self._write_source(source_path)

            for mode in ("lort", "ltro"):
                with self.subTest(mode=mode):
                    dual_path = root / f"{mode}.pdf"
                    self._write_dual(source_path, dual_path, mode=mode)
                    audit = audit_side_by_side_source(
                        source_path,
                        dual_path,
                        mode,
                    )

                    self.assertTrue(audit.passed)
                    self.assertEqual(audit.page_count, 2)
                    self.assertEqual(audit.failed_pages, ())
                    ensure_side_by_side_source_preserved(audit)

    def test_corrupted_source_half_is_rejected_with_page_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.pdf"
            dual_path = root / "corrupted.pdf"
            self._write_source(source_path)
            self._write_dual(
                source_path,
                dual_path,
                mode="lort",
                corrupt_page=1,
            )

            audit = audit_side_by_side_source(
                source_path,
                dual_path,
                "lort",
            )

            self.assertFalse(audit.passed)
            self.assertEqual(audit.failed_pages, (2,))
            self.assertGreater(audit.pages[1].changed_pixel_fraction, 0.02)
            with self.assertRaisesRegex(PdfOutputIntegrityError, "page 2"):
                ensure_side_by_side_source_preserved(audit)

    def test_page_count_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.pdf"
            dual_path = root / "short.pdf"
            self._write_source(source_path, page_count=2)
            self._write_dual(
                source_path,
                dual_path,
                mode="lort",
                page_count=1,
            )

            audit = audit_side_by_side_source(
                source_path,
                dual_path,
                "lort",
            )

            self.assertFalse(audit.passed)
            self.assertEqual(audit.failure_reason, "page_count_mismatch")
            with self.assertRaisesRegex(PdfOutputIntegrityError, "page count"):
                ensure_side_by_side_source_preserved(audit)


if __name__ == "__main__":
    unittest.main()
