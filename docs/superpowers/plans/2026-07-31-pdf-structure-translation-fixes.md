# PDF Structure Translation Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make BabelDOC translate native and image-based table text, preserve complete reference sections in the source language, and retain translated table-of-contents alignment.

**Architecture:** Add a version-gated downstream compatibility layer under `local_babeldoc_server` instead of editing the ignored BabelDOC checkout. Pure structure rules identify references and TOC entries; runtime adapters wrap BabelDOC's layout, paragraph, translation, and render boundaries; a lazy RapidOCR adapter injects OCR table paragraphs only for table regions without extractable text.

**Tech Stack:** Python 3.12, BabelDOC 0.6.4 document IL, PyMuPDF, RapidOCR with ONNX Runtime, `unittest`, TypeScript/Zotero build tooling.

## Global Constraints

- Keep BabelDOC pinned to exactly `v0.6.4`.
- Do not edit or commit the ignored `BabelDOC/` checkout.
- All three fixes are enabled by default and add no Zotero preference fields.
- OCR is restricted to detected table regions and is preinstalled by the backend installers.
- Reference headings and entries remain entirely untranslated.
- TOC section numbers, indentation, line boundaries, dot leaders, and right-aligned page numbers are preserved.
- Dual PDF original pages must remain unchanged.
- Existing Zotero submission, polling, download, and attachment-import APIs remain unchanged.

## File Structure

- Create `local_babeldoc_server/structure_rules.py`: pure reference and TOC detection/parsing rules, independent of BabelDOC imports.
- Create `local_babeldoc_server/table_ocr.py`: lazy RapidOCR initialization, crop OCR, coordinate conversion, and OCR result normalization.
- Create `local_babeldoc_server/babeldoc_compat.py`: BabelDOC 0.6.4 runtime adapters and IL object creation.
- Create `local_babeldoc_server/requirements.txt`: pinned downstream OCR dependency.
- Modify `local_babeldoc_server/server.py`: default flags, compatibility installation, OCR lifecycle, and task statistics.
- Modify `local_babeldoc_server/config.example.json`: document the four advanced rollback flags.
- Modify `scripts/install-local-backend.sh` and `scripts/install-local-backend.ps1`: install and prewarm RapidOCR in BabelDOC's virtual environment.
- Create `tests/test_structure_rules.py`: pure rules tests.
- Create `tests/test_table_ocr.py`: OCR selection and coordinate tests with a fake engine.
- Create `tests/test_babeldoc_compat.py`: fake BabelDOC module/IL integration tests.
- Modify `tests/test_local_babeldoc_server.py`: config wiring and installed dependency assertions.
- Modify `local_babeldoc_server/README.md` and root `README.md`: behavior and troubleshooting.

---

### Task 1: Reference and TOC Structure Rules

**Files:**
- Create: `local_babeldoc_server/structure_rules.py`
- Create: `tests/test_structure_rules.py`

**Interfaces:**
- Produces: `normalize_heading(text: str) -> str`
- Produces: `is_reference_heading(text: str) -> bool`
- Produces: `is_post_reference_heading(text: str) -> bool`
- Produces: `TocEntry(prefix: str, title: str, leader: str, page_number: str)`
- Produces: `parse_toc_entry(text: str) -> TocEntry | None`
- Produces: `rebuild_toc_entry(entry: TocEntry, translated_title: str, target_columns: int) -> str`
- Produces: `mark_document_structure(document: Any) -> StructureStats`

- [ ] **Step 1: Write failing reference-boundary tests**

```python
def test_marks_reference_heading_and_entries_but_resumes_at_appendix():
    doc = fake_document([
        ["9 Conclusion", "References", "[1] A. Author, Paper"],
        ["[2] B. Author, Paper", "Appendix A", "Proof details"],
    ])
    stats = mark_document_structure(doc)
    assert labels(doc) == [
        None, "babeldoc_preserve_reference", "babeldoc_preserve_reference",
        "babeldoc_preserve_reference", None, None,
    ]
    assert stats.reference_paragraphs == 3
```

- [ ] **Step 2: Run the focused test and confirm the missing-module failure**

Run: `python3 -m unittest tests.test_structure_rules.StructureRulesTest.test_marks_reference_heading_and_entries_but_resumes_at_appendix -v`

Expected: FAIL because `local_babeldoc_server.structure_rules` does not exist.

- [ ] **Step 3: Implement normalized heading and stateful reference marking**

```python
REFERENCE_HEADINGS = frozenset({
    "references", "bibliography", "works cited", "literature cited",
})
POST_REFERENCE_HEADINGS = re.compile(
    r"^(appendix(?:\s+[a-z0-9]+)?|supplementary material|author biographies?)$",
    re.IGNORECASE,
)

def mark_document_structure(document: Any) -> StructureStats:
    in_references = False
    for page in document.page:
        heading = next((p for p in page.pdf_paragraph if is_reference_heading(p.unicode or "")), None)
        if heading is not None:
            in_references = True
        for paragraph in page.pdf_paragraph:
            if in_references and is_post_reference_heading(paragraph.unicode or ""):
                in_references = False
            if in_references and paragraph_is_at_or_below_reference_heading(paragraph, heading):
                paragraph.layout_label = REFERENCE_LABEL
```

- [ ] **Step 4: Write failing TOC parsing and reconstruction tests**

```python
def test_rebuilds_spaced_dot_leader_and_right_page_number():
    entry = parse_toc_entry("9.3.2  Interactive Long Video Generation . . . . . 244")
    assert entry.prefix == "9.3.2"
    assert entry.title == "Interactive Long Video Generation"
    rebuilt = rebuild_toc_entry(entry, "交互式长视频生成", target_columns=64)
    assert rebuilt.startswith("9.3.2  交互式长视频生成")
    assert rebuilt.endswith("244")
    assert len(rebuilt) >= 58
```

- [ ] **Step 5: Run the TOC test and confirm it fails**

Run: `python3 -m unittest tests.test_structure_rules.StructureRulesTest.test_rebuilds_spaced_dot_leader_and_right_page_number -v`

Expected: FAIL because `parse_toc_entry` is not implemented.

- [ ] **Step 6: Implement TOC parsing, page detection, and width-aware leaders**

```python
TOC_ENTRY_RE = re.compile(
    r"^\s*(?P<prefix>(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.?)?)\s*"
    r"(?P<title>.*?)\s+(?P<leader>(?:\.\s*){2,})"
    r"(?P<page>\d+|[ivxlcdm]+)\s*$",
    re.IGNORECASE,
)

def rebuild_toc_entry(entry: TocEntry, translated_title: str, target_columns: int) -> str:
    fixed = f"{entry.prefix}  {translated_title}".strip()
    page = entry.page_number
    leader_count = max(2, target_columns - display_width(fixed) - display_width(page) - 2)
    return f"{fixed} {'.' * leader_count} {page}"
```

- [ ] **Step 7: Run all pure structure tests**

Run: `python3 -m unittest tests.test_structure_rules -v`

Expected: PASS.

- [ ] **Step 8: Commit the structure rules**

```bash
git add local_babeldoc_server/structure_rules.py tests/test_structure_rules.py
git commit -m "feat: detect references and structured TOC entries"
```

---

### Task 2: BabelDOC Reference and TOC Runtime Adapter

**Files:**
- Create: `local_babeldoc_server/babeldoc_compat.py`
- Create: `tests/test_babeldoc_compat.py`
- Modify: `local_babeldoc_server/server.py`

**Interfaces:**
- Consumes: `mark_document_structure`, `parse_toc_entry`, `rebuild_toc_entry`
- Produces: `install_babeldoc_compat(config: dict[str, Any], ocr_runtime: TableOcrRuntime | None = None) -> CompatibilityHandle`
- Produces: `CompatibilityHandle.restore() -> None`
- Produces: `translate_toc_paragraph(translator: Any, paragraph: Any, tracker: Any, ...) -> bool`

- [ ] **Step 1: Write a failing compatibility installation test**

```python
def test_installer_wraps_paragraph_finder_once_and_can_restore():
    modules, original_process = fake_babeldoc_modules()
    with patch.dict(sys.modules, modules):
        handle = install_babeldoc_compat(default_flags())
        assert modules[PARAGRAPH_MODULE].ParagraphFinder.process is not original_process
        second = install_babeldoc_compat(default_flags())
        assert second.already_installed is True
        handle.restore()
        assert modules[PARAGRAPH_MODULE].ParagraphFinder.process is original_process
```

- [ ] **Step 2: Run the installation test and confirm it fails**

Run: `python3 -m unittest tests.test_babeldoc_compat.BabeldocCompatTest.test_installer_wraps_paragraph_finder_once_and_can_restore -v`

Expected: FAIL because `install_babeldoc_compat` does not exist.

- [ ] **Step 3: Implement version/API guards and idempotent wrappers**

```python
SUPPORTED_BABELDOC_VERSION = "0.6.4"

def install_babeldoc_compat(config, ocr_runtime=None):
    paragraph_finder = import_module(
        "babeldoc.format.pdf.document_il.midend.paragraph_finder"
    ).ParagraphFinder
    il_translator = import_module(
        "babeldoc.format.pdf.document_il.midend.il_translator"
    ).ILTranslator
    require_callables(paragraph_finder, il_translator)
    if getattr(paragraph_finder.process, PATCH_MARKER, False):
        return CompatibilityHandle(already_installed=True)
    originals = patch_paragraph_and_translation_boundaries(
        paragraph_finder, il_translator, config
    )
    return CompatibilityHandle(originals=originals)
```

- [ ] **Step 4: Write failing reference bypass tests**

```python
def test_reference_paragraph_never_reaches_translate_engine():
    paragraph = fake_paragraph("[1] A. Author", REFERENCE_LABEL)
    translator = make_il_translator()
    result = translator.pre_translate_paragraph(paragraph, tracker(), {}, {})
    assert result == (None, None)
    assert translator.translate_engine.calls == []
```

- [ ] **Step 5: Implement reference bypass in the pre-translation wrapper**

```python
def wrapped_pre_translate(self, paragraph, tracker, page_font_map, xobj_font_map):
    if paragraph.layout_label == REFERENCE_LABEL:
        tracker.set_pdf_unicode(paragraph.unicode)
        return None, None
    return original_pre_translate(self, paragraph, tracker, page_font_map, xobj_font_map)
```

- [ ] **Step 6: Write failing TOC special-translation tests**

```python
def test_toc_translates_only_title_and_rebuilds_leader():
    paragraph = fake_toc_paragraph(
        "9.3.2 Interactive Long Video Generation . . . . 244", width=360
    )
    translator = make_il_translator(output="交互式长视频生成")
    handled = translate_toc_paragraph(translator, paragraph, tracker(), {}, {})
    assert handled is True
    assert translator.translate_engine.inputs == ["Interactive Long Video Generation"]
    assert paragraph.unicode.endswith("244")
    assert "..." in paragraph.unicode
```

- [ ] **Step 7: Implement TOC-only translation and composition replacement**

```python
def translate_toc_paragraph(self, paragraph, tracker, page_font_map, xobj_font_map):
    entry = parse_toc_entry(paragraph.unicode or "")
    if paragraph.layout_label != TOC_LABEL or entry is None:
        return False
    translated = self.translate_engine.translate(entry.title, rate_limit_params={})
    rebuilt = rebuild_toc_entry(entry, translated, target_columns_for(paragraph))
    replace_with_single_style_unicode(paragraph, rebuilt)
    return True
```

- [ ] **Step 8: Wire default flags and install the adapter before `async_translate`**

Add these defaults under `DEFAULT_CONFIG["babeldoc"]`:

```python
"enable_native_table_translation": True,
"enable_table_ocr": True,
"preserve_references": True,
"preserve_toc_layout": True,
```

Call `install_babeldoc_compat(babeldoc_cfg, self.table_ocr_runtime)` once after the BabelDOC path is inserted and before `_run_babeldoc` imports `async_translate`.

- [ ] **Step 9: Run compatibility and server tests**

Run: `python3 -m unittest tests.test_babeldoc_compat tests.test_local_babeldoc_server -v`

Expected: PASS.

- [ ] **Step 10: Commit the runtime adapter**

```bash
git add local_babeldoc_server/babeldoc_compat.py local_babeldoc_server/server.py tests/test_babeldoc_compat.py
git commit -m "feat: preserve references and TOC structure"
```

---

### Task 3: Table OCR Selection and Coordinate Mapping

**Files:**
- Create: `local_babeldoc_server/table_ocr.py`
- Create: `tests/test_table_ocr.py`

**Interfaces:**
- Produces: `OcrTextBlock(text: str, confidence: float, pdf_box: PdfBox, background_rgb: tuple[int, int, int])`
- Produces: `TableOcrRuntime(engine_factory: Callable[[], Any], scale: float = 2.0, min_confidence: float = 0.55)`
- Produces: `TableOcrRuntime.needs_ocr(table_box: PdfBox, characters: Iterable[Any]) -> bool`
- Produces: `TableOcrRuntime.extract(page: Any, table_box: PdfBox) -> list[OcrTextBlock]`
- Produces: `image_box_to_pdf_box(...) -> PdfBox`

- [ ] **Step 1: Write failing OCR selection tests**

```python
def test_ocr_runs_only_when_table_has_insufficient_native_text():
    runtime = TableOcrRuntime(engine_factory=FakeEngine)
    table = PdfBox(20, 100, 500, 400)
    assert runtime.needs_ocr(table, []) is True
    assert runtime.needs_ocr(table, native_characters(60, inside=table)) is False
```

- [ ] **Step 2: Run the selection test and confirm it fails**

Run: `python3 -m unittest tests.test_table_ocr.TableOcrRuntimeTest.test_ocr_runs_only_when_table_has_insufficient_native_text -v`

Expected: FAIL because `TableOcrRuntime` does not exist.

- [ ] **Step 3: Implement native-text coverage selection**

```python
def needs_ocr(self, table_box, characters):
    inside = [char for char in characters if overlap_ratio(char.visual_bbox.box, table_box) >= 0.5]
    printable = sum(1 for char in inside if (char.char_unicode or "").strip())
    return printable < self.min_native_characters
```

- [ ] **Step 4: Write failing coordinate conversion and confidence tests**

```python
def test_maps_crop_pixels_to_bottom_left_pdf_coordinates():
    box = image_box_to_pdf_box(
        image_box=(20, 10, 220, 50), crop_pdf_box=PdfBox(50, 100, 350, 250),
        page_height=700, scale=2.0,
    )
    assert box == PdfBox(60, 225, 160, 245)

def test_discards_low_confidence_blocks():
    runtime = TableOcrRuntime(lambda: FakeEngine(scores=[0.91, 0.31]))
    assert [b.text for b in runtime.extract(fake_page(), TABLE)] == ["Functions"]
```

- [ ] **Step 5: Implement crop rendering, lazy engine creation, result normalization, and background sampling**

```python
class TableOcrRuntime:
    def _engine(self):
        with self._lock:
            if self._engine_instance is None:
                self._engine_instance = self.engine_factory()
            return self._engine_instance

    def extract(self, page, table_box):
        pixmap, crop = render_table_crop(page, table_box, self.scale)
        result = self._engine()(pixmap_to_array(pixmap))
        return normalize_rapidocr_result(
            result, crop, page.rect.height, self.scale, self.min_confidence
        )
```

- [ ] **Step 6: Run all OCR unit tests**

Run: `python3 -m unittest tests.test_table_ocr -v`

Expected: PASS without importing or downloading RapidOCR because tests inject a fake engine.

- [ ] **Step 7: Commit the OCR boundary**

```bash
git add local_babeldoc_server/table_ocr.py tests/test_table_ocr.py
git commit -m "feat: add region-scoped table OCR"
```

---

### Task 4: Inject and Render OCR Table Paragraphs

**Files:**
- Modify: `local_babeldoc_server/babeldoc_compat.py`
- Modify: `tests/test_babeldoc_compat.py`
- Modify: `local_babeldoc_server/server.py`

**Interfaces:**
- Consumes: `TableOcrRuntime.extract(page, table_box) -> list[OcrTextBlock]`
- Produces: `inject_ocr_table_paragraphs(document: Any, mupdf_document: Any, runtime: TableOcrRuntime) -> TableStats`
- Produces: `TABLE_OCR_LABEL = "babeldoc_table_ocr"`

- [ ] **Step 1: Write a failing OCR IL-injection test**

```python
def test_layout_wrapper_injects_translatable_table_paragraphs():
    doc = fake_il_document(table_layout=True, native_chars=[])
    runtime = fake_ocr_runtime([block("Classification", box=(40, 200, 140, 220))])
    wrapped_layout_process(doc, fake_mupdf(), runtime)
    paragraph = doc.page[0].pdf_paragraph[0]
    assert paragraph.unicode == "Classification"
    assert paragraph.layout_label == TABLE_OCR_LABEL
    assert paragraph.box == il_box(40, 200, 140, 220)
```

- [ ] **Step 2: Run the IL-injection test and confirm it fails**

Run: `python3 -m unittest tests.test_babeldoc_compat.BabeldocCompatTest.test_layout_wrapper_injects_translatable_table_paragraphs -v`

Expected: FAIL because OCR blocks are not injected.

- [ ] **Step 3: Wrap `LayoutParser.process` and create IL paragraphs**

```python
def inject_ocr_table_paragraphs(document, mupdf_document, runtime):
    for il_page in document.page:
        table_layouts = [x for x in il_page.page_layout if x.class_name == "table"]
        for layout in table_layouts:
            if not runtime.needs_ocr(layout.box, il_page.pdf_character):
                continue
            for block in runtime.extract(mupdf_document[il_page.page_number], layout.box):
                il_page.pdf_paragraph.append(make_ocr_paragraph(block))
                il_page.pdf_rectangle.append(make_background_rectangle(block))
```

- [ ] **Step 4: Write a failing render-order test**

```python
def test_ocr_background_is_rendered_after_source_paths_before_translation_text():
    units = patched_create_render_units(page_with_ocr_rectangle(), translation_config())
    ordered = sorted(units, key=lambda unit: (unit.render_order, unit.sub_render_order))
    assert type(ordered[-2]).__name__ == "RectangleRenderUnit"
    assert type(ordered[-1]).__name__ == "CharacterRenderUnit"
```

- [ ] **Step 5: Patch the PDF creator boundary for marked OCR backgrounds**

Render only rectangles whose marker is `line_width == -1.0`; convert their effective line width to zero and assign a render order immediately before the injected translation characters. Do not enable BabelDOC's document-wide `ocr_workaround`.

- [ ] **Step 6: Add job-level table OCR lifecycle and statistics**

Create the RapidOCR engine lazily with:

```python
def create_rapidocr_engine():
    from rapidocr import RapidOCR
    return RapidOCR(params={"EngineConfig.onnxruntime.use_dml": False})
```

Store one thread-safe `TableOcrRuntime` on `AppState`; include table/OCR counts in log messages at job completion.

- [ ] **Step 7: Run adapter, OCR, and server tests**

Run: `python3 -m unittest tests.test_babeldoc_compat tests.test_table_ocr tests.test_local_babeldoc_server -v`

Expected: PASS.

- [ ] **Step 8: Commit table injection and rendering**

```bash
git add local_babeldoc_server/babeldoc_compat.py local_babeldoc_server/server.py tests/test_babeldoc_compat.py
git commit -m "feat: translate OCR-recovered table regions"
```

---

### Task 5: Cross-Platform OCR Installation and Configuration

**Files:**
- Create: `local_babeldoc_server/requirements.txt`
- Modify: `scripts/install-local-backend.sh`
- Modify: `scripts/install-local-backend.ps1`
- Modify: `local_babeldoc_server/config.example.json`
- Modify: `tests/test_local_babeldoc_server.py`

**Interfaces:**
- Consumes: BabelDOC virtual environment created by `uv sync`
- Produces: an environment where `from rapidocr import RapidOCR` succeeds and models are prewarmed

- [ ] **Step 1: Write failing installer-content tests**

```python
def test_installers_install_and_prewarm_backend_requirements():
    assert 'local_babeldoc_server/requirements.txt' in bash_installer
    assert 'from rapidocr import RapidOCR' in bash_installer
    assert 'local_babeldoc_server\\requirements.txt' in powershell_installer
    assert 'from rapidocr import RapidOCR' in powershell_installer
```

- [ ] **Step 2: Run installer tests and confirm they fail**

Run: `python3 -m unittest tests.test_local_babeldoc_server.InstallerVersionTest -v`

Expected: FAIL because OCR requirements are not installed or prewarmed.

- [ ] **Step 3: Add the pinned dependency and Unix installation commands**

`local_babeldoc_server/requirements.txt`:

```text
rapidocr>=3.4,<4
```

After `uv sync`, resolve the environment Python and execute `uv pip install --python ... -r ...`, then run a one-line RapidOCR constructor/import prewarm using that Python.

- [ ] **Step 4: Add equivalent PowerShell installation commands**

Use `Invoke-CaptureChecked` to resolve `sys.executable`, then `Invoke-Checked` for `uv pip install` and Python `-c "from rapidocr import RapidOCR; RapidOCR()"`. Keep arguments as arrays so paths containing spaces remain safe.

- [ ] **Step 5: Add all four default-enabled flags to example config and tests**

```json
"enable_native_table_translation": true,
"enable_table_ocr": true,
"preserve_references": true,
"preserve_toc_layout": true
```

- [ ] **Step 6: Run installer and server tests**

Run: `python3 -m unittest tests.test_local_babeldoc_server -v`

Expected: PASS.

- [ ] **Step 7: Commit installer and config changes**

```bash
git add local_babeldoc_server/requirements.txt local_babeldoc_server/config.example.json scripts/install-local-backend.sh scripts/install-local-backend.ps1 tests/test_local_babeldoc_server.py
git commit -m "feat: install table OCR backend"
```

---

### Task 6: Automated PDF Integration Fixtures

**Files:**
- Create: `tests/test_pdf_structure_integration.py`
- Modify: `local_babeldoc_server/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: all compatibility-layer interfaces from Tasks 1–5
- Produces: deterministic integration assertions using a fake translator and local fixture PDFs

- [ ] **Step 1: Write an integration test for the existing U-Net PDF**

Use `.local-babeldoc/uploads/12ec6b78540f4697ad70c66efa00629e.pdf` when present; skip with an explicit message otherwise. Assert fake-translator inputs include `Group name` but exclude `References` and all numbered reference entries.

- [ ] **Step 2: Run the U-Net integration test**

Run: `python3 -m unittest tests.test_pdf_structure_integration.PdfStructureIntegrationTest.test_unet_table_and_references -v`

Expected: PASS or explicit SKIP only when the local fixture is absent.

- [ ] **Step 3: Write an integration test for the existing 49-page TOC PDF**

Use `.local-babeldoc/uploads/b75fbb14eba84fa4906e47a1165b2a7b.pdf`; translate TOC titles with a deterministic fake mapping and assert extracted output lines end in the original page numbers and contain dot leaders.

- [ ] **Step 4: Run the TOC integration test**

Run: `python3 -m unittest tests.test_pdf_structure_integration.PdfStructureIntegrationTest.test_toc_preserves_lines_and_pages -v`

Expected: PASS or explicit SKIP only when the local fixture is absent.

- [ ] **Step 5: Document default behavior and OCR troubleshooting**

Document that tables, references, and TOC repair are automatic; OCR model installation happens during Install/Repair; and missing OCR raises a clear task error instead of silently leaving tables untranslated.

- [ ] **Step 6: Run the complete automated suite and build**

Run: `python3 -m unittest discover -s tests -p 'test_*.py' -v`

Expected: PASS.

Run: `pnpm build`

Expected: exit 0.

- [ ] **Step 7: Commit integration coverage and docs**

```bash
git add tests/test_pdf_structure_integration.py local_babeldoc_server/README.md README.md
git commit -m "test: cover PDF structure translation fixes"
```

---

### Task 7: Real Alatise and Hancke Translation Verification

**Files:**
- Read: `/home/lbz/Zotero/storage/NSYVJWHZ/Alatise和Hancke - 2020 - A review on challenges of autonomous mobile robot and sensor fusion methods.pdf`
- Generate under: `/tmp/alatise-structure-verification/`
- Do not modify: the source Zotero PDF

**Interfaces:**
- Consumes: the production `AppState` BabelDOC configuration and an existing configured model supplied by the Zotero environment
- Produces: verified mono and dual PDFs plus text/screenshot evidence

- [ ] **Step 1: Verify the exact source and isolate output paths**

Run: `pdfinfo '/home/lbz/Zotero/storage/NSYVJWHZ/Alatise和Hancke - 2020 - A review on challenges of autonomous mobile robot and sensor fusion methods.pdf'`

Expected: 17 pages and title `A Review on Challenges of Autonomous Mobile Robot and Sensor Fusion Methods`.

- [ ] **Step 2: Install the downstream OCR requirement in the local BabelDOC environment**

Run the same `uv pip install` and RapidOCR prewarm commands used by the installer, with network approval if packages or models are not cached.

Expected: `from rapidocr import RapidOCR` succeeds inside `BabelDOC/.venv`.

- [ ] **Step 3: Run a real translation into the isolated verification directory**

Use the already configured Zotero model without printing credentials. Keep source immutable and configure BabelDOC output/working directories under `/tmp/alatise-structure-verification`.

Expected: both non-empty `.mono.pdf` and `.dual.pdf` outputs.

- [ ] **Step 4: Verify table and references textually**

Extract text with `pdftotext -layout` and assert:

```text
表1
分类
传感器系统
功能
REFERENCES
[1] D. Di Paola
```

Also assert translated reference-title text `参考文献` is absent from the translated reference region.

- [ ] **Step 5: Verify visual layout**

Render the table page and first reference page at 150 DPI. Confirm table cells contain translated Chinese without covering grid lines and reference pages preserve the original English two-column layout.

- [ ] **Step 6: Verify PDF integrity and regression suite**

Run `pdfinfo` on both outputs, confirm expected page counts and sizes, rerun the complete Python suite and `pnpm build`.

- [ ] **Step 7: Commit any final test-only corrections, then record clean status**

```bash
git status --short
```

Expected: no uncommitted implementation changes. Generated verification PDFs remain under `/tmp` and are not committed.
