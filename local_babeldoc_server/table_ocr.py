from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any
from typing import Callable
from typing import Iterable

import numpy as np


@dataclass(frozen=True, slots=True)
class PdfBox:
    x: float
    y: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class OcrTextBlock:
    text: str
    confidence: float
    pdf_box: PdfBox
    background_rgb: tuple[int, int, int]


def create_rapidocr_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "Table OCR is enabled but RapidOCR is not installed. "
            "Run the local backend installer again."
        ) from exc
    return RapidOCR()


def _coerce_box(box: Any) -> PdfBox:
    return PdfBox(float(box.x), float(box.y), float(box.x2), float(box.y2))


def _overlap_ratio(inner: PdfBox, outer: PdfBox) -> float:
    intersection_width = max(0.0, min(inner.x2, outer.x2) - max(inner.x, outer.x))
    intersection_height = max(0.0, min(inner.y2, outer.y2) - max(inner.y, outer.y))
    area = max(0.0, inner.x2 - inner.x) * max(0.0, inner.y2 - inner.y)
    if area == 0:
        return 0.0
    return intersection_width * intersection_height / area


def _polygon_bounds(polygon: Any) -> tuple[float, float, float, float]:
    points = np.asarray(polygon, dtype=float).reshape(-1, 2)
    return (
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    )


def image_box_to_pdf_box(
    image_box: tuple[float, float, float, float],
    crop_pdf_box: PdfBox,
    scale: float,
) -> PdfBox:
    x, y, x2, y2 = image_box
    return PdfBox(
        crop_pdf_box.x + x / scale,
        crop_pdf_box.y2 - y2 / scale,
        crop_pdf_box.x + x2 / scale,
        crop_pdf_box.y2 - y / scale,
    )


def _pixmap_to_array(pixmap: Any) -> np.ndarray:
    channels = int(getattr(pixmap, "n", 3))
    array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        int(pixmap.height),
        int(pixmap.width),
        channels,
    )
    if channels == 1:
        return np.repeat(array, 3, axis=2)
    return array[:, :, :3]


def _sample_background(
    image: np.ndarray,
    image_box: tuple[float, float, float, float],
) -> tuple[int, int, int]:
    x, y, x2, y2 = image_box
    height, width = image.shape[:2]
    x0 = max(0, min(width - 1, int(x)))
    x1 = max(x0 + 1, min(width, int(x2) + 1))
    y0 = max(0, min(height - 1, int(y)))
    y1 = max(y0 + 1, min(height, int(y2) + 1))
    region = image[y0:y1, x0:x1, :3]
    if region.size == 0:
        return (255, 255, 255)
    upper_quartile = np.percentile(region.reshape(-1, 3), 75, axis=0)
    return tuple(int(round(value)) for value in upper_quartile)


def _result_rows(result: Any) -> list[tuple[Any, str, float]]:
    if hasattr(result, "boxes") and hasattr(result, "txts"):
        return list(zip(result.boxes, result.txts, result.scores, strict=False))
    if isinstance(result, tuple) and result:
        result = result[0]
    rows = []
    for row in result or []:
        if len(row) >= 3:
            rows.append((row[0], str(row[1]), float(row[2])))
    return rows


class TableOcrRuntime:
    def __init__(
        self,
        engine_factory: Callable[[], Any],
        *,
        scale: float = 2.0,
        min_confidence: float = 0.55,
        min_native_characters: int = 12,
        geometry_factory: Callable[
            [float, tuple[float, float, float, float]], tuple[Any, Any]
        ]
        | None = None,
    ):
        self.engine_factory = engine_factory
        self.scale = float(scale)
        self.min_confidence = float(min_confidence)
        self.min_native_characters = int(min_native_characters)
        self.geometry_factory = geometry_factory
        self._engine_instance = None
        self._engine_lock = threading.Lock()

    def _engine(self):
        with self._engine_lock:
            if self._engine_instance is None:
                self._engine_instance = self.engine_factory()
            return self._engine_instance

    def needs_ocr(self, table_box: PdfBox | Any, characters: Iterable[Any]) -> bool:
        table = _coerce_box(table_box)
        printable = 0
        for character in characters:
            text = getattr(character, "char_unicode", "") or ""
            visual_bbox = getattr(character, "visual_bbox", None)
            character_box = getattr(visual_bbox, "box", None)
            if not text.strip() or character_box is None:
                continue
            if _overlap_ratio(_coerce_box(character_box), table) >= 0.5:
                printable += 1
                if printable >= self.min_native_characters:
                    return False
        return True

    def extract(self, page: Any, table_box: PdfBox | Any) -> list[OcrTextBlock]:
        table = _coerce_box(table_box)
        page_height = float(page.rect.height)
        clip_coordinates = (
            table.x,
            page_height - table.y2,
            table.x2,
            page_height - table.y,
        )
        if self.geometry_factory is None:
            import pymupdf

            matrix = pymupdf.Matrix(self.scale, self.scale)
            clip = pymupdf.Rect(*clip_coordinates)
        else:
            matrix, clip = self.geometry_factory(self.scale, clip_coordinates)
        pixmap = page.get_pixmap(
            matrix=matrix,
            clip=clip,
            alpha=False,
        )
        image = _pixmap_to_array(pixmap)
        result = self._engine()(image)
        blocks = []
        for polygon, text, score in _result_rows(result):
            confidence = float(score)
            normalized_text = str(text or "").strip()
            if confidence < self.min_confidence or not normalized_text:
                continue
            image_box = _polygon_bounds(polygon)
            blocks.append(
                OcrTextBlock(
                    text=normalized_text,
                    confidence=confidence,
                    pdf_box=image_box_to_pdf_box(image_box, table, self.scale),
                    background_rgb=_sample_background(image, image_box),
                )
            )
        return blocks
