import unittest
from types import SimpleNamespace

from local_babeldoc_server.table_ocr import PdfBox
from local_babeldoc_server.table_ocr import TableOcrRuntime
from local_babeldoc_server.table_ocr import image_box_to_pdf_box


def native_character(x, y, x2, y2, text="A"):
    return SimpleNamespace(
        char_unicode=text,
        visual_bbox=SimpleNamespace(box=SimpleNamespace(x=x, y=y, x2=x2, y2=y2)),
    )


class FakePixmap:
    width = 400
    height = 200
    n = 3
    alpha = 0
    samples = bytes([245, 245, 245]) * width * height


class FakePage:
    rect = SimpleNamespace(height=700.0)

    def get_pixmap(self, **kwargs):
        self.kwargs = kwargs
        return FakePixmap()


class FakeOcrResult:
    def __init__(self):
        self.boxes = [
            [[20, 10], [220, 10], [220, 50], [20, 50]],
            [[20, 70], [180, 70], [180, 100], [20, 100]],
        ]
        self.txts = ["Functions", "unreliable"]
        self.scores = [0.91, 0.31]


class FakeEngine:
    def __init__(self):
        self.images = []

    def __call__(self, image):
        self.images.append(image)
        return FakeOcrResult()


class TableOcrRuntimeTest(unittest.TestCase):
    def test_ocr_runs_only_when_table_has_insufficient_native_text(self):
        runtime = TableOcrRuntime(engine_factory=FakeEngine, min_native_characters=12)
        table = PdfBox(20, 100, 500, 400)
        enough_text = [
            native_character(40 + index * 10, 200, 48 + index * 10, 210)
            for index in range(12)
        ]

        self.assertTrue(runtime.needs_ocr(table, []))
        self.assertFalse(runtime.needs_ocr(table, enough_text))

    def test_maps_crop_pixels_to_bottom_left_pdf_coordinates(self):
        box = image_box_to_pdf_box(
            image_box=(20, 10, 220, 50),
            crop_pdf_box=PdfBox(50, 100, 350, 250),
            scale=2.0,
        )

        self.assertEqual(box, PdfBox(60, 225, 160, 245))

    def test_extract_discards_low_confidence_blocks_and_maps_coordinates(self):
        engine = FakeEngine()
        runtime = TableOcrRuntime(
            engine_factory=lambda: engine,
            scale=2.0,
            min_confidence=0.55,
            geometry_factory=lambda scale, clip: (scale, clip),
        )
        table = PdfBox(50, 100, 350, 250)

        blocks = runtime.extract(FakePage(), table)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "Functions")
        self.assertAlmostEqual(blocks[0].confidence, 0.91)
        self.assertEqual(blocks[0].pdf_box, PdfBox(60, 225, 160, 245))
        self.assertEqual(blocks[0].background_rgb, (245, 245, 245))
        self.assertEqual(len(engine.images), 1)


if __name__ == "__main__":
    unittest.main()
