# Split PDF Render Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve source rendering and prevent duplicate table OCR when BabelDOC translates and merges a multi-part PDF.

**Architecture:** Extend the existing BabelDOC 0.6.4 compatibility layer with an early table-OCR eligibility snapshot and a merge-only font-subset bypass. Add a final side-by-side source-render audit before successful job publication.

**Tech Stack:** Python 3.11+, BabelDOC 0.6.4, PyMuPDF, `unittest`

## Global Constraints

- Keep native PDF source text selectable and searchable.
- Keep image-only table OCR translation enabled.
- Do not change the Zotero request schema or preferences UI.
- Do not mark a job successful when the final dual PDF source side is corrupted.
- Preserve all reference, table-of-contents, and translation-completeness safeguards.

---

### Task 1: Freeze table-OCR eligibility before character mutation

**Files:**
- Modify: `local_babeldoc_server/babeldoc_compat.py`
- Test: `tests/test_babeldoc_compat.py`

**Interfaces:**
- Produces: `select_table_ocr_regions(document, runtime) -> frozenset[tuple[int, int]]`
- Consumes: `inject_ocr_table_paragraphs(document, mupdf_document, runtime, eligible_regions)`

- [ ] **Step 1: Write the failing regression tests**

  Add one test whose layout stage contains native table characters and whose
  styles stage clears them. Assert that OCR extraction is never called. Add a
  second test showing that an initially characterless image table remains OCR
  eligible after the styles stage.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run: `python3 -m unittest tests.test_babeldoc_compat.BabeldocCompatTest.test_native_table_ocr_decision_survives_character_consumption tests.test_babeldoc_compat.BabeldocCompatTest.test_image_table_ocr_decision_survives_character_consumption -v`

  Expected: FAIL because eligibility is currently recalculated after character
  mutation.

- [ ] **Step 3: Implement the eligibility snapshot**

  Add `select_table_ocr_regions`, store its result in
  `local_table_ocr_context` during `wrapped_layout_process`, and pass it into
  `inject_ocr_table_paragraphs` after styles processing.

- [ ] **Step 4: Run the focused tests and verify GREEN**

  Run the command from Step 2. Expected: PASS.

### Task 2: Skip redundant font subsetting for merged parts

**Files:**
- Modify: `local_babeldoc_server/babeldoc_compat.py`
- Test: `tests/test_babeldoc_compat.py`

**Interfaces:**
- Patches: `PDFCreater.subset_fonts_in_subprocess(pdf, translation_config, tag)`

- [ ] **Step 1: Write a failing merge-policy test**

  Extend the fake PDF creator with a tracked static subset method. Assert that a
  `merged_dual` call returns the original document without invoking the original
  subset method, while a `debug` call still invokes it.

- [ ] **Step 2: Run the focused test and verify RED**

  Run: `python3 -m unittest tests.test_babeldoc_compat.BabeldocCompatTest.test_merged_documents_skip_second_font_subset -v`

  Expected: FAIL because the compatibility layer does not patch font subsetting.

- [ ] **Step 3: Add the merge-only bypass**

  Install a wrapper for `PDFCreater.subset_fonts_in_subprocess`. Return the input
  document for tags beginning with `merged`; delegate all other tags unchanged.
  Register the original method with `CompatibilityHandle.restore`.

- [ ] **Step 4: Run the focused test and verify GREEN**

  Run the command from Step 2. Expected: PASS.

### Task 3: Reject dual PDFs with corrupted source rendering

**Files:**
- Create: `local_babeldoc_server/pdf_output_quality.py`
- Create: `tests/test_pdf_output_quality.py`
- Modify: `local_babeldoc_server/server.py`
- Modify: `tests/test_local_babeldoc_server.py`

**Interfaces:**
- Produces: `audit_side_by_side_source(source_path, dual_path, mode) -> PdfOutputAudit`
- Produces: `ensure_side_by_side_source_preserved(audit) -> None`

- [ ] **Step 1: Write failing unit tests with generated PDFs**

  Generate a two-page source PDF and side-by-side dual PDFs in a temporary
  directory. Assert that a faithful source half passes, a scrambled or blank
  source half fails, page-count mismatch fails, and both `lort` and `ltro` select
  the correct source half.

- [ ] **Step 2: Run the new tests and verify RED**

  Run: `python3 -m unittest tests.test_pdf_output_quality -v`

  Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the render audit**

  Render source pages and the selected dual-page half to low-resolution grayscale
  pixmaps with PyMuPDF. Compare dimensions, changed-pixel fraction, and mean
  absolute pixel difference. Return page-level diagnostics and raise a descriptive
  error for a failed audit.

- [ ] **Step 4: Wire the audit before job success**

  In `_run_babeldoc`, run the audit after result-path validation and before
  `update_job(status="success")` for `lort` and `ltro` modes. Add a server test
  confirming audit failure prevents the success update.

- [ ] **Step 5: Run focused tests and verify GREEN**

  Run: `python3 -m unittest tests.test_pdf_output_quality tests.test_local_babeldoc_server -v`

  Expected: PASS.

### Task 4: Full regression and Borges PDF validation

**Files:**
- Modify if required by test findings: files from Tasks 1-3 only

**Interfaces:**
- Consumes: the current Zotero translation model configuration and the uploaded
  Borges source PDF whose SHA-256 is
  `72cc1337f05de3877e884f86d467b08513742b14395fa1fc860a2acd0bab2b2c`

- [ ] **Step 1: Run the complete unit suite**

  Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

  Expected: all tests pass without errors or warnings.

- [ ] **Step 2: Deploy the patched backend and restart its owner process**

  Copy the validated server files through the repository's normal local-backend
  deployment flow. Ensure no stale backend process remains.

- [ ] **Step 3: Rerun the Borges translation**

  Use the uploaded source with the same Zotero options and configured third-party
  translation model. Write new verified mono and dual files; do not overwrite the
  source PDF.

- [ ] **Step 4: Audit all output pages**

  Require zero unresolved translations, render all 61 mono and dual pages, compare
  all 61 source halves with the source PDF, and inspect translated table pages 11,
  13, 14, 21, 42, 43, 45, 46, and 47 for duplicate OCR text or hidden body content.

- [ ] **Step 5: Commit the implementation**

  Stage only the design, plan, source, and test files. Commit with a focused bug-fix
  message after all validation evidence is captured.
