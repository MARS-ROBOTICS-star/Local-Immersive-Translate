# Translation Completeness Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent empty or structurally incomplete model responses from erasing PDF paragraphs, recover rejected translations with bounded chunked retries, and reject unqualified successful jobs with unresolved translatable content.

**Architecture:** Add a provider-independent acceptance and retry module, integrate it at BabelDOC's pre-mutation paragraph boundary through the existing compatibility patch, and audit tracking plus local recovery events before the server marks a job successful. Preserve the original paragraph objects transactionally until a validated target is ready.

**Tech Stack:** Python 3.12, BabelDOC 0.6.4 compatibility monkeypatches, `unittest`, Zotero local backend, existing OpenAI-compatible translator.

## Global Constraints

- A non-empty source paragraph may never be replaced by an empty target.
- References and bibliography headings and entries remain original and are excluded from unresolved counts.
- Formula/style placeholders, URLs, DOI values, citation ranges, and meaningful numeric values must survive accepted translation.
- Retries are bounded and sentence-aligned; no unbounded API requests.
- The original PDF is never overwritten.
- Successful translation requires zero unresolved translatable paragraphs.

---

### Task 1: Translation acceptance and chunking primitives

**Files:**
- Create: `local_babeldoc_server/translation_quality.py`
- Create: `tests/test_translation_quality.py`

**Interfaces:**
- Produces: `validate_translation(source: str, target: str, target_language: str, placeholders: Iterable[str] = ()) -> ValidationResult`
- Produces: `split_translation_chunks(text: str, max_chars: int = 700) -> list[str]`
- Produces: immutable `ValidationResult(accepted: bool, reasons: tuple[str, ...])`

- [ ] **Step 1: Write failing validation tests**

  Cover empty and whitespace targets, missing placeholders, URLs, DOI values,
  citation ranges and numbers, unchanged substantial English, truncated output,
  valid Chinese translation, and sentence-aligned chunk order.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run: `python3 -m unittest tests.test_translation_quality -v`

  Expected: import failure because `translation_quality.py` does not exist.

- [ ] **Step 3: Implement the smallest acceptance and chunking module**

  Extract protected tokens with compiled regular expressions and compare source
  and target multisets. Reject empty, unchanged, structurally lossy, and clearly
  truncated targets. Split on sentence punctuation first and hard-split only
  sentences exceeding the ceiling.

- [ ] **Step 4: Run focused and complete tests**

  Run: `python3 -m unittest tests.test_translation_quality -v`

  Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

### Task 2: Transactional paragraph guard and bounded recovery

**Files:**
- Modify: `local_babeldoc_server/babeldoc_compat.py`
- Modify: `tests/test_babeldoc_compat.py`

**Interfaces:**
- Consumes: `validate_translation` and `split_translation_chunks`
- Produces: compatibility wrappers for `ILTranslator.post_translate_paragraph`
  and `ILTranslator.translate_paragraph`
- Produces: `translation_config.local_translation_quality`, containing counters
  and serializable recovery/unresolved records

- [ ] **Step 1: Write a failing empty-result regression test**

  Build a fake BabelDOC translator whose individual fallback returns `""` and
  assert that paragraph Unicode and the original composition object identity are
  unchanged after translation.

- [ ] **Step 2: Run the regression test and verify RED**

  Run: `python3 -m unittest tests.test_babeldoc_compat.BabeldocCompatTest.test_empty_translation_never_replaces_source -v`

  Expected: paragraph Unicode becomes empty or the test lacks the required patched method.

- [ ] **Step 3: Add pre-mutation validation**

  Wrap `post_translate_paragraph`; validate before invoking BabelDOC's original
  implementation. On rejection, set tracker output to `None`, record rejection,
  and return `False` without mutating the paragraph.

- [ ] **Step 4: Write failing recovery tests**

  Verify sentence chunks are translated in order, a recovered combined target is
  applied once, placeholder loss is rejected, and retry exhaustion leaves the
  original paragraph intact with one unresolved audit record.

- [ ] **Step 5: Run recovery tests and verify RED**

  Run: `python3 -m unittest tests.test_babeldoc_compat -v`

- [ ] **Step 6: Implement bounded chunk recovery**

  After BabelDOC's individual attempt returns, inspect the local rejection marker.
  Translate chunks using `generate_prompt_for_llm`, validate each chunk and the
  joined result, then call the original post-translation method only for an
  accepted combined result. Use at most two chunk sizes, 700 then 350 characters.

- [ ] **Step 7: Run focused and complete tests**

  Run: `python3 -m unittest tests.test_babeldoc_compat -v`

  Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

### Task 3: Task-level audit and status enforcement

**Files:**
- Modify: `local_babeldoc_server/server.py`
- Modify: `tests/test_local_babeldoc_server.py`
- Modify: `local_babeldoc_server/config.example.json`
- Modify: `local_babeldoc_server/README.md`

**Interfaces:**
- Produces: `TranslationAudit` summary parsed from `translate_tracking.json` and
  `translation_config.local_translation_quality`
- Produces: `audit_translation_completion(working_dir: Path, local_quality: Any) -> TranslationAudit`
- Produces: job message containing recovered/unresolved counts

- [ ] **Step 1: Write failing audit tests**

  Use temporary tracking JSON to verify references are excluded, non-empty input
  plus empty output is unresolved, local recovered items are counted, and a job
  with unresolved items cannot transition to `success`.

- [ ] **Step 2: Run server tests and verify RED**

  Run: `python3 -m unittest tests.test_local_babeldoc_server -v`

- [ ] **Step 3: Implement audit parsing and status enforcement**

  Audit immediately after BabelDOC returns and before `update_job(status="success")`.
  Raise a descriptive completeness error when unresolved count is non-zero. Keep
  generated PDFs for diagnostics but do not expose them as successful downloads.

- [ ] **Step 4: Document behavior and configuration**

  Document the acceptance checks, bounded retry sizes, failure status, and audit
  fields. Add enabled-by-default `enable_translation_quality_guard`,
  `translation_retry_chunk_sizes` set to `[700, 350]`, and
  `fail_on_unresolved_translation` without model credentials.

- [ ] **Step 5: Run server and complete tests**

  Run: `python3 -m unittest tests.test_local_babeldoc_server -v`

  Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

### Task 4: Build, deploy, and formal PDF verification

**Files:**
- No additional repository files anticipated beyond Tasks 1-3.
- Deploy exact backend copies under `/home/lbz/Local-Immersive-Translate/local_babeldoc_server/`.
- Create new validation PDFs under `/home/lbz/Zotero/storage/NSYVJWHZ/`.

**Interfaces:**
- Consumes: Zotero's current third-party translation model configuration.
- Produces: validated mono and dual PDFs with distinct filenames.

- [ ] **Step 1: Run static and production checks**

  Run: `python3 -m py_compile local_babeldoc_server/*.py`

  Run: `PATH=/home/lbz/.nvm/versions/node/v24.14.0/bin:$PATH ./node_modules/.bin/tsc --noEmit`

  Run: `PATH=/home/lbz/.nvm/versions/node/v24.14.0/bin:$PATH npm run build`

- [ ] **Step 2: Deploy exact backend files and verify byte identity**

  Copy the changed local backend files to the installed backend, then use `cmp -s`
  for every deployed file.

- [ ] **Step 3: Re-run the specified 17-page PDF through the configured API**

  Use the existing secure validation runner without printing API credentials.
  Write new mono and dual filenames; do not overwrite the original or earlier
  diagnostic translation.

- [ ] **Step 4: Perform source/target content comparison**

  Assert zero non-empty source/empty target tracking rows, zero unresolved audit
  records, zero reference-like model inputs, preserved protected-token counts,
  translated table text, and original reference text.

- [ ] **Step 5: Render and inspect all pages**

  Render every mono page with `pdftoppm`. Confirm no renderer errors and compare
  source text-region occupancy with target pages to detect new blank regions.

- [ ] **Step 6: Run final regression suite and repository checks**

  Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

  Run: `git diff --check`

  Run: `git status --short`

- [ ] **Step 7: Commit the implementation**

  Commit only the scoped source, tests, configuration, and documentation changes
  with message `fix: prevent incomplete PDF translations`.
