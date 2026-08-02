# Translation Batch Efficiency and Reliability Design

## Status and scope

- Date: 2026-08-02
- Status: approved direction, written design pending final user review
- Scope: the existing translation batching, structured-output envelope, fallback
  scheduling, Gemini token estimation, unresolved-paragraph delivery behavior,
  audit fields, and Gemini Lite price selection

This is deliberately a narrow change. Existing translation instructions, OCR,
table extraction, image handling, paragraph layout, font handling, and PDF render
logic remain unchanged unless a regression test proves that the batching change
directly breaks them.

Images are not translated. Existing scanned-document text OCR and eligible
image-table OCR behavior remain as-is.

## Problem statement

The five-page Gemini run `7d2d8f0dd60341d1b5f05898996f9cdb` completed 39
audited model requests: 19 initial batches and 20 fallbacks. Its structured
batch protocol repeated long stable paragraph IDs in the input, JSON schema, and
model output. Together with repeated JSON field names, this produced a much
larger protocol envelope than the source text itself. The run reported 23,337
batch input tokens for only 3,875 estimated source tokens and returned 17,347
visible batch-output tokens for 6,513 translated characters.

The Gemini adapter also calls the provider's remote `countTokens` endpoint before
generation. Those physical HTTP calls are not translation requests, do not
appear in the translation audit or request budget, and omit the response schema
from their count. They therefore increase traffic without providing a complete
reservation estimate.

Finally, a locally rejected item can currently cause one-request-per-paragraph
fallback behavior. A single unresolved paragraph then causes the whole task to
fail at 100%, even though retaining the source paragraph would produce a usable,
content-complete PDF.

## Goals

1. Preserve the current translation instruction text and translation-quality
   constraints.
2. Reduce structured-protocol input and output overhead without removing stable
   local provenance.
3. Increase initial batch utilization while keeping bounded structured output.
4. Eliminate unbudgeted remote token-count requests.
5. Recover only failed items, but group multiple failed items into recovery
   batches instead of creating one HTTP request per paragraph.
6. Prevent isolated model-output defects from failing an otherwise usable PDF at
   100% progress.
7. Keep every attempted source paragraph non-empty in the delivered document.
8. Make the request audit explain batching efficiency and every recovery request.
9. Use the correct price snapshot when the Gemini model field is changed to the
   supported Lite model.

## Non-goals

- Rewriting BabelDOC extraction, OCR, tables, layout, or rendering.
- Translating text inside ordinary figures or images.
- Changing the prose translation prompt merely to save tokens.
- Adding semantic review by a second model.
- Claiming that authentication, provider outages, exhausted budgets, or invalid
  input PDFs can never fail.
- Sending a real provider request during implementation or offline verification.

## Invariants

- Full stable paragraph IDs remain the local source of truth for provenance,
  attempts, audit, and final replacement matching.
- Full stable IDs are never included in a model batch payload or response schema.
- The prose translation instructions remain semantically and textually unchanged;
  only the machine-readable input/output envelope changes.
- A model response cannot mutate a paragraph until its local batch ID, target
  text, and existing content-quality checks pass.
- Each paragraph keeps the existing maximum of two billable exposures.
- Every physical generation request passes through `TranslationRuntime`.
- Token estimation performs no network I/O.
- Unresolved isolated paragraphs retain their complete source text and produce a
  delivery warning, not an end-of-job failure.
- Authentication failures, persistent provider failures, budget refusal, OCR
  safety refusal, and PDF generation/integrity failures remain task failures.

## Compact batch protocol

### Local alias map

For each request, the batch planner creates short decimal aliases in request
order:

```text
0 -> part-001/page-001/paragraph-000
1 -> part-001/page-001/paragraph-001
2 -> part-001/page-002/table-000/block-003
```

The alias map exists only in memory for response validation. Existing audit
records continue to store the full stable paragraph IDs, but those IDs are not
repeated in prompt or schema content. A new request gets a new independent alias
map.

### Request and response envelope

The existing translation instruction body is followed by a compact input object:

```json
{ "p": [{ "i": "0", "s": "source paragraph" }] }
```

The required output is:

```json
{ "t": [{ "i": "0", "t": "translated paragraph" }] }
```

The schema keeps `additionalProperties: false`, requires both compact fields,
and restricts `i` to the request's short aliases. This retains stable matching
while removing long IDs and repeated descriptive keys from both directions.

Local validation still computes missing, unknown, and duplicate aliases. Schema
validation alone is not treated as proof that every alias appears exactly once.

## Batch planning

Initial batches use:

```text
target source tokens: 2400
maximum source tokens: 3200
maximum paragraphs: 40
```

The existing local paragraph token estimates determine boundaries. A batch ends
at the first applicable limit. Page, paragraph, table, and provenance objects are
not otherwise changed.

`max_output_tokens` is derived conservatively from the source estimate plus the
compact response envelope, bounded by the configured provider limit. It must not
be reduced below the current safe minimum for the same amount of source text.

## Recovery behavior

The response validator separates failure classes:

- valid aliases: accepted independently;
- unknown aliases: discarded;
- duplicate aliases: the corresponding source items enter recovery;
- missing aliases: only missing items enter recovery;
- content-quality rejection: only rejected items enter recovery;
- malformed or truncated whole JSON: all affected items enter recovery.

Failed items are scheduled as compact **recovery batches** using the same planner
and the paragraph's second billable exposure. A malformed 40-item batch is split
into smaller recovery batches; it is not expanded directly into 40 individual
requests. A single-paragraph request is used only when one item remains or its
size prevents safe grouping.

No 700/350 cascading retry chain is introduced. Existing paragraph content
validation remains unchanged. After the second exposure, an unresolved item is
preserved as source text and recorded as a warning.

## Completion semantics

The completeness audit continues to reject empty replacement content. Its final
classification changes as follows:

- zero unresolved items: completed normally;
- at most three unresolved items, also no more than 1% of attempted items, whose
  complete source text was preserved: completed with warnings and deliverable
  artifacts;
- unresolved items above either warning threshold: failed as a systematic
  translation failure;
- any empty replacement, missing source preservation, corrupt output, global
  provider/configuration failure, or safety/budget abort: failed.

This avoids a 100%-progress task failure for one provider output defect while
never silently erasing content. The task summary records unresolved IDs, reasons,
and source-preservation status.

## Network-free token estimation

`GeminiInteractionsAdapter.count_tokens()` no longer calls
`client.models.count_tokens()`. All adapters use a conservative local estimate
over the complete serialized request components:

- unchanged instruction text;
- compact paragraph JSON;
- structured-output schema;
- response-envelope allowance;
- configured safety margin.

This estimate is used only for batching and worst-case budget reservation. Actual
provider usage remains authoritative for settlement and cost reporting.

## Audit additions

Each request record adds:

```json
{
  "request_phase": "initial|recovery",
  "batch_item_count": 0,
  "source_token_estimate": 0,
  "serialized_input_bytes": 0,
  "schema_bytes": 0,
  "protocol_overhead_bytes": 0,
  "batch_fill_ratio": 0.0,
  "recovery_reason_counts": {},
  "remote_token_count_request": false
}
```

The document summary adds initial batch count, recovery batch count, recovered
item count, preserved-source warning count, and total protocol overhead. Existing
usage and price fields remain intact.

## Gemini Lite UI and pricing

The plugin's Gemini row continues to accept connection overrides. With the
official native Gemini adapter:

- Base URL is left blank because the SDK supplies the official endpoint;
- Model is set to `gemini-3.1-flash-lite`;
- API key is unchanged.

The server selects a captured price profile by the resolved model name so that a
UI model override does not retain the Gemini 3.6 Flash estimate. The initial
supported mapping is limited to known Gemini model IDs used by this project;
unknown model IDs require explicit server pricing instead of silently inheriting
another model's price.

## Test strategy

All tests use fake adapters and local fixtures.

### Protocol tests

- Full stable IDs do not appear in serialized model input or schema.
- Every short alias maps back to exactly one stable ID.
- The existing prose instruction body is unchanged.
- Missing, unknown, duplicate, reordered, and malformed outputs select the
  correct recovery set.
- A recovery set with multiple items is grouped rather than sent item-by-item.

### Network and budget tests

- Planning and execution never call a provider token-count endpoint.
- Every generation call appears in the request audit and budget.
- Actual usage settles the conservative reservation.
- Two-exposure limits remain enforced across initial and recovery batches.

### Reliability tests

- An empty or invalid candidate cannot replace non-empty source content.
- A locally rejected item is recovered without re-translating valid siblings.
- An unrecoverable isolated item preserves source text and completes with warning.
- Global authentication, budget, provider, and PDF failures still fail clearly.

### Historical-profile acceptance

For the archived five-page Borges profile containing 358 attempted paragraphs:

- the initial plan contains at most 10 batches;
- a fully valid fake response produces zero recovery requests;
- no remote token-count calls occur;
- protocol bytes are at least 50% lower than the legacy full-ID envelope for an
  equivalent 32-item fixture;
- every attempted paragraph ends as translated or explicitly source-preserved;
- no empty replacement is accepted.

The full Python and TypeScript suites and production build must pass. No real
five-page or full-document API test is run until separately authorized.

## Delivery order

1. Add failing protocol, planner, recovery, warning-delivery, pricing, and audit
   tests.
2. Implement local aliasing and compact serialization without changing the prose
   prompt.
3. Increase planner limits and implement grouped recovery batches.
4. Remove remote token counting and extend request audits.
5. Add isolated-unresolved warning delivery and model-aware Gemini pricing.
6. Run offline suites and historical-profile acceptance.
7. Present results and request separate authorization for a five-page canary.
