from __future__ import annotations

import logging
import re
import shutil
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from local_babeldoc_server.ocr_safety import OcrRegion
from local_babeldoc_server.ocr_safety import OcrSafetySnapshot
from local_babeldoc_server.ocr_safety import OcrSafetyThresholds
from local_babeldoc_server.ocr_safety import PageTextStats
from local_babeldoc_server.ocr_safety import ParagraphOriginRegistry
from local_babeldoc_server.ocr_safety import evaluate_ocr_safety
from local_babeldoc_server.ocr_safety import is_duplicate_text_region
from local_babeldoc_server.structure_rules import REFERENCE_LABEL
from local_babeldoc_server.structure_rules import TOC_LABEL
from local_babeldoc_server.structure_rules import display_width
from local_babeldoc_server.structure_rules import mark_document_structure
from local_babeldoc_server.structure_rules import parse_toc_entry
from local_babeldoc_server.structure_rules import rebuild_toc_entry
from local_babeldoc_server.translation_quality import validate_translation
from local_babeldoc_server.translation_quality import split_translation_chunks
from local_babeldoc_server.translation_batching import partition_batch_indices


logger = logging.getLogger(__name__)

SUPPORTED_BABELDOC_VERSION = "0.6.4"
PATCH_MARKER = "__local_immersive_translate_compat__"
TABLE_OCR_LABEL = "babeldoc_table_ocr"
OCR_BACKGROUND_MARKER_LINE_WIDTH = -1.0
_STYLE_MARKUP_RE = re.compile(r"</?style\b[^>]*>", re.IGNORECASE)
_INLINE_PLACEHOLDER_RE = re.compile(r"\{v\d+\}", re.IGNORECASE)
_URL_LITERAL_RE = re.compile(
    r"^(?:(?:https?://)|(?:www\.))\S+$",
    re.IGNORECASE,
)
_PARENTHETICAL_CITATION_RE = re.compile(
    r"^\([^()]{1,160},\s*(?:18|19|20)\d{2}[a-z]?\)[†‡*]?$",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


@dataclass(slots=True)
class CompatibilityHandle:
    originals: list[tuple[Any, str, Any]] = field(default_factory=list)
    already_installed: bool = False

    def restore(self) -> None:
        for owner, name, original in reversed(self.originals):
            setattr(owner, name, original)
        self.originals.clear()


@dataclass(frozen=True, slots=True)
class TableStats:
    table_regions: int = 0
    ocr_table_regions: int = 0
    ocr_text_blocks: int = 0


class OcrSafetyError(RuntimeError):
    pass


class TableLayoutSafetyError(RuntimeError):
    pass


def _is_nontranslatable_literal(text: str) -> bool:
    normalized = _INLINE_PLACEHOLDER_RE.sub("", text).strip()
    return bool(
        _URL_LITERAL_RE.fullmatch(normalized)
        or _PARENTHETICAL_CITATION_RE.fullmatch(normalized)
    )


_TABLE_OCR_DEBUG_ID_RE = re.compile(
    r"^local-table-ocr-(?P<page>\d+)-(?P<table>\d+)-(?P<block>\d+)$"
)
_SPLIT_PART_FILENAME_RE = re.compile(r"\.part(?P<part>\d+)\.pdf$", re.IGNORECASE)


def _part_index(translation_config: Any) -> int:
    input_file = str(getattr(translation_config, "input_file", "") or "")
    match = _SPLIT_PART_FILENAME_RE.search(input_file)
    if match:
        return int(match.group("part"))
    return int(getattr(translation_config, "local_part_index", 0) or 0)


def _paragraph_bbox(paragraph: Any) -> tuple[float, float, float, float] | None:
    box = getattr(paragraph, "box", None)
    if box is None:
        return None
    try:
        return (float(box.x), float(box.y), float(box.x2), float(box.y2))
    except (AttributeError, TypeError, ValueError):
        return None


def register_document_origins_and_validate(
    document: Any,
    translation_config: Any,
):
    registry = getattr(translation_config, "local_origin_registry", None)
    if registry is None:
        registry = ParagraphOriginRegistry()
        translation_config.local_origin_registry = registry
    part_index = _part_index(translation_config)
    native_count = 0
    table_ocr_count = 0
    image_ocr_count = 0
    page_stats = []
    blocks_per_table: dict[str, int] = {}
    duplicate_pairs: list[tuple[str, str]] = []

    for page in getattr(document, "page", []):
        page_number = int(getattr(page, "page_number", 0) or 0)
        page_native: list[tuple[str, OcrRegion]] = []
        page_ocr: list[tuple[str, OcrRegion]] = []
        page_native_count = 0
        page_ocr_count = 0
        for paragraph_index, paragraph in enumerate(
            getattr(page, "pdf_paragraph", [])
        ):
            text = str(getattr(paragraph, "unicode", "") or "").strip()
            if not text:
                continue
            bbox = _paragraph_bbox(paragraph)
            label = getattr(paragraph, "layout_label", None)
            if label == TABLE_OCR_LABEL:
                match = _TABLE_OCR_DEBUG_ID_RE.match(
                    str(getattr(paragraph, "debug_id", "") or "")
                )
                table_index = int(match.group("table")) if match else 0
                block_index = (
                    int(match.group("block")) if match else page_ocr_count
                )
                record = registry.register_table_ocr(
                    paragraph,
                    part_index=part_index,
                    page_number=page_number,
                    table_index=table_index,
                    block_index=block_index,
                    bbox=bbox,
                    text=text,
                )
                table_key = (
                    f"part-{part_index:03d}/page-{page_number:03d}/"
                    f"table-{table_index:03d}"
                )
                blocks_per_table[table_key] = blocks_per_table.get(table_key, 0) + 1
                table_ocr_count += 1
                page_ocr_count += 1
            elif "ocr" in str(label or "").casefold():
                record = registry.register_image_ocr(
                    paragraph,
                    part_index=part_index,
                    page_number=page_number,
                    block_index=page_ocr_count,
                    bbox=bbox,
                    text=text,
                )
                image_ocr_count += 1
                page_ocr_count += 1
            else:
                record = registry.register_native(
                    paragraph,
                    part_index=part_index,
                    page_number=page_number,
                    paragraph_index=paragraph_index,
                    bbox=bbox,
                    text=text,
                )
                native_count += 1
                page_native_count += 1
            if bbox is None:
                continue
            region = OcrRegion(text=text, bbox=bbox)
            if record.source_type == "native_text":
                page_native.append((record.stable_id, region))
            else:
                page_ocr.append((record.stable_id, region))

        for native_id, native_region in page_native:
            for ocr_id, ocr_region in page_ocr:
                if is_duplicate_text_region(native_region, ocr_region):
                    duplicate_pairs.append((native_id, ocr_id))
        page_stats.append(
            PageTextStats(
                page_number=page_number,
                native_paragraphs=page_native_count,
                ocr_paragraphs=page_ocr_count,
            )
        )

    snapshot = OcrSafetySnapshot(
        page_stats=tuple(page_stats),
        native_paragraphs=native_count,
        table_ocr_paragraphs=table_ocr_count,
        image_ocr_paragraphs=image_ocr_count,
        ocr_blocks_per_table=blocks_per_table,
        duplicate_pairs=tuple(duplicate_pairs),
    )
    thresholds = OcrSafetyThresholds(
        max_table_ocr_paragraphs=int(
            getattr(translation_config, "max_table_ocr_paragraphs", 200)
        ),
        max_table_ocr_to_native_ratio=float(
            getattr(
                translation_config,
                "max_table_ocr_to_native_ratio",
                0.5,
            )
        ),
        max_ocr_blocks_per_table=int(
            getattr(translation_config, "max_ocr_blocks_per_table", 100)
        ),
        ratio_check_min_native_paragraphs=int(
            getattr(
                translation_config,
                "ratio_check_min_native_paragraphs",
                20,
            )
        ),
    )
    report = evaluate_ocr_safety(snapshot, thresholds)
    translation_config.local_ocr_safety_snapshot = snapshot
    translation_config.local_ocr_safety_report = report
    shared_snapshots = getattr(
        translation_config,
        "local_ocr_safety_snapshots",
        None,
    )
    if shared_snapshots is not None:
        shared_snapshots[part_index] = snapshot
    if not report.safe:
        runtime = getattr(
            translation_config,
            "local_translation_runtime",
            None,
        )
        if runtime is not None:
            runtime.abort("ocr_anomaly")
        raise OcrSafetyError(
            "ocr_anomaly: OCR safety check failed: "
            + ", ".join(report.reasons)
        )
    return report


def _translation_quality_state(translation_config: Any) -> tuple[dict[str, Any], Any]:
    state = getattr(translation_config, "local_translation_quality", None)
    lock = getattr(translation_config, "local_translation_quality_lock", None)
    if state is None:
        state = {
            "rejected_count": 0,
            "recovered_count": 0,
            "unresolved_count": 0,
            "recovered": [],
            "unresolved": [],
        }
        setattr(translation_config, "local_translation_quality", state)
    if lock is None:
        lock = threading.Lock()
        setattr(translation_config, "local_translation_quality_lock", lock)
    return state, lock


def _record_quality_event(
    translation_config: Any,
    kind: str,
    paragraph: Any,
    source_text: str,
    reasons: tuple[str, ...] = (),
    *,
    source_preserved: bool = False,
) -> None:
    state, lock = _translation_quality_state(translation_config)
    record = {
        "paragraph_id": getattr(paragraph, "debug_id", None),
        "source_preview": source_text[:240],
        "reasons": list(reasons),
        "source_preserved": bool(source_preserved),
    }
    with lock:
        if kind == "rejected":
            state["rejected_count"] += 1
            return
        state[f"{kind}_count"] += 1
        state[kind].append(record)


def _require_supported_babeldoc() -> None:
    babeldoc = import_module("babeldoc")
    version = getattr(babeldoc, "__version__", None)
    if version != SUPPORTED_BABELDOC_VERSION:
        raise RuntimeError(
            "PDF structure compatibility requires BabelDOC "
            f"{SUPPORTED_BABELDOC_VERSION}; found {version or 'unknown'}"
        )


def _call_tracker(tracker: Any, method: str, value: Any) -> None:
    callback = getattr(tracker, method, None)
    if callable(callback):
        callback(value)


def _replace_paragraph_unicode(paragraph: Any, text: str) -> None:
    il_version = import_module("babeldoc.format.pdf.document_il.il_version_1")
    style = getattr(paragraph, "pdf_style", None)
    if style is None:
        raise RuntimeError("TOC paragraph has no resolved PDF style")
    same_style = il_version.PdfSameStyleUnicodeCharacters(
        unicode=text,
        pdf_style=style,
        debug_info=False,
    )
    paragraph.pdf_paragraph_composition = [
        il_version.PdfParagraphComposition(
            pdf_same_style_unicode_characters=same_style,
        )
    ]
    paragraph.unicode = text


def _rgb_graphic_state(il_version: Any, rgb: tuple[int, int, int]):
    red, green, blue = (channel / 255.0 for channel in rgb)
    return il_version.GraphicState(
        passthrough_per_char_instruction=(
            f"{red:.10f} {green:.10f} {blue:.10f} rg "
            f"{red:.10f} {green:.10f} {blue:.10f} RG "
        )
    )


def _box_overlap_ratio(inner: Any, outer: Any) -> float:
    if inner is None or outer is None:
        return 0.0
    width = max(0.0, float(inner.x2) - float(inner.x))
    height = max(0.0, float(inner.y2) - float(inner.y))
    area = width * height
    if area == 0:
        return 0.0
    intersection_width = max(
        0.0,
        min(float(inner.x2), float(outer.x2))
        - max(float(inner.x), float(outer.x)),
    )
    intersection_height = max(
        0.0,
        min(float(inner.y2), float(outer.y2))
        - max(float(inner.y), float(outer.y)),
    )
    return intersection_width * intersection_height / area


def _find_native_table_paragraph_object_ids(document: Any) -> set[int]:
    object_ids: set[int] = set()
    for page in getattr(document, "page", []):
        table_boxes = [
            getattr(layout, "box", None)
            for layout in getattr(page, "page_layout", [])
            if getattr(layout, "class_name", None) == "table"
            and getattr(layout, "box", None) is not None
        ]
        if not table_boxes:
            continue
        for paragraph in getattr(page, "pdf_paragraph", []):
            paragraph_box = getattr(paragraph, "box", None)
            if any(
                _box_overlap_ratio(paragraph_box, table_box) >= 0.5
                for table_box in table_boxes
            ):
                object_ids.add(id(paragraph))
    return object_ids


def _is_native_table_paragraph(
    translation_config: Any,
    paragraph: Any,
) -> bool:
    object_ids = getattr(
        translation_config,
        "local_native_table_paragraph_object_ids",
        (),
    )
    return id(paragraph) in object_ids


def _rebuild_native_table_paragraph(il_version: Any, paragraph: Any) -> None:
    source_text = getattr(paragraph, "unicode", "") or ""
    style = getattr(paragraph, "pdf_style", None)
    same_style = il_version.PdfSameStyleUnicodeCharacters(
        unicode=source_text,
        pdf_style=style,
        debug_info=False,
    )
    paragraph.pdf_paragraph_composition = [
        il_version.PdfParagraphComposition(
            pdf_same_style_unicode_characters=same_style,
        )
    ]


def _is_translatable_short_table_text(text: str, min_length: int) -> bool:
    normalized = _INLINE_PLACEHOLDER_RE.sub("", text).strip()
    if not normalized or len(normalized) >= min_length:
        return False
    if _is_nontranslatable_literal(normalized):
        return False
    return bool(
        re.fullmatch(r"[A-Za-z][A-Za-z .-]*", normalized)
        and re.search(r"[a-z]", normalized)
    )


def _remove_reference_backgrounds(page: Any, references: list[Any]) -> None:
    reference_boxes = [
        getattr(paragraph, "box", None)
        for paragraph in references
        if getattr(paragraph, "box", None) is not None
    ]
    page.pdf_rectangle = [
        rectangle
        for rectangle in getattr(page, "pdf_rectangle", [])
        if not (
            getattr(rectangle, "fill_background", False)
            and any(
                _box_overlap_ratio(reference_box, getattr(rectangle, "box", None))
                >= 0.5
                for reference_box in reference_boxes
            )
        )
    ]


def _make_ocr_paragraph(il_version: Any, block: Any, debug_id: str):
    text_state = il_version.GraphicState(
        passthrough_per_char_instruction="0 g 0 G"
    )
    font_size = max(4.0, min(12.0, (block.pdf_box.y2 - block.pdf_box.y) * 0.72))
    style = il_version.PdfStyle(
        font_id="base",
        font_size=font_size,
        graphic_state=text_state,
    )
    same_style = il_version.PdfSameStyleUnicodeCharacters(
        unicode=block.text,
        pdf_style=style,
        debug_info=False,
    )
    return il_version.PdfParagraph(
        box=il_version.Box(
            block.pdf_box.x,
            block.pdf_box.y,
            block.pdf_box.x2,
            block.pdf_box.y2,
        ),
        pdf_style=style,
        pdf_paragraph_composition=[
            il_version.PdfParagraphComposition(
                pdf_same_style_unicode_characters=same_style,
            )
        ],
        xobj_id=-1,
        unicode=block.text,
        vertical=False,
        first_line_indent=False,
        debug_id=debug_id,
        layout_label=TABLE_OCR_LABEL,
        layout_id=None,
        render_order=1_000_000,
    )


def _make_ocr_background(il_version: Any, block: Any):
    return il_version.PdfRectangle(
        box=il_version.Box(
            block.pdf_box.x,
            block.pdf_box.y,
            block.pdf_box.x2,
            block.pdf_box.y2,
        ),
        graphic_state=_rgb_graphic_state(il_version, block.background_rgb),
        debug_info=False,
        fill_background=True,
        xobj_id=-1,
        line_width=OCR_BACKGROUND_MARKER_LINE_WIDTH,
        render_order=999_999,
    )


def select_table_ocr_regions(
    document: Any,
    runtime: Any,
) -> frozenset[tuple[int, int]]:
    eligible = set()
    for page in getattr(document, "page", []):
        table_layouts = [
            layout
            for layout in getattr(page, "page_layout", [])
            if getattr(layout, "class_name", None) == "table"
        ]
        for table_index, layout in enumerate(table_layouts):
            if runtime.needs_ocr(
                layout.box,
                getattr(page, "pdf_character", []),
            ):
                eligible.add((page.page_number, table_index))
    return frozenset(eligible)


def inject_ocr_table_paragraphs(
    document: Any,
    mupdf_document: Any,
    runtime: Any,
    eligible_regions: frozenset[tuple[int, int]] | None = None,
) -> TableStats:
    il_version = import_module("babeldoc.format.pdf.document_il.il_version_1")
    table_regions = 0
    ocr_table_regions = 0
    ocr_text_blocks = 0

    for page in getattr(document, "page", []):
        table_layouts = [
            layout
            for layout in getattr(page, "page_layout", [])
            if getattr(layout, "class_name", None) == "table"
        ]
        table_regions += len(table_layouts)
        for table_index, layout in enumerate(table_layouts):
            region_key = (page.page_number, table_index)
            if eligible_regions is not None:
                needs_ocr = region_key in eligible_regions
            else:
                needs_ocr = runtime.needs_ocr(
                    layout.box,
                    getattr(page, "pdf_character", []),
                )
            if not needs_ocr:
                continue
            blocks = runtime.extract(mupdf_document[page.page_number], layout.box)
            if not blocks:
                continue
            ocr_table_regions += 1
            for block_index, block in enumerate(blocks):
                debug_id = (
                    f"local-table-ocr-{page.page_number}-{table_index}-{block_index}"
                )
                page.pdf_paragraph.append(
                    _make_ocr_paragraph(il_version, block, debug_id)
                )
                page.pdf_rectangle.append(_make_ocr_background(il_version, block))
                ocr_text_blocks += 1

    return TableStats(
        table_regions=table_regions,
        ocr_table_regions=ocr_table_regions,
        ocr_text_blocks=ocr_text_blocks,
    )


def translate_toc_paragraph(
    translator: Any,
    paragraph: Any,
    tracker: Any = None,
) -> bool:
    if getattr(paragraph, "layout_label", None) != TOC_LABEL:
        return False
    entry = parse_toc_entry(getattr(paragraph, "unicode", "") or "")
    if entry is None:
        return False

    source_text = getattr(paragraph, "unicode", "") or ""
    _call_tracker(tracker, "set_pdf_unicode", source_text)
    _call_tracker(tracker, "set_input", entry.title)
    with _paragraph_translation_context(translator, paragraph, "fallback"):
        translated_title = translator.translate_engine.translate(
            entry.title,
            rate_limit_params={"paragraph_token_count": len(entry.title)},
        )
    target_columns = max(20, display_width(source_text))
    rebuilt = rebuild_toc_entry(entry, translated_title, target_columns)
    _replace_paragraph_unicode(paragraph, rebuilt)
    _call_tracker(tracker, "set_output", rebuilt)
    return True


def _paragraph_translation_context(
    translator: Any,
    paragraph: Any,
    request_category: str,
):
    engine = getattr(translator, "translate_engine", None)
    context_factory = getattr(engine, "paragraph_context", None)
    if not callable(context_factory):
        return nullcontext()
    registry = getattr(
        getattr(translator, "translation_config", None),
        "local_origin_registry",
        None,
    )
    stable_id = registry.stable_id(paragraph) if registry is not None else None
    if stable_id is None:
        raise OcrSafetyError(
            "paragraph has no stable provenance before translation"
        )
    return context_factory(stable_id, request_category)


def install_babeldoc_compat(
    config: dict[str, Any],
    ocr_runtime: Any = None,
) -> CompatibilityHandle:
    _require_supported_babeldoc()
    paragraph_module = import_module(
        "babeldoc.format.pdf.document_il.midend.paragraph_finder"
    )
    translator_module = import_module(
        "babeldoc.format.pdf.document_il.midend.il_translator"
    )
    translation_config_module = import_module(
        "babeldoc.format.pdf.translation_config"
    )
    typesetting_module = import_module(
        "babeldoc.format.pdf.document_il.midend.typesetting"
    )
    paragraph_finder = paragraph_module.ParagraphFinder
    il_translator = translator_module.ILTranslator
    il_version = import_module("babeldoc.format.pdf.document_il.il_version_1")
    translation_config_class = translation_config_module.TranslationConfig
    typesetting_class = typesetting_module.Typesetting
    llm_only_translator = None
    try:
        llm_only_module = import_module(
            "babeldoc.format.pdf.document_il.midend.il_translator_llm_only"
        )
        llm_only_translator = llm_only_module.ILTranslatorLLMOnly
    except (ImportError, AttributeError):
        llm_only_translator = None
    table_ocr_enabled = bool(config.get("enable_table_ocr", True) and ocr_runtime)

    pdf_creater_module = import_module(
        "babeldoc.format.pdf.document_il.backend.pdf_creater"
    )
    pdf_creater = pdf_creater_module.PDFCreater

    layout_parser = None
    rectangle_render_unit = None
    styles_and_formulas = None
    if table_ocr_enabled:
        layout_module = import_module(
            "babeldoc.format.pdf.document_il.midend.layout_parser"
        )
        layout_parser = layout_module.LayoutParser
        rectangle_render_unit = pdf_creater_module.RectangleRenderUnit
        styles_module = import_module(
            "babeldoc.format.pdf.document_il.midend.styles_and_formulas"
        )
        styles_and_formulas = styles_module.StylesAndFormulas

    if getattr(paragraph_finder.process, PATCH_MARKER, False):
        return CompatibilityHandle(already_installed=True)

    handle = CompatibilityHandle()
    original_process = paragraph_finder.process
    original_pre_translate = il_translator.pre_translate_paragraph
    original_translate = il_translator.translate_paragraph
    original_post_translate = il_translator.post_translate_paragraph
    original_subset_fonts = pdf_creater.subset_fonts_in_subprocess
    original_subset_fonts_descriptor = pdf_creater.__dict__[
        "subset_fonts_in_subprocess"
    ]
    original_cleanup_part_working_dir = (
        translation_config_class.cleanup_part_working_dir
    )
    original_typesetting_document = typesetting_class.typesetting_document
    handle.originals.extend(
        [
            (paragraph_finder, "process", original_process),
            (il_translator, "pre_translate_paragraph", original_pre_translate),
            (il_translator, "translate_paragraph", original_translate),
            (il_translator, "post_translate_paragraph", original_post_translate),
            (
                pdf_creater,
                "subset_fonts_in_subprocess",
                original_subset_fonts_descriptor,
            ),
            (
                translation_config_class,
                "cleanup_part_working_dir",
                original_cleanup_part_working_dir,
            ),
            (
                typesetting_class,
                "typesetting_document",
                original_typesetting_document,
            ),
        ]
    )
    original_batch_translate = None
    original_process_page = None
    if llm_only_translator is not None:
        original_batch_translate = llm_only_translator.translate_paragraph
        original_process_page = llm_only_translator.process_page
        handle.originals.append(
            (llm_only_translator, "translate_paragraph", original_batch_translate)
        )
        handle.originals.append(
            (llm_only_translator, "process_page", original_process_page)
        )
    original_layout_process = None
    original_create_render_units = None
    original_styles_process = None
    if table_ocr_enabled:
        original_layout_process = layout_parser.process
        original_create_render_units = pdf_creater.create_render_units_for_page
        original_styles_process = styles_and_formulas.process
        handle.originals.extend(
            [
                (layout_parser, "process", original_layout_process),
                (
                    pdf_creater,
                    "create_render_units_for_page",
                    original_create_render_units,
                ),
                (styles_and_formulas, "process", original_styles_process),
            ]
        )

    preserve_references = bool(config.get("preserve_references", True))
    preserve_toc_layout = bool(config.get("preserve_toc_layout", True))
    enable_native_table_translation = bool(
        config.get("enable_native_table_translation", True)
    )
    quality_guard_enabled = bool(
        config.get("enable_translation_quality_guard", True)
    )
    min_table_translation_scale = float(
        config.get("min_table_translation_scale", 0.55)
    )
    table_bbox_tolerance = float(config.get("table_bbox_tolerance", 0.5))

    def wrapped_process(self, document):
        result = original_process(self, document)
        native_table_paragraph_object_ids: set[int] = set()
        if enable_native_table_translation:
            native_table_paragraph_object_ids = (
                _find_native_table_paragraph_object_ids(document)
            )
        self.translation_config.local_native_table_paragraph_object_ids = (
            native_table_paragraph_object_ids
        )
        stats = mark_document_structure(document)
        self.translation_config.local_structure_stats = stats
        logger.info(
            "PDF structure detection: references=%s toc_entries=%s "
            "native_table_paragraphs=%s",
            stats.reference_paragraphs,
            stats.toc_entries,
            len(native_table_paragraph_object_ids),
        )
        return result

    def wrapped_pre_translate(
        self,
        paragraph,
        tracker,
        page_font_map,
        xobj_font_map,
    ):
        def record_batch_id(result):
            if not result or result[0] is None:
                return result
            collector = getattr(
                getattr(self, "translate_engine", None),
                "collect_batch_stable_id",
                None,
            )
            if callable(collector):
                registry = getattr(
                    self.translation_config,
                    "local_origin_registry",
                    None,
                )
                stable_id = (
                    registry.stable_id(paragraph)
                    if registry is not None
                    else None
                )
                if stable_id is None:
                    raise OcrSafetyError(
                        "paragraph has no stable provenance before translation"
                    )
                collector(stable_id)
            return result

        source_text = (getattr(paragraph, "unicode", "") or "").strip()
        is_native_table_text = bool(
            enable_native_table_translation
            and _is_native_table_paragraph(
                self.translation_config,
                paragraph,
            )
        )
        if _is_nontranslatable_literal(source_text):
            _call_tracker(tracker, "set_pdf_unicode", source_text)
            if is_native_table_text:
                _rebuild_native_table_paragraph(il_version, paragraph)
            return None, None
        if (
            preserve_references
            and getattr(paragraph, "layout_label", None) == REFERENCE_LABEL
        ):
            _call_tracker(
                tracker,
                "set_pdf_unicode",
                getattr(paragraph, "unicode", "") or "",
            )
            return record_batch_id((None, None))
        if getattr(paragraph, "layout_label", None) == TABLE_OCR_LABEL:
            if not source_text:
                return None, None
            _call_tracker(tracker, "set_pdf_unicode", source_text)
            _call_tracker(tracker, "set_input", source_text)
            translate_input = self.TranslateInput(
                source_text,
                [],
                paragraph.pdf_style,
            )
            setter = getattr(
                translate_input,
                "set_original_placeholder_tokens",
                None,
            )
            if callable(setter):
                setter({})
            return record_batch_id((source_text, translate_input))

        min_text_length = int(
            getattr(self.translation_config, "min_text_length", 5)
        )
        if is_native_table_text and _is_translatable_short_table_text(
            source_text,
            min_text_length,
        ):
            effective_font_map = page_font_map
            xobj_id = getattr(paragraph, "xobj_id", None)
            if xobj_id in xobj_font_map:
                effective_font_map = xobj_font_map[xobj_id]
            disable_rich_text_translate = bool(
                getattr(
                    self.translation_config,
                    "disable_rich_text_translate",
                    False,
                )
            )
            if not getattr(self, "support_llm_translate", False):
                disable_rich_text_translate = True
            translate_input = self.get_translate_input(
                paragraph,
                effective_font_map,
                disable_rich_text_translate,
            )
            if translate_input is not None:
                _call_tracker(tracker, "set_pdf_unicode", source_text)
                _call_tracker(tracker, "set_input", translate_input.unicode)
                _call_tracker(
                    tracker,
                    "set_placeholders",
                    translate_input.placeholders,
                )
                _call_tracker(
                    tracker,
                    "set_original_placeholders",
                    getattr(
                        translate_input,
                        "original_placeholder_tokens",
                        None,
                    ),
                )
                return record_batch_id((translate_input.unicode, translate_input))

        result = original_pre_translate(
            self,
            paragraph,
            tracker,
            page_font_map,
            xobj_font_map,
        )
        if is_native_table_text and (not result or result[0] is None):
            _rebuild_native_table_paragraph(il_version, paragraph)
        return record_batch_id(result)

    def wrapped_batch_translate(self, *args, **kwargs):
        context_factory = getattr(
            getattr(self, "translate_engine", None),
            "batch_context",
            None,
        )
        if not callable(context_factory):
            return original_batch_translate(self, *args, **kwargs)
        with context_factory():
            return original_batch_translate(self, *args, **kwargs)

    def wrapped_process_page(
        self,
        page,
        executor,
        pbar=None,
        tracker=None,
        executor2=None,
        translated_ids=None,
    ):
        self.translation_config.raise_if_cancelled()
        if translated_ids is None:
            translated_ids = set()
        page_font_map, page_xobj_font_map = self._build_font_maps(page)
        globals_ = original_process_page.__globals__
        is_cid = globals_["is_cid_paragraph"]
        is_numeric = globals_["is_pure_numeric_paragraph"]
        is_placeholder = globals_["is_placeholder_only_paragraph"]
        batch_class = globals_["BatchParagraph"]
        paragraphs = []
        token_counts = []
        for paragraph in page.pdf_paragraph:
            if id(paragraph) in translated_ids:
                continue
            if paragraph.debug_id is None or paragraph.unicode is None:
                continue
            is_native_table_text = bool(
                enable_native_table_translation
                and _is_native_table_paragraph(
                    self.translation_config,
                    paragraph,
                )
            )
            source_text = paragraph.unicode
            min_text_length = int(
                getattr(self.translation_config, "min_text_length", 5)
            )
            is_short = len(source_text) < min_text_length
            translate_short = bool(
                is_native_table_text
                and _is_translatable_short_table_text(
                    source_text,
                    min_text_length,
                )
            )
            cid_paragraph = is_cid(paragraph)
            numeric_paragraph = is_numeric(paragraph)
            placeholder_paragraph = is_placeholder(paragraph)
            nontranslatable_literal = _is_nontranslatable_literal(source_text)
            if (
                is_native_table_text
                and not cid_paragraph
                and not placeholder_paragraph
                and (
                    nontranslatable_literal
                    or numeric_paragraph
                    or (is_short and not translate_short)
                )
            ):
                _rebuild_native_table_paragraph(il_version, paragraph)
            if (
                cid_paragraph
                or (is_short and not translate_short)
                or numeric_paragraph
                or placeholder_paragraph
                or nontranslatable_literal
            ):
                if pbar:
                    pbar.advance(1)
                continue
            token_count = self.calc_token_count(paragraph.unicode)
            paragraphs.append(paragraph)
            token_counts.append(token_count)
            translated_ids.add(id(paragraph))
            if paragraph.layout_label == "title":
                self.shared_context_cross_split_part.recent_title_paragraph = (
                    self.shared_context_cross_split_part.snapshot_title_paragraph(
                        paragraph
                    )
                )

        for start, end in partition_batch_indices(
            token_counts,
            target_tokens=int(
                getattr(
                    self.translation_config,
                    "batch_target_source_tokens",
                    2400,
                )
            ),
            max_tokens=int(
                getattr(
                    self.translation_config,
                    "batch_max_source_tokens",
                    3200,
                )
            ),
            max_paragraphs=int(
                getattr(self.translation_config, "batch_max_paragraphs", 40)
            ),
        ):
            batch = paragraphs[start:end]
            total_tokens = sum(token_counts[start:end])
            self.mid += 1
            executor.submit(
                self.translate_paragraph,
                batch_class(batch, [page] * len(batch), tracker),
                pbar,
                page_font_map,
                page_xobj_font_map,
                self.translation_config.shared_context_cross_split_part.first_paragraph,
                self.translation_config.shared_context_cross_split_part.recent_title_paragraph,
                executor2,
                priority=1048576 - total_tokens,
                paragraph_token_count=total_tokens,
                mp_id=self.mid,
            )

    def wrapped_translate(self, paragraph, *args, **kwargs):
        if preserve_toc_layout and getattr(paragraph, "layout_label", None) == TOC_LABEL:
            tracker = kwargs.get("tracker")
            if tracker is None and len(args) >= 3:
                tracker = args[2]
            if translate_toc_paragraph(self, paragraph, tracker):
                return None
        with _paragraph_translation_context(self, paragraph, "fallback"):
            result = original_translate(self, paragraph, *args, **kwargs)
        tracker = kwargs.get("tracker")
        if tracker is None and len(args) >= 3:
            tracker = args[2]
        rejection = getattr(tracker, "local_translation_rejection", None)
        if not rejection:
            return result

        source_text = getattr(tracker, "local_translation_source", "") or ""
        translate_input = getattr(tracker, "local_translate_input", None)
        if not source_text or translate_input is None:
            _call_tracker(tracker, "set_output", source_text)
            _record_quality_event(
                self.translation_config,
                "unresolved",
                paragraph,
                source_text,
                tuple(rejection),
            )
            return result

        title_paragraph = kwargs.get("title_paragraph")
        local_title_paragraph = kwargs.get("local_title_paragraph")
        if title_paragraph is None and len(args) >= 7:
            title_paragraph = args[6]
        if local_title_paragraph is None and len(args) >= 8:
            local_title_paragraph = args[7]

        retry_sizes = getattr(
            self.translation_config,
            "translation_retry_chunk_sizes",
            config.get("translation_retry_chunk_sizes", [700, 350]),
        )
        if getattr(self.translation_config, "local_translation_runtime", None):
            retry_sizes = ()
        last_reasons = tuple(rejection)
        for chunk_size in retry_sizes:
            chunks = split_translation_chunks(source_text, int(chunk_size))
            translated_chunks = []
            chunk_failed = False
            use_plain_style = False
            for chunk in chunks:
                prompt = self.generate_prompt_for_llm(
                    chunk,
                    title_paragraph,
                    local_title_paragraph,
                    None,
                )
                llm_tracker = None
                tracker_factory = getattr(tracker, "new_llm_translate_tracker", None)
                if callable(tracker_factory):
                    llm_tracker = tracker_factory()
                    _call_tracker(llm_tracker, "set_input", prompt)
                translated_chunk = self.translate_engine.llm_translate(
                    prompt,
                    ignore_cache=True,
                    rate_limit_params={"paragraph_token_count": len(chunk)},
                )
                translated_chunk = translated_chunk or ""
                if llm_tracker is not None:
                    _call_tracker(llm_tracker, "set_output", translated_chunk)
                validation = validate_translation(
                    chunk,
                    translated_chunk,
                    getattr(self.translation_config, "lang_out", "zh"),
                )
                if not validation.accepted:
                    translated_chunk = self.translate_engine.translate(
                        chunk,
                        ignore_cache=True,
                        rate_limit_params={"paragraph_token_count": len(chunk)},
                    )
                    translated_chunk = translated_chunk or ""
                    if llm_tracker is not None:
                        _call_tracker(llm_tracker, "set_output", translated_chunk)
                    validation = validate_translation(
                        chunk,
                        translated_chunk,
                        getattr(self.translation_config, "lang_out", "zh"),
                    )
                    if (
                        not validation.accepted
                        and validation.reasons == ("protected_token_mismatch",)
                        and _STYLE_MARKUP_RE.search(chunk)
                    ):
                        plain_validation = validate_translation(
                            _STYLE_MARKUP_RE.sub("", chunk),
                            _STYLE_MARKUP_RE.sub("", translated_chunk),
                            getattr(self.translation_config, "lang_out", "zh"),
                        )
                        if plain_validation.accepted:
                            validation = plain_validation
                            use_plain_style = True
                    if not validation.accepted:
                        last_reasons = validation.reasons
                        if llm_tracker is not None:
                            _call_tracker(
                                llm_tracker,
                                "set_error_message",
                                ", ".join(validation.reasons),
                            )
                        chunk_failed = True
                        break
                translated_chunks.append(translated_chunk.strip())
            if chunk_failed or not translated_chunks:
                continue

            combined = " ".join(translated_chunks)
            combined_source = source_text
            combined_translate_input = translate_input
            if use_plain_style:
                combined = _STYLE_MARKUP_RE.sub("", combined)
                combined_source = _STYLE_MARKUP_RE.sub("", source_text)
                formula_placeholders = [
                    placeholder
                    for placeholder in getattr(translate_input, "placeholders", [])
                    if hasattr(placeholder, "placeholder")
                    and not hasattr(placeholder, "left_placeholder")
                ]
                combined_translate_input = self.TranslateInput(
                    combined_source,
                    formula_placeholders,
                    getattr(translate_input, "base_style", None),
                )
                setter = getattr(
                    combined_translate_input,
                    "set_original_placeholder_tokens",
                    None,
                )
                if callable(setter):
                    setter({})
            combined_validation = validate_translation(
                combined_source,
                combined,
                getattr(self.translation_config, "lang_out", "zh"),
            )
            if not combined_validation.accepted:
                last_reasons = combined_validation.reasons
                continue

            setattr(tracker, "local_translation_rejection", ())
            applied = original_post_translate(
                self,
                paragraph,
                tracker,
                combined_translate_input,
                combined,
            )
            _record_quality_event(
                self.translation_config,
                "recovered",
                paragraph,
                source_text,
            )
            return applied

        _call_tracker(tracker, "set_output", source_text)
        _record_quality_event(
            self.translation_config,
            "unresolved",
            paragraph,
            source_text,
            last_reasons,
        )
        return result

    def wrapped_post_translate(
        self,
        paragraph,
        tracker,
        translate_input,
        translated_text,
    ):
        if not quality_guard_enabled:
            return original_post_translate(
                self,
                paragraph,
                tracker,
                translate_input,
                translated_text,
            )
        source_text = getattr(translate_input, "unicode", "") or ""
        translate_engine = getattr(self, "translate_engine", None)
        outcome_getter = getattr(
            translate_engine,
            "batch_item_outcome",
            None,
        )
        registry = getattr(
            getattr(self, "translation_config", None),
            "local_origin_registry",
            None,
        )
        stable_id = (
            registry.stable_id(paragraph)
            if registry is not None
            else None
        )
        if (
            stable_id is not None
            and callable(outcome_getter)
            and outcome_getter(stable_id) == "source_preserved"
        ):
            _call_tracker(tracker, "set_output", source_text)
            _record_quality_event(
                self.translation_config,
                "unresolved",
                paragraph,
                source_text,
                ("source_preserved_after_two_attempts",),
                source_preserved=True,
            )
            return False
        target_language = getattr(
            getattr(self, "translation_config", None),
            "lang_out",
            "zh",
        )
        validation = validate_translation(
            source_text,
            translated_text,
            target_language,
        )
        if not validation.accepted:
            batch_context_active = getattr(
                translate_engine,
                "is_batch_context_active",
                lambda: False,
            )
            if batch_context_active():
                with _paragraph_translation_context(
                    self,
                    paragraph,
                    "fallback",
                ):
                    fallback_text = translate_engine.translate(
                        source_text,
                        ignore_cache=True,
                        rate_limit_params={
                            "paragraph_token_count": len(source_text),
                        },
                    )
                fallback_validation = validate_translation(
                    source_text,
                    fallback_text,
                    target_language,
                )
                if fallback_validation.accepted:
                    _record_quality_event(
                        self.translation_config,
                        "recovered",
                        paragraph,
                        source_text,
                    )
                    return original_post_translate(
                        self,
                        paragraph,
                        tracker,
                        translate_input,
                        fallback_text,
                    )
                validation = fallback_validation
            setattr(tracker, "local_translation_rejection", validation.reasons)
            setattr(tracker, "local_translation_source", source_text)
            setattr(tracker, "local_translate_input", translate_input)
            _record_quality_event(
                self.translation_config,
                "rejected",
                paragraph,
                source_text,
                validation.reasons,
            )
            return False
        return original_post_translate(
            self,
            paragraph,
            tracker,
            translate_input,
            translated_text,
        )

    def wrapped_subset_fonts(pdf, translation_config, tag):
        if str(tag).startswith("merged"):
            logger.info(
                "Skipping redundant font subsetting for split-result merge: %s",
                tag,
            )
            return pdf
        return original_subset_fonts(pdf, translation_config, tag)

    def wrapped_cleanup_part_working_dir(self, part_index):
        part_dir = getattr(self, "_part_working_dirs", {}).get(part_index)
        working_dir = getattr(self, "working_dir", None)
        if part_dir is not None and working_dir:
            part_dir = Path(part_dir)
            audit_dir = (
                Path(working_dir)
                / "translation_tracking_parts"
                / f"part_{int(part_index):03d}"
            )
            for tracking_path in part_dir.rglob("translate_tracking.json"):
                relative_path = tracking_path.relative_to(part_dir)
                destination = audit_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(tracking_path, destination)
        return original_cleanup_part_working_dir(self, part_index)

    def wrapped_typesetting_document(self, document):
        original_boxes: dict[int, tuple[float, float, float, float]] = {}
        table_locations: dict[int, int] = {}
        for page in getattr(document, "page", []):
            page_number = int(getattr(page, "page_number", 0) or 0) + 1
            for paragraph in getattr(page, "pdf_paragraph", []):
                label = str(getattr(paragraph, "layout_label", "") or "")
                if label not in {"table", TABLE_OCR_LABEL}:
                    continue
                bbox = _paragraph_bbox(paragraph)
                if bbox is not None:
                    original_boxes[id(paragraph)] = bbox
                    table_locations[id(paragraph)] = page_number

        result = original_typesetting_document(self, document)

        for page in getattr(document, "page", []):
            for paragraph in getattr(page, "pdf_paragraph", []):
                paragraph_id = id(paragraph)
                original_bbox = original_boxes.get(paragraph_id)
                if original_bbox is None:
                    continue
                page_number = table_locations[paragraph_id]
                scale = float(getattr(paragraph, "scale", 1.0) or 0.0)
                if scale < min_table_translation_scale:
                    raise TableLayoutSafetyError(
                        "table_layout_unsafe: "
                        f"page={page_number} scale={scale:.3f} "
                        f"minimum={min_table_translation_scale:.3f}"
                    )
                current_bbox = _paragraph_bbox(paragraph)
                if current_bbox is None:
                    raise TableLayoutSafetyError(
                        "table_layout_unsafe: "
                        f"page={page_number} bbox_missing"
                    )
                ox, oy, ox2, oy2 = original_bbox
                x, y, x2, y2 = current_bbox
                expanded = (
                    x < ox - table_bbox_tolerance
                    or y < oy - table_bbox_tolerance
                    or x2 > ox2 + table_bbox_tolerance
                    or y2 > oy2 + table_bbox_tolerance
                )
                changed = any(
                    abs(before - after) > table_bbox_tolerance
                    for before, after in zip(original_bbox, current_bbox)
                )
                if expanded or changed:
                    reason = "bbox_expanded" if expanded else "bbox_changed"
                    raise TableLayoutSafetyError(
                        "table_layout_unsafe: "
                        f"page={page_number} {reason} "
                        f"before={original_bbox} after={current_bbox}"
                    )
        return result

    if table_ocr_enabled:

        def wrapped_layout_process(self, document, mupdf_document):
            result = original_layout_process(self, document, mupdf_document)
            self.translation_config.local_table_ocr_context = (
                result,
                mupdf_document,
                select_table_ocr_regions(result, ocr_runtime),
            )
            return result

        def wrapped_create_render_units(self, page, translation_config):
            units = original_create_render_units(self, page, translation_config)
            rendered_rectangles = {
                id(getattr(unit, "rectangle", None)) for unit in units
            }
            for index, rectangle in enumerate(page.pdf_rectangle):
                if (
                    getattr(rectangle, "line_width", None)
                    != OCR_BACKGROUND_MARKER_LINE_WIDTH
                    or id(rectangle) in rendered_rectangles
                ):
                    continue
                units.append(
                    rectangle_render_unit(
                        rectangle,
                        getattr(rectangle, "render_order", 999_999),
                        getattr(rectangle, "sub_render_order", index),
                        0.0,
                    )
                )
            return units

        def wrapped_styles_process(self, document):
            preserved_page_paragraphs = []
            bypass_reference_styles = bool(
                preserve_references
                and getattr(self.translation_config, "ocr_workaround", False)
            )
            if bypass_reference_styles:
                for page in getattr(document, "page", []):
                    original_paragraphs = list(
                        getattr(page, "pdf_paragraph", [])
                    )
                    references = [
                        paragraph
                        for paragraph in original_paragraphs
                        if getattr(paragraph, "layout_label", None)
                        == REFERENCE_LABEL
                    ]
                    preserved_page_paragraphs.append(
                        (page, original_paragraphs, references)
                    )
                    page.pdf_paragraph = [
                        paragraph
                        for paragraph in original_paragraphs
                        if getattr(paragraph, "layout_label", None)
                        != REFERENCE_LABEL
                    ]
            try:
                result = original_styles_process(self, document)
            finally:
                for (
                    page,
                    original_paragraphs,
                    references,
                ) in preserved_page_paragraphs:
                    page.pdf_paragraph = original_paragraphs
                    _remove_reference_backgrounds(page, references)
            context = getattr(
                self.translation_config,
                "local_table_ocr_context",
                None,
            )
            if context and context[0] is document:
                table_stats = inject_ocr_table_paragraphs(
                    document,
                    context[1],
                    ocr_runtime,
                    context[2],
                )
                self.translation_config.local_table_stats = table_stats
                logger.info(
                    "PDF table detection: regions=%s OCR regions=%s OCR blocks=%s",
                    table_stats.table_regions,
                    table_stats.ocr_table_regions,
                    table_stats.ocr_text_blocks,
                )
            ocr_report = register_document_origins_and_validate(
                document,
                self.translation_config,
            )
            logger.info(
                "PDF text provenance: mode=%s native=%s table_ocr=%s "
                "image_ocr=%s duplicates=%s",
                ocr_report.document_mode,
                ocr_report.native_paragraphs,
                ocr_report.table_ocr_paragraphs,
                ocr_report.image_ocr_paragraphs,
                ocr_report.duplicate_count,
            )
            return result

        setattr(wrapped_layout_process, PATCH_MARKER, True)
        setattr(wrapped_create_render_units, PATCH_MARKER, True)
        setattr(wrapped_styles_process, PATCH_MARKER, True)
        layout_parser.process = wrapped_layout_process
        pdf_creater.create_render_units_for_page = wrapped_create_render_units
        styles_and_formulas.process = wrapped_styles_process

    setattr(wrapped_process, PATCH_MARKER, True)
    setattr(wrapped_pre_translate, PATCH_MARKER, True)
    setattr(wrapped_translate, PATCH_MARKER, True)
    setattr(wrapped_post_translate, PATCH_MARKER, True)
    setattr(wrapped_cleanup_part_working_dir, PATCH_MARKER, True)
    setattr(wrapped_typesetting_document, PATCH_MARKER, True)
    paragraph_finder.process = wrapped_process
    il_translator.pre_translate_paragraph = wrapped_pre_translate
    il_translator.translate_paragraph = wrapped_translate
    il_translator.post_translate_paragraph = wrapped_post_translate
    translation_config_class.cleanup_part_working_dir = (
        wrapped_cleanup_part_working_dir
    )
    typesetting_class.typesetting_document = wrapped_typesetting_document
    if llm_only_translator is not None:
        original_calc_token_count = llm_only_translator.calc_token_count

        def wrapped_calc_token_count(self, text):
            result = original_calc_token_count(self, text)
            if len(text) <= 30 and _CJK_RE.search(text):
                return max(1, len(text) // 3)
            return result

        setattr(wrapped_batch_translate, PATCH_MARKER, True)
        setattr(wrapped_process_page, PATCH_MARKER, True)
        setattr(wrapped_calc_token_count, PATCH_MARKER, True)
        llm_only_translator.translate_paragraph = wrapped_batch_translate
        llm_only_translator.process_page = wrapped_process_page
        llm_only_translator.calc_token_count = wrapped_calc_token_count
        handle.originals.append(
            (llm_only_translator, "calc_token_count", original_calc_token_count)
        )
    pdf_creater.subset_fonts_in_subprocess = staticmethod(wrapped_subset_fonts)
    return handle
