# PDF Translation Completeness Safety Design

## Goal

Prevent missing or blank translated paragraphs when a third-party translation
model returns malformed, truncated, empty, or structurally incomplete output.
Translation completeness takes priority over layout enhancement. A generated PDF
must never silently remove source content.

## Confirmed Scope

- Preserve the existing table, table-OCR, table-layout, table-of-contents, and
  reference-section behavior.
- Validate every attempted paragraph translation before it can replace source
  content.
- Retry rejected long or rich-text paragraphs in smaller sentence-aligned chunks.
- Compare source and target structural content before accepting a translation.
- Keep the complete source paragraph when all retries fail, record the failure,
  and prevent the job from reporting an unqualified successful result.
- Re-run the named Alatise and Hancke PDF through Zotero's currently configured
  third-party model and perform automated and rendered source/target comparison.

## Root Cause

BabelDOC first requested multi-paragraph JSON translation. The configured model
returned truncated JSON for some batches. BabelDOC then made individual fallback
requests, but the model returned an empty string for 18 paragraphs. The existing
post-translation path accepted that string, replaced `paragraph.unicode` with an
empty value, replaced its compositions with an empty list, and caused the PDF
renderer to erase the original text without drawing a translation.

## Architecture

### Translation acceptance gate

The local BabelDOC compatibility layer will wrap paragraph post-processing with a
single acceptance gate. The gate receives the source text, proposed target text,
and parsed placeholders. It returns a structured result containing acceptance,
failure reasons, and source/target facts used by the audit.

A target is rejected when any of these conditions apply:

1. The source is non-empty but the target is empty or whitespace-only.
2. The target is truncated or falls outside conservative length bounds.
3. Formula/style placeholders required by the source are missing, duplicated, or
   unexpectedly introduced. As a final content-first fallback, malformed
   rich-text-only style markers may be removed and the paragraph rendered with
   its base style after formula and protected-content validation still passes.
4. Protected tokens such as URLs, DOI values, citation ranges, and meaningful
   numeric values are lost.
5. A substantial English source is returned materially unchanged when Chinese is
   the configured target language.

The acceptance gate must execute before BabelDOC mutates paragraph Unicode or
compositions. Rejected output cannot alter the source paragraph.

### Targeted retry

The existing batch and individual attempts remain the first two attempts. When an
individual result is rejected, the local compatibility layer splits the original
paragraph at sentence boundaries, with a hard size ceiling for sentences that are
still too long. Each non-empty chunk is translated independently using the normal
model API and validated independently. Accepted chunks are joined in source order
and checked again as one paragraph before rendering.

Retries are bounded. The implementation will not loop indefinitely or silently
consume unlimited model requests.

### Fail-safe preservation and job status

If retry translation still fails, the original paragraph Unicode and composition
objects remain untouched. The failure is added to a document-level quality audit
with page, paragraph identifier, source preview, attempts, and rejection reasons.

The backend may produce diagnostic artifacts, but a document with unresolved
translatable paragraphs must be reported as completed with warnings or failed;
it must not be reported as an unqualified successful translation. The API response
and task log will expose the unresolved count.

Reference entries intentionally excluded from translation do not count as
unresolved. Pure formulas, numbers, metadata explicitly skipped by BabelDOC, and
other non-translatable paragraphs also do not count.

## Completeness Audit

After translation and before delivery, the backend will read BabelDOC's paragraph
tracking data and the local acceptance results. It will calculate:

- attempted paragraphs;
- accepted translated paragraphs;
- intentionally preserved paragraphs, including references;
- rejected and recovered paragraphs;
- unresolved paragraphs;
- non-empty source paragraphs with empty targets;
- protected-token mismatches.

The hard delivery invariant is zero non-empty source paragraphs with an empty
rendered replacement. The successful-translation invariant is zero unresolved
translatable paragraphs.

Automated comparison cannot prove literary or domain-level semantic perfection.
It will strictly prove content presence and structural fidelity, and flag suspect
translations for retry rather than accepting them silently.

## Testing

Unit tests will reproduce the observed failure by passing an empty individual
fallback result and verify that the original paragraph and its compositions are
unchanged. Further tests cover whitespace-only results, malformed/truncated
results, placeholder loss, protected-token loss, sentence-aligned retry, retry
success, retry exhaustion, references excluded from unresolved counts, and audit
status propagation.

Integration verification will:

1. Run the complete Python and TypeScript test suites and production build.
2. Translate the specified 17-page Alatise and Hancke paper through the configured
   third-party API.
3. Assert zero empty replacements and zero unresolved translatable paragraphs.
4. Confirm the reference heading and entries were never submitted for translation.
5. Confirm the table translation and table grid are present.
6. Render all translated pages and compare text-bearing source regions against
   target regions for unexpected blank areas.
7. Retain the original input PDF and write new validated mono and dual output files
   without overwriting it.

## Non-goals

- Replacing the user's selected translation provider.
- Adding a second reviewer model.
- Claiming automatic proof of nuanced semantic accuracy.
- Translating reference headings or reference entries.
