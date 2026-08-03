from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal
from typing import Mapping


DocumentTextMode = Literal["born_digital", "scanned", "hybrid"]
ParagraphSourceType = Literal["native_text", "table_ocr", "image_ocr"]


@dataclass(frozen=True, slots=True)
class ParagraphOriginRecord:
    stable_id: str
    source_type: ParagraphSourceType
    part_index: int
    page_number: int
    paragraph_index: int | None
    table_index: int | None
    block_index: int | None
    bbox: tuple[float, float, float, float] | None
    normalized_text_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "source_type": self.source_type,
            "part_index": self.part_index,
            "page_number": self.page_number,
            "paragraph_index": self.paragraph_index,
            "table_index": self.table_index,
            "block_index": self.block_index,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "normalized_text_hash": self.normalized_text_hash,
        }


def _normalized_text(text: str) -> str:
    return "".join(re.findall(r"\w+", (text or "").casefold()))


def _text_hash(text: str) -> str:
    digest = hashlib.sha256(_normalized_text(text).encode("utf-8")).hexdigest()
    return f"sha256-{digest[:16]}"


class ParagraphOriginRegistry:
    def __init__(self) -> None:
        self.origin_by_stable_id: dict[str, ParagraphOriginRecord] = {}
        self.stable_id_by_object_id: dict[int, str] = {}
        self._object_by_id: dict[int, object] = {}

    def register_native(
        self,
        paragraph: object,
        *,
        part_index: int,
        page_number: int,
        paragraph_index: int,
        bbox: tuple[float, float, float, float] | None,
        text: str,
    ) -> ParagraphOriginRecord:
        stable_id = (
            f"part-{part_index:03d}/page-{page_number:03d}/"
            f"paragraph-{paragraph_index:03d}"
        )
        return self._register(
            paragraph,
            ParagraphOriginRecord(
                stable_id=stable_id,
                source_type="native_text",
                part_index=part_index,
                page_number=page_number,
                paragraph_index=paragraph_index,
                table_index=None,
                block_index=None,
                bbox=bbox,
                normalized_text_hash=_text_hash(text),
            ),
        )

    def register_table_ocr(
        self,
        paragraph: object,
        *,
        part_index: int,
        page_number: int,
        table_index: int,
        block_index: int,
        bbox: tuple[float, float, float, float] | None,
        text: str,
    ) -> ParagraphOriginRecord:
        stable_id = (
            f"part-{part_index:03d}/page-{page_number:03d}/"
            f"table-{table_index:03d}/block-{block_index:03d}"
        )
        return self._register(
            paragraph,
            ParagraphOriginRecord(
                stable_id=stable_id,
                source_type="table_ocr",
                part_index=part_index,
                page_number=page_number,
                paragraph_index=None,
                table_index=table_index,
                block_index=block_index,
                bbox=bbox,
                normalized_text_hash=_text_hash(text),
            ),
        )

    def register_image_ocr(
        self,
        paragraph: object,
        *,
        part_index: int,
        page_number: int,
        block_index: int,
        bbox: tuple[float, float, float, float] | None,
        text: str,
    ) -> ParagraphOriginRecord:
        stable_id = (
            f"part-{part_index:03d}/page-{page_number:03d}/"
            f"image-ocr-{block_index:03d}"
        )
        return self._register(
            paragraph,
            ParagraphOriginRecord(
                stable_id=stable_id,
                source_type="image_ocr",
                part_index=part_index,
                page_number=page_number,
                paragraph_index=None,
                table_index=None,
                block_index=block_index,
                bbox=bbox,
                normalized_text_hash=_text_hash(text),
            ),
        )

    def _register(
        self,
        paragraph: object,
        record: ParagraphOriginRecord,
    ) -> ParagraphOriginRecord:
        existing = self.origin_by_stable_id.get(record.stable_id)
        if existing is not None and existing != record:
            raise ValueError(f"stable paragraph id collision: {record.stable_id}")
        object_id = id(paragraph)
        self.origin_by_stable_id[record.stable_id] = record
        self.stable_id_by_object_id[object_id] = record.stable_id
        self._object_by_id[object_id] = paragraph
        return record

    def stable_id(self, paragraph: object) -> str | None:
        object_id = id(paragraph)
        if self._object_by_id.get(object_id) is not paragraph:
            return None
        return self.stable_id_by_object_id.get(object_id)

    def clear_part(self, part_index: int) -> None:
        stable_ids = {
            stable_id
            for stable_id, record in self.origin_by_stable_id.items()
            if record.part_index == part_index
        }
        for stable_id in stable_ids:
            self.origin_by_stable_id.pop(stable_id, None)
        for object_id, stable_id in list(self.stable_id_by_object_id.items()):
            if stable_id not in stable_ids:
                continue
            self.stable_id_by_object_id.pop(object_id, None)
            self._object_by_id.pop(object_id, None)


@dataclass(frozen=True, slots=True)
class PageTextStats:
    page_number: int
    native_paragraphs: int
    ocr_paragraphs: int


def classify_document_text_mode(
    page_stats: tuple[PageTextStats, ...] | list[PageTextStats],
) -> DocumentTextMode:
    if not page_stats:
        return "born_digital"
    native_pages = sum(page.native_paragraphs > 0 for page in page_stats)
    native_ratio = native_pages / len(page_stats)
    if native_ratio >= 0.8:
        return "born_digital"
    if native_ratio <= 0.2:
        return "scanned"
    return "hybrid"


@dataclass(frozen=True, slots=True)
class OcrRegion:
    text: str
    bbox: tuple[float, float, float, float]


def _bbox_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    first_area = max(0.0, first[2] - first[0]) * max(
        0.0, first[3] - first[1]
    )
    second_area = max(0.0, second[2] - second[0]) * max(
        0.0, second[3] - second[1]
    )
    denominator = min(first_area, second_area)
    if denominator <= 0:
        return 0.0
    return intersection / denominator


def is_duplicate_text_region(
    native: OcrRegion,
    ocr: OcrRegion,
    *,
    bbox_overlap_threshold: float = 0.5,
    text_similarity_threshold: float = 0.75,
) -> bool:
    overlap = _bbox_overlap(native.bbox, ocr.bbox)
    similarity = SequenceMatcher(
        None,
        _normalized_text(native.text),
        _normalized_text(ocr.text),
    ).ratio()
    return (
        overlap >= bbox_overlap_threshold
        and similarity >= text_similarity_threshold
    )


@dataclass(frozen=True, slots=True)
class OcrSafetyThresholds:
    max_table_ocr_paragraphs: int = 200
    max_table_ocr_to_native_ratio: float = 0.5
    max_ocr_blocks_per_table: int = 100
    ratio_check_min_native_paragraphs: int = 20


@dataclass(frozen=True, slots=True)
class OcrSafetySnapshot:
    page_stats: tuple[PageTextStats, ...]
    native_paragraphs: int
    table_ocr_paragraphs: int
    image_ocr_paragraphs: int
    ocr_blocks_per_table: Mapping[str, int]
    duplicate_pairs: tuple[tuple[str, str], ...]
    document_mode: DocumentTextMode | None = None


@dataclass(frozen=True, slots=True)
class OcrSafetyReport:
    safe: bool
    document_mode: DocumentTextMode
    reasons: tuple[str, ...]
    ratio_failure_pages: tuple[int, ...]
    native_paragraphs: int
    table_ocr_paragraphs: int
    image_ocr_paragraphs: int
    duplicate_count: int


def evaluate_ocr_safety(
    snapshot: OcrSafetySnapshot,
    thresholds: OcrSafetyThresholds | None = None,
) -> OcrSafetyReport:
    limits = thresholds or OcrSafetyThresholds()
    mode = snapshot.document_mode or classify_document_text_mode(
        snapshot.page_stats
    )
    reasons: list[str] = []
    ratio_failure_pages: list[int] = []
    if snapshot.table_ocr_paragraphs > limits.max_table_ocr_paragraphs:
        reasons.append("max_table_ocr_paragraphs")
    if any(
        count > limits.max_ocr_blocks_per_table
        for count in snapshot.ocr_blocks_per_table.values()
    ):
        reasons.append("max_ocr_blocks_per_table")
    if snapshot.duplicate_pairs:
        reasons.append("duplicate_ocr_injection")

    if mode == "born_digital":
        if snapshot.native_paragraphs >= limits.ratio_check_min_native_paragraphs:
            ratio = snapshot.table_ocr_paragraphs / snapshot.native_paragraphs
            if ratio > limits.max_table_ocr_to_native_ratio:
                reasons.append("ocr_native_ratio")
    elif mode == "hybrid":
        for page in snapshot.page_stats:
            if page.native_paragraphs < limits.ratio_check_min_native_paragraphs:
                continue
            if (
                page.ocr_paragraphs / page.native_paragraphs
                > limits.max_table_ocr_to_native_ratio
            ):
                ratio_failure_pages.append(page.page_number)
        if ratio_failure_pages:
            reasons.append("ocr_native_ratio")

    return OcrSafetyReport(
        safe=not reasons,
        document_mode=mode,
        reasons=tuple(dict.fromkeys(reasons)),
        ratio_failure_pages=tuple(ratio_failure_pages),
        native_paragraphs=snapshot.native_paragraphs,
        table_ocr_paragraphs=snapshot.table_ocr_paragraphs,
        image_ocr_paragraphs=snapshot.image_ocr_paragraphs,
        duplicate_count=len(snapshot.duplicate_pairs),
    )
