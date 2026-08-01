import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from local_babeldoc_server.babeldoc_compat import install_babeldoc_compat
from local_babeldoc_server.babeldoc_compat import inject_ocr_table_paragraphs
from local_babeldoc_server.babeldoc_compat import translate_toc_paragraph
from local_babeldoc_server.structure_rules import REFERENCE_LABEL
from local_babeldoc_server.structure_rules import TOC_LABEL
from local_babeldoc_server.table_ocr import OcrTextBlock
from local_babeldoc_server.table_ocr import PdfBox


PARAGRAPH_MODULE = "babeldoc.format.pdf.document_il.midend.paragraph_finder"
TRANSLATOR_MODULE = "babeldoc.format.pdf.document_il.midend.il_translator"
IL_MODULE = "babeldoc.format.pdf.document_il.il_version_1"
LAYOUT_MODULE = "babeldoc.format.pdf.document_il.midend.layout_parser"
PDF_CREATER_MODULE = "babeldoc.format.pdf.document_il.backend.pdf_creater"
STYLES_MODULE = "babeldoc.format.pdf.document_il.midend.styles_and_formulas"


class FlexibleIlObject:
    def __init__(self, *args, **kwargs):
        names = ("x", "y", "x2", "y2")
        for name, value in zip(names, args):
            setattr(self, name, value)
        for name, value in kwargs.items():
            setattr(self, name, value)


class PdfSameStyleUnicodeCharacters:
    def __init__(self, unicode=None, pdf_style=None, debug_info=None):
        self.unicode = unicode
        self.pdf_style = pdf_style
        self.debug_info = debug_info


class PdfParagraphComposition:
    def __init__(self, pdf_same_style_unicode_characters=None):
        self.pdf_same_style_unicode_characters = pdf_same_style_unicode_characters


class FakeParagraphFinder:
    def __init__(self):
        self.translation_config = SimpleNamespace()

    def process(self, document):
        document.original_process_called = True
        return "original-result"


class FakeILTranslator:
    class TranslateInput:
        def __init__(self, unicode, placeholders, base_style):
            self.unicode = unicode
            self.placeholders = placeholders
            self.base_style = base_style
            self.original_placeholder_tokens = None

        def set_original_placeholder_tokens(self, tokens):
            self.original_placeholder_tokens = tokens

    def __init__(self):
        self.translation_config = SimpleNamespace(
            lang_out="zh",
            translation_retry_chunk_sizes=[700, 350],
        )
        self.translate_engine = None
        self.initial_output = None

    def pre_translate_paragraph(
        self, paragraph, tracker, page_font_map, xobj_font_map
    ):
        return "ordinary", object()

    def translate_paragraph(self, paragraph, *args, **kwargs):
        paragraph.used_original_translation = True
        tracker = kwargs.get("tracker")
        if tracker is not None and self.initial_output is not None:
            translate_input = self.TranslateInput(
                paragraph.unicode,
                [],
                paragraph.pdf_style,
            )
            self.post_translate_paragraph(
                paragraph,
                tracker,
                translate_input,
                self.initial_output,
            )

    def generate_prompt_for_llm(
        self,
        text,
        title_paragraph=None,
        local_title_paragraph=None,
        translate_input=None,
    ):
        return text

    def post_translate_paragraph(
        self, paragraph, tracker, translate_input, translated_text
    ):
        tracker.set_output(translated_text)
        paragraph.unicode = translated_text
        paragraph.pdf_paragraph_composition = []
        return True


class FakeLayoutParser:
    def __init__(self):
        self.translation_config = SimpleNamespace()

    def process(self, document, mupdf_document):
        document.original_layout_process_called = True
        return document


class FakeStylesAndFormulas:
    def __init__(self):
        self.translation_config = SimpleNamespace()

    def process(self, document):
        document.original_styles_process_called = True
        document.paragraphs_seen_by_styles = [
            list(page.pdf_paragraph) for page in document.page
        ]
        return document


class FakeRectangleRenderUnit:
    def __init__(self, rectangle, render_order, sub_render_order=0, line_width=0.4):
        self.rectangle = rectangle
        self.render_order = render_order
        self.sub_render_order = sub_render_order
        self.line_width = line_width

    def get_sort_key(self):
        return self.render_order, self.sub_render_order


class FakePDFCreater:
    def create_render_units_for_page(self, page, translation_config):
        return [SimpleNamespace(kind="original", render_order=1)]


def fake_modules():
    babeldoc = types.ModuleType("babeldoc")
    babeldoc.__path__ = []
    babeldoc.__version__ = "0.6.4"
    paragraph_module = types.ModuleType(PARAGRAPH_MODULE)
    paragraph_module.ParagraphFinder = FakeParagraphFinder
    translator_module = types.ModuleType(TRANSLATOR_MODULE)
    translator_module.ILTranslator = FakeILTranslator
    il_module = types.ModuleType(IL_MODULE)
    il_module.PdfSameStyleUnicodeCharacters = PdfSameStyleUnicodeCharacters
    il_module.PdfParagraphComposition = PdfParagraphComposition
    il_module.Box = FlexibleIlObject
    il_module.GraphicState = FlexibleIlObject
    il_module.PdfStyle = FlexibleIlObject
    il_module.PdfParagraph = FlexibleIlObject
    il_module.PdfRectangle = FlexibleIlObject
    layout_module = types.ModuleType(LAYOUT_MODULE)
    layout_module.LayoutParser = FakeLayoutParser
    pdf_creater_module = types.ModuleType(PDF_CREATER_MODULE)
    pdf_creater_module.PDFCreater = FakePDFCreater
    pdf_creater_module.RectangleRenderUnit = FakeRectangleRenderUnit
    styles_module = types.ModuleType(STYLES_MODULE)
    styles_module.StylesAndFormulas = FakeStylesAndFormulas
    return {
        "babeldoc": babeldoc,
        PARAGRAPH_MODULE: paragraph_module,
        TRANSLATOR_MODULE: translator_module,
        IL_MODULE: il_module,
        LAYOUT_MODULE: layout_module,
        PDF_CREATER_MODULE: pdf_creater_module,
        STYLES_MODULE: styles_module,
    }


def paragraph(text, label=None):
    style = SimpleNamespace(font_id="base", font_size=10)
    return SimpleNamespace(
        unicode=text,
        layout_label=label,
        pdf_style=style,
        pdf_paragraph_composition=[],
        box=SimpleNamespace(x=40.0, y=100.0, x2=520.0, y2=112.0),
    )


class FakeTracker:
    def __init__(self):
        self.pdf_unicode = None
        self.input = None
        self.output = None

    def set_pdf_unicode(self, value):
        self.pdf_unicode = value

    def set_input(self, value):
        self.input = value

    def set_output(self, value):
        self.output = value


class FakeTranslateEngine:
    def __init__(self, output):
        self.output = output
        self.inputs = []

    def translate(self, text, rate_limit_params=None):
        self.inputs.append(text)
        return self.output


class FakeQueuedLLMEngine:
    def __init__(self, outputs, require_cache_bypass=False, simple_outputs=None):
        self.outputs = list(outputs)
        self.inputs = []
        self.require_cache_bypass = require_cache_bypass
        self.simple_outputs = list(simple_outputs or [])
        self.simple_inputs = []

    def llm_translate(self, text, ignore_cache=False, rate_limit_params=None):
        self.inputs.append(text)
        if self.require_cache_bypass and not ignore_cache:
            return ""
        return self.outputs.pop(0) if self.outputs else ""

    def translate(self, text, ignore_cache=False, rate_limit_params=None):
        self.simple_inputs.append(text)
        return self.simple_outputs.pop(0) if self.simple_outputs else ""


class FakeOcrRuntime:
    def __init__(self, blocks):
        self.blocks = blocks
        self.extract_calls = []

    def needs_ocr(self, table_box, characters):
        return not characters

    def extract(self, page, table_box):
        self.extract_calls.append((page, table_box))
        return list(self.blocks)


class BabeldocCompatTest(unittest.TestCase):
    def test_empty_translation_never_replaces_source(self):
        modules = fake_modules()
        source = paragraph("Mobile robots navigate autonomously.")
        original_composition = object()
        source.pdf_paragraph_composition = [original_composition]
        tracker = FakeTracker()
        translate_input = FakeILTranslator.TranslateInput(
            source.unicode,
            [],
            source.pdf_style,
        )

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            result = FakeILTranslator().post_translate_paragraph(
                source,
                tracker,
                translate_input,
                "  \n",
            )
            handle.restore()

        self.assertFalse(result)
        self.assertEqual(source.unicode, "Mobile robots navigate autonomously.")
        self.assertEqual(source.pdf_paragraph_composition, [original_composition])

    def test_rejected_translation_is_recovered_from_validated_chunks(self):
        modules = fake_modules()
        source = paragraph(
            "First sentence explains robot navigation. "
            "Second sentence explains sensor fusion."
        )
        tracker = FakeTracker()
        translator = FakeILTranslator()
        translator.initial_output = ""
        translator.translation_config.translation_retry_chunk_sizes = [48]
        translator.translate_engine = FakeQueuedLLMEngine(
            ["第一句解释机器人导航。", "第二句解释传感器融合。"]
        )

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            translator.translate_paragraph(source, tracker=tracker)
            handle.restore()

        self.assertEqual(
            source.unicode,
            "第一句解释机器人导航。 第二句解释传感器融合。",
        )
        self.assertEqual(
            translator.translate_engine.inputs,
            [
                "First sentence explains robot navigation.",
                "Second sentence explains sensor fusion.",
            ],
        )
        quality = translator.translation_config.local_translation_quality
        self.assertEqual(quality["recovered_count"], 1)
        self.assertEqual(quality["unresolved_count"], 0)

    def test_retry_exhaustion_preserves_original_composition_and_records_failure(self):
        modules = fake_modules()
        source = paragraph(
            "A long source paragraph describes autonomous navigation and "
            "sensor fusion without losing any original content."
        )
        original_composition = object()
        source.pdf_paragraph_composition = [original_composition]
        tracker = FakeTracker()
        translator = FakeILTranslator()
        translator.initial_output = ""
        translator.translation_config.translation_retry_chunk_sizes = [60, 30]
        translator.translate_engine = FakeQueuedLLMEngine([""] * 8)

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            translator.translate_paragraph(source, tracker=tracker)
            handle.restore()

        self.assertEqual(
            source.unicode,
            "A long source paragraph describes autonomous navigation and "
            "sensor fusion without losing any original content.",
        )
        self.assertEqual(source.pdf_paragraph_composition, [original_composition])
        quality = translator.translation_config.local_translation_quality
        self.assertEqual(quality["recovered_count"], 0)
        self.assertEqual(quality["unresolved_count"], 1)
        self.assertIn("empty_target", quality["unresolved"][0]["reasons"])

    def test_recovery_bypasses_cached_empty_model_response(self):
        modules = fake_modules()
        source = paragraph("Mobile robots combine sensor measurements safely.")
        tracker = FakeTracker()
        translator = FakeILTranslator()
        translator.initial_output = ""
        translator.translation_config.translation_retry_chunk_sizes = [700]
        translator.translate_engine = FakeQueuedLLMEngine(
            ["移动机器人安全地融合传感器测量结果。"],
            require_cache_bypass=True,
        )

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            translator.translate_paragraph(source, tracker=tracker)
            handle.restore()

        self.assertEqual(source.unicode, "移动机器人安全地融合传感器测量结果。")
        self.assertEqual(
            translator.translation_config.local_translation_quality[
                "recovered_count"
            ],
            1,
        )

    def test_recovery_uses_simple_translation_when_llm_prompt_returns_empty(self):
        modules = fake_modules()
        source = paragraph("Mobile robots estimate position from sensor data.")
        tracker = FakeTracker()
        translator = FakeILTranslator()
        translator.initial_output = ""
        translator.translation_config.translation_retry_chunk_sizes = [700]
        translator.translate_engine = FakeQueuedLLMEngine(
            [""],
            simple_outputs=["移动机器人根据传感器数据估计位置。"],
        )

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            translator.translate_paragraph(source, tracker=tracker)
            handle.restore()

        self.assertEqual(source.unicode, "移动机器人根据传感器数据估计位置。")
        self.assertEqual(
            translator.translate_engine.simple_inputs,
            ["Mobile robots estimate position from sensor data."],
        )

    def test_recovery_can_drop_broken_rich_text_style_without_losing_content(self):
        modules = fake_modules()
        source = paragraph(
            "This method<style id='1'>e</style>stimates the robot position."
        )
        tracker = FakeTracker()
        translator = FakeILTranslator()
        translator.initial_output = ""
        translator.translation_config.translation_retry_chunk_sizes = [700]
        translator.translate_engine = FakeQueuedLLMEngine(
            [""],
            simple_outputs=["该方法估计机器人的位置。"],
        )

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            translator.translate_paragraph(source, tracker=tracker)
            handle.restore()

        self.assertEqual(source.unicode, "该方法估计机器人的位置。")
        self.assertEqual(
            translator.translation_config.local_translation_quality[
                "recovered_count"
            ],
            1,
        )

    def test_installs_once_marks_structure_and_restores_original_methods(self):
        modules = fake_modules()
        original_process = FakeParagraphFinder.process
        original_pre_translate = FakeILTranslator.pre_translate_paragraph
        doc = SimpleNamespace(
            page=[
                SimpleNamespace(
                    pdf_paragraph=[
                        paragraph("References"),
                        paragraph("[1] D. Di Paola, Paper"),
                    ]
                )
            ]
        )

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            second = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            result = FakeParagraphFinder().process(doc)

            self.assertEqual(result, "original-result")
            self.assertTrue(doc.original_process_called)
            self.assertEqual(
                [p.layout_label for p in doc.page[0].pdf_paragraph],
                [REFERENCE_LABEL, REFERENCE_LABEL],
            )
            self.assertTrue(second.already_installed)

            handle.restore()
            self.assertIs(FakeParagraphFinder.process, original_process)
            self.assertIs(
                FakeILTranslator.pre_translate_paragraph, original_pre_translate
            )

    def test_reference_paragraph_bypasses_translation_preparation(self):
        modules = fake_modules()
        reference = paragraph("[1] D. Di Paola, Paper", REFERENCE_LABEL)
        tracker = FakeTracker()

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            result = FakeILTranslator().pre_translate_paragraph(
                reference, tracker, {}, {}
            )
            handle.restore()

        self.assertEqual(result, (None, None))
        self.assertEqual(tracker.pdf_unicode, reference.unicode)

    def test_ocr_reference_paragraph_bypasses_formula_style_processing(self):
        modules = fake_modules()
        reference = paragraph(
            "[30] A. C. Murtra, Efficient use of 3D environment models, "
            "vol. 6472, 2010, pp. 461–472.",
            REFERENCE_LABEL,
        )
        body = paragraph("Mobile robots navigate autonomously.", "plain text")
        reference_background = SimpleNamespace(
            box=SimpleNamespace(x=35.0, y=95.0, x2=525.0, y2=117.0),
            fill_background=True,
        )
        body_background = SimpleNamespace(
            box=SimpleNamespace(x=35.0, y=195.0, x2=525.0, y2=217.0),
            fill_background=True,
        )
        page = SimpleNamespace(
            pdf_paragraph=[reference, body],
            pdf_rectangle=[reference_background, body_background],
        )
        document = SimpleNamespace(page=[page])

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {
                    "preserve_references": True,
                    "preserve_toc_layout": True,
                    "enable_table_ocr": True,
                },
                FakeOcrRuntime([]),
            )
            styles = FakeStylesAndFormulas()
            styles.translation_config = SimpleNamespace(ocr_workaround=True)
            styles.process(document)
            handle.restore()

        self.assertEqual(document.paragraphs_seen_by_styles, [[body]])
        self.assertEqual(page.pdf_paragraph, [reference, body])
        self.assertEqual(page.pdf_rectangle, [body_background])

    def test_ocr_paragraph_is_prepared_for_translation(self):
        modules = fake_modules()
        ocr_paragraph = paragraph("Classification", "babeldoc_table_ocr")
        tracker = FakeTracker()

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {"preserve_references": True, "preserve_toc_layout": True}
            )
            text, translate_input = FakeILTranslator().pre_translate_paragraph(
                ocr_paragraph, tracker, {}, {}
            )
            handle.restore()

        self.assertEqual(text, "Classification")
        self.assertEqual(translate_input.unicode, "Classification")
        self.assertEqual(tracker.pdf_unicode, "Classification")
        self.assertEqual(tracker.input, "Classification")

    def test_toc_translates_only_title_and_rebuilds_page_anchor(self):
        modules = fake_modules()
        toc = paragraph(
            "9.3.2  Interactive Long Video Generation . . . . . . 244",
            TOC_LABEL,
        )
        engine = FakeTranslateEngine("交互式长视频生成")
        translator = SimpleNamespace(translate_engine=engine)
        tracker = FakeTracker()

        with patch.dict(sys.modules, modules):
            handled = translate_toc_paragraph(translator, toc, tracker)

        self.assertTrue(handled)
        self.assertEqual(engine.inputs, ["Interactive Long Video Generation"])
        self.assertTrue(toc.unicode.startswith("9.3.2  交互式长视频生成"))
        self.assertTrue(toc.unicode.endswith("244"))
        self.assertIn("...", toc.unicode)
        self.assertEqual(tracker.input, "Interactive Long Video Generation")
        self.assertEqual(tracker.output, toc.unicode)
        rendered = toc.pdf_paragraph_composition[0]
        self.assertEqual(
            rendered.pdf_same_style_unicode_characters.unicode, toc.unicode
        )

    def test_injects_translatable_paragraph_and_background_for_ocr_table_text(self):
        modules = fake_modules()
        table_layout = SimpleNamespace(
            class_name="table",
            box=SimpleNamespace(x=20.0, y=100.0, x2=520.0, y2=400.0),
        )
        il_page = SimpleNamespace(
            page_number=0,
            page_layout=[table_layout],
            pdf_character=[],
            pdf_paragraph=[],
            pdf_rectangle=[],
        )
        doc = SimpleNamespace(page=[il_page])
        block = OcrTextBlock(
            text="Classification",
            confidence=0.94,
            pdf_box=PdfBox(40, 350, 140, 370),
            background_rgb=(78, 143, 132),
        )
        runtime = FakeOcrRuntime([block])

        with patch.dict(sys.modules, modules):
            stats = inject_ocr_table_paragraphs(doc, [object()], runtime)

        self.assertEqual(stats.table_regions, 1)
        self.assertEqual(stats.ocr_table_regions, 1)
        self.assertEqual(stats.ocr_text_blocks, 1)
        self.assertEqual(len(runtime.extract_calls), 1)
        injected = il_page.pdf_paragraph[0]
        self.assertEqual(injected.unicode, "Classification")
        self.assertEqual(injected.layout_label, "babeldoc_table_ocr")
        self.assertEqual(injected.render_order, 1_000_000)
        self.assertEqual(
            (injected.box.x, injected.box.y, injected.box.x2, injected.box.y2),
            (40, 350, 140, 370),
        )
        background = il_page.pdf_rectangle[0]
        self.assertTrue(background.fill_background)
        self.assertEqual(background.line_width, -1.0)
        self.assertEqual(background.render_order, 999_999)
        self.assertTrue(
            background.graphic_state.passthrough_per_char_instruction.endswith(" ")
        )

    def test_runtime_patch_injects_after_paragraph_finder_and_renders_background(self):
        modules = fake_modules()
        table_layout = SimpleNamespace(
            class_name="table",
            box=SimpleNamespace(x=20.0, y=100.0, x2=520.0, y2=400.0),
        )
        page = SimpleNamespace(
            page_number=0,
            page_layout=[table_layout],
            pdf_character=[],
            pdf_paragraph=[],
            pdf_rectangle=[],
        )
        doc = SimpleNamespace(page=[page])
        block = OcrTextBlock(
            text="Functions",
            confidence=0.96,
            pdf_box=PdfBox(360, 350, 500, 370),
            background_rgb=(78, 143, 132),
        )
        runtime = FakeOcrRuntime([block])
        mupdf_document = [object()]

        with patch.dict(sys.modules, modules):
            handle = install_babeldoc_compat(
                {
                    "preserve_references": True,
                    "preserve_toc_layout": True,
                    "enable_table_ocr": True,
                },
                runtime,
            )
            layout_parser = FakeLayoutParser()
            paragraph_finder = FakeParagraphFinder()
            styles_and_formulas = FakeStylesAndFormulas()
            shared_config = SimpleNamespace()
            layout_parser.translation_config = shared_config
            paragraph_finder.translation_config = shared_config
            styles_and_formulas.translation_config = shared_config
            layout_parser.process(doc, mupdf_document)
            paragraph_finder.process(doc)
            self.assertEqual(page.pdf_paragraph, [])
            styles_and_formulas.process(doc)
            units = FakePDFCreater().create_render_units_for_page(
                page, SimpleNamespace(ocr_workaround=False, debug=False)
            )
            handle.restore()

        self.assertTrue(doc.original_layout_process_called)
        self.assertTrue(doc.original_styles_process_called)
        self.assertEqual(page.pdf_paragraph[-1].unicode, "Functions")
        self.assertEqual(shared_config.local_table_stats.ocr_text_blocks, 1)
        background_units = [
            unit for unit in units if isinstance(unit, FakeRectangleRenderUnit)
        ]
        self.assertEqual(len(background_units), 1)
        self.assertEqual(background_units[0].render_order, 999_999)
        self.assertEqual(background_units[0].line_width, 0.0)


if __name__ == "__main__":
    unittest.main()
