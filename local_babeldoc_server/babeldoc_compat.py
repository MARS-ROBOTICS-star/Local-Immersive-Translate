from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from local_babeldoc_server.structure_rules import REFERENCE_LABEL
from local_babeldoc_server.structure_rules import TOC_LABEL
from local_babeldoc_server.structure_rules import display_width
from local_babeldoc_server.structure_rules import mark_document_structure
from local_babeldoc_server.structure_rules import parse_toc_entry
from local_babeldoc_server.structure_rules import rebuild_toc_entry


logger = logging.getLogger(__name__)

SUPPORTED_BABELDOC_VERSION = "0.6.4"
PATCH_MARKER = "__local_immersive_translate_compat__"
TABLE_OCR_LABEL = "babeldoc_table_ocr"
OCR_BACKGROUND_MARKER_LINE_WIDTH = -1.0


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


def inject_ocr_table_paragraphs(
    document: Any,
    mupdf_document: Any,
    runtime: Any,
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
            if not runtime.needs_ocr(layout.box, getattr(page, "pdf_character", [])):
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
    translated_title = translator.translate_engine.translate(
        entry.title,
        rate_limit_params={"paragraph_token_count": len(entry.title)},
    )
    target_columns = max(20, display_width(source_text))
    rebuilt = rebuild_toc_entry(entry, translated_title, target_columns)
    _replace_paragraph_unicode(paragraph, rebuilt)
    _call_tracker(tracker, "set_output", rebuilt)
    return True


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
    paragraph_finder = paragraph_module.ParagraphFinder
    il_translator = translator_module.ILTranslator
    table_ocr_enabled = bool(config.get("enable_table_ocr", True) and ocr_runtime)

    layout_parser = None
    pdf_creater = None
    rectangle_render_unit = None
    styles_and_formulas = None
    if table_ocr_enabled:
        layout_module = import_module(
            "babeldoc.format.pdf.document_il.midend.layout_parser"
        )
        pdf_creater_module = import_module(
            "babeldoc.format.pdf.document_il.backend.pdf_creater"
        )
        layout_parser = layout_module.LayoutParser
        pdf_creater = pdf_creater_module.PDFCreater
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
    handle.originals.extend(
        [
            (paragraph_finder, "process", original_process),
            (il_translator, "pre_translate_paragraph", original_pre_translate),
            (il_translator, "translate_paragraph", original_translate),
        ]
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

    def wrapped_process(self, document):
        result = original_process(self, document)
        stats = mark_document_structure(document)
        self.translation_config.local_structure_stats = stats
        logger.info(
            "PDF structure detection: references=%s toc_entries=%s",
            stats.reference_paragraphs,
            stats.toc_entries,
        )
        return result

    def wrapped_pre_translate(
        self,
        paragraph,
        tracker,
        page_font_map,
        xobj_font_map,
    ):
        if preserve_references and getattr(paragraph, "layout_label", None) == REFERENCE_LABEL:
            _call_tracker(
                tracker,
                "set_pdf_unicode",
                getattr(paragraph, "unicode", "") or "",
            )
            return None, None
        if getattr(paragraph, "layout_label", None) == TABLE_OCR_LABEL:
            source_text = (getattr(paragraph, "unicode", "") or "").strip()
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
            return source_text, translate_input
        return original_pre_translate(
            self,
            paragraph,
            tracker,
            page_font_map,
            xobj_font_map,
        )

    def wrapped_translate(self, paragraph, *args, **kwargs):
        if preserve_toc_layout and getattr(paragraph, "layout_label", None) == TOC_LABEL:
            tracker = kwargs.get("tracker")
            if tracker is None and len(args) >= 3:
                tracker = args[2]
            if translate_toc_paragraph(self, paragraph, tracker):
                return None
        return original_translate(self, paragraph, *args, **kwargs)

    if table_ocr_enabled:

        def wrapped_layout_process(self, document, mupdf_document):
            result = original_layout_process(self, document, mupdf_document)
            self.translation_config.local_table_ocr_context = (
                result,
                mupdf_document,
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
            result = original_styles_process(self, document)
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
                )
                self.translation_config.local_table_stats = table_stats
                logger.info(
                    "PDF table detection: regions=%s OCR regions=%s OCR blocks=%s",
                    table_stats.table_regions,
                    table_stats.ocr_table_regions,
                    table_stats.ocr_text_blocks,
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
    paragraph_finder.process = wrapped_process
    il_translator.pre_translate_paragraph = wrapped_pre_translate
    il_translator.translate_paragraph = wrapped_translate
    return handle
