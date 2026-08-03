# Split PDF Render Integrity Design

## Problem

Translation task `b4248a83479c4efab5869279ccc77f8a` produced a 61-page
side-by-side PDF with three severe defects:

- nearly every source-side page rendered as scrambled or piled-up glyphs;
- native table translations contained overlapping duplicate text;
- table-region backgrounds could hide content even though the model-response audit
  reported a complete translation.

The source attachment is
`Borges et al. - 2022 - A Survey on Terrain Traversability Analysis for
Autonomous Ground Vehicles`. BabelDOC split it into three 20-page parts and one
1-page part.

## Root Causes

### Repeated font subsetting

Each part is already rendered and font-subset before merge. BabelDOC's
`ResultMerger` inserts those part PDFs into a new document and calls
`PDFCreater.subset_fonts_in_subprocess` again. With this paper's embedded custom
Type 1 fonts, the second subset pass rewrites the merged font resources without
preserving the source content streams' glyph mapping. The pre-subset merged PDF
renders correctly; the post-subset merged PDF does not.

### Late table-OCR eligibility check

The compatibility layer currently asks `TableOcrRuntime.needs_ocr` after
`StylesAndFormulas.process`. That BabelDOC stage consumes or reorganizes native
`pdf_character` entries. A native-text table therefore appears to contain no
extractable characters and is incorrectly sent through table OCR. The original
native table translation and the OCR translation are both rendered in the same
cells, with OCR background rectangles capable of covering nearby content.

### No final source-render audit

Translation completeness checks model inputs and outputs, but a structurally
complete set of translations does not prove that the final PDF renders correctly.
The corrupted source side was therefore published as a successful task.

## Considered Approaches

### Disable table OCR and PDF font subsetting globally

This avoids both observed failures, but image-only tables would stop translating
and all non-split documents would lose an effective size optimization. It is too
broad.

### Rasterize the source side and all tables

Rasterization guarantees visual stability, but loses selectable/searchable source
text, increases file size, and degrades accessibility. It is not acceptable as
the default output.

### Stage-aware decisions and a publish-time render guard

This is the selected approach. Record table-OCR eligibility while native
characters are still present, skip only the redundant subset pass for merged
part results, and reject a side-by-side dual PDF when its source half no longer
matches the source PDF. This preserves selectable text and existing image-table
translation while addressing each root cause at its origin.

## Design

### Table OCR eligibility snapshot

Immediately after `LayoutParser.process`, inspect every layout classified as a
table against that page's native `pdf_character` collection. Store the set of
`(page_number, table_index)` entries that truly need OCR on the shared translation
configuration. After `StylesAndFormulas.process`, inject OCR paragraphs only for
that frozen set. Do not recalculate eligibility from the mutated document.

Statistics continue to count every detected table region, but OCR-region and
OCR-block counts include only prequalified image-based tables.

### Split-result merge policy

Patch `PDFCreater.subset_fonts_in_subprocess` through the existing BabelDOC 0.6.4
compatibility layer. Tags beginning with `merged` return the already-merged
document unchanged; normal per-part and debug subsetting keeps its current
behavior. BabelDOC's existing save/deflate step still writes and compresses the
final PDF.

### Dual source-render integrity audit

Add a small PDF output-quality module that compares each source page with the
source half of its corresponding side-by-side dual page. It verifies page count,
page geometry, and low-resolution grayscale rendering. A page fails when the
fraction and magnitude of changed pixels exceed conservative tolerances.

The guard applies to `lort` and `ltro`. Alternating-page modes are not changed in
this fix. The audit runs after BabelDOC returns paths and before the server marks
the job successful. A failed audit keeps the generated artifacts for diagnosis
but prevents publication as a successful translation.

### Content and table validation

The existing translation-completeness audit remains mandatory. The table fix is
validated by ensuring native table regions produce zero OCR injections while a
synthetic image table still produces OCR paragraphs and backgrounds. The Borges
PDF validation also compares all 61 source-side pages, inspects every table page,
and renders every page of the mono and dual outputs.

## Error Handling

- A render-audit mismatch raises a descriptive error with the first failed page
  and measured difference.
- Missing PDFs, page-count mismatches, and incompatible page geometry fail the
  task instead of silently skipping validation.
- OCR extraction failure retains the existing behavior: no OCR paragraphs are
  injected for that region.

## Acceptance Criteria

- All existing and new unit tests pass.
- Native tables are translated exactly once and contain no OCR overlay.
- Image-only tables remain eligible for OCR translation.
- Split-result merging does not call font subsetting a second time.
- The source side of all 61 dual pages passes render comparison with the uploaded
  source PDF.
- All 61 pages of the generated mono and dual PDFs render successfully.
- The translated table pages 11, 13, 14, 21, 42, 43, 45, 46, and 47 show no
  duplicate OCR overlay or hidden body text.
- Translation completeness reports zero unresolved substantial paragraphs.

## Out of Scope

- Replacing BabelDOC's general table layout engine.
- Rasterizing source pages or ordinary native tables.
- Changing the translation model, prompt, terminology, or Zotero UI.
