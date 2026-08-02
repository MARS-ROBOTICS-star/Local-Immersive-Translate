# Translation Batch Efficiency and Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce structured-translation token and request amplification while preserving the existing translation instructions and delivering usable PDFs for a small number of source-preserved paragraph warnings.

**Architecture:** Keep BabelDOC's extraction and layout pipeline intact. Compact batch-local aliases and grouped recovery live at the existing `RuntimeBackedTranslator` boundary; the central runtime remains the only generation gateway, and the server keeps responsibility for final completeness disposition and model-aware pricing.

**Tech Stack:** Python 3.11+, `unittest`, BabelDOC 0.6.4 compatibility hooks, Google Gen AI Interactions v1, TypeScript/Zotero plugin build.

## Global Constraints

- Preserve the existing prose translation instruction body; only the machine-readable batch envelope may change.
- Use target 2400 source tokens, maximum 3200 source tokens, and maximum 40 paragraphs per initial batch.
- Full stable paragraph IDs remain local and must not appear in provider input or schema.
- Each paragraph retains at most two billable exposures.
- Token estimation must perform no network I/O.
- Ordinary images are never OCRed or translated by this change.
- Do not modify existing OCR, table extraction, layout, font, or render behavior.
- At most three unresolved paragraphs and at most 1% of attempted paragraphs may be delivered with warnings, and only when their complete source text is preserved.
- Do not issue real provider requests during implementation or offline verification.

---

### Task 0: Preserve the already-verified prerequisite fixes

**Files:**
- Verify: `local_babeldoc_server/babeldoc_compat.py`
- Verify: `local_babeldoc_server/config.example.json`
- Verify: `local_babeldoc_server/pdf_output_quality.py`
- Verify: `local_babeldoc_server/server.py`
- Verify: `local_babeldoc_server/translation_runtime.py`
- Verify: `local_babeldoc_server/translator_adapters.py`
- Verify: `tests/test_babeldoc_compat.py`
- Verify: `tests/test_local_babeldoc_server.py`
- Verify: `tests/test_pdf_output_quality.py`
- Verify: `tests/test_server_translation_runtime.py`
- Verify: `tests/test_translation_runtime.py`
- Verify: `tests/test_translator_adapters.py`

**Interfaces:**
- Consumes: the current dirty-tree fixes for the one-empty-paragraph Gemini failure, source-render audit, OCR provenance, and numeric-token fallback.
- Produces: a clean prerequisite commit on `agent/pdf-translation-fixes` before new batching behavior is introduced.

- [ ] **Step 1: Run the complete Python baseline**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: 129 tests pass, with only the previously documented optional-environment skips.

- [ ] **Step 2: Run the production plugin build baseline**

Run:

```bash
pnpm build
```

Expected: the Zotero plugin build and `tsc --noEmit` both exit 0.

- [ ] **Step 3: Review the prerequisite diff for unrelated files**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Expected: only the listed prerequisite implementation/tests plus this plan are dirty, and `git diff --check` exits 0.

- [ ] **Step 4: Commit the prerequisite fixes separately**

```bash
git add local_babeldoc_server/babeldoc_compat.py local_babeldoc_server/config.example.json local_babeldoc_server/pdf_output_quality.py local_babeldoc_server/server.py local_babeldoc_server/translation_runtime.py local_babeldoc_server/translator_adapters.py tests/test_babeldoc_compat.py tests/test_local_babeldoc_server.py tests/test_pdf_output_quality.py tests/test_server_translation_runtime.py tests/test_translation_runtime.py tests/test_translator_adapters.py
git commit -m "fix: preserve pdf translation integrity"
```

### Task 1: Replace full stable IDs with compact batch aliases

**Files:**
- Modify: `local_babeldoc_server/translation_batching.py:58-247`
- Modify: `local_babeldoc_server/translation_runtime.py:429-548`
- Test: `tests/test_translation_batching.py`
- Test: `tests/test_translation_runtime.py`

**Interfaces:**
- Consumes: `rewrite_babeldoc_batch_prompt(prompt: str, stable_ids: tuple[str, ...])` and BabelDOC's existing input marker.
- Produces: `RewrittenBatchPrompt.alias_by_stable_id`, `stable_id_by_alias`, `source_by_stable_id`, compact `p/i/s` input, compact `t/i/t` output schema, and stable-ID response mapping.

- [ ] **Step 1: Write failing compact-protocol tests**

Replace the legacy stable-ID prompt assertions with behavior assertions:

```python
def test_prompt_rewrite_uses_short_aliases_without_changing_instruction_prefix(self):
    prefix = "translation rules and glossary"
    upstream = prefix + "\n\n## Here is the input:\n\n" + json.dumps([
        {"id": 0, "input": "<style id='1'>First</style>"},
        {"id": 1, "input": "Second {v1}"},
    ])

    rewritten = rewrite_babeldoc_batch_prompt(upstream, EXPECTED)
    payload = json.loads(rewritten.prompt.rsplit("BATCH_INPUT_JSON:\n", 1)[1])
    serialized_contract = json.dumps(
        {"prompt": rewritten.prompt, "schema": rewritten.response_format},
        ensure_ascii=False,
    )

    self.assertEqual(rewritten.instruction_prefix, prefix)
    self.assertEqual(payload, {"p": [
        {"i": "0", "s": "<style id='1'>First</style>"},
        {"i": "1", "s": "Second {v1}"},
    ]})
    self.assertNotIn(EXPECTED[0], serialized_contract)
    self.assertNotIn(EXPECTED[1], serialized_contract)
    self.assertEqual(rewritten.stable_id_by_alias, {"0": EXPECTED[0], "1": EXPECTED[1]})
```

Add a response mapping test with reordered compact aliases:

```python
def test_compact_response_maps_aliases_back_to_stable_ids(self):
    rewritten = rewrite_babeldoc_batch_prompt(make_upstream_prompt(), EXPECTED)
    result = validate_rewritten_batch_response(
        json.dumps({"t": [
            {"i": "1", "t": "第二段"},
            {"i": "0", "t": "第一段"},
        ]}),
        rewritten,
        target_language="zh",
    )

    self.assertEqual(result.valid_translations, {
        EXPECTED[0]: "第一段",
        EXPECTED[1]: "第二段",
    })
    self.assertEqual(result.recovery_ids, ())
```

- [ ] **Step 2: Run the compact-protocol tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_translation_batching.TranslationBatchingTest.test_prompt_rewrite_uses_short_aliases_without_changing_instruction_prefix tests.test_translation_batching.TranslationBatchingTest.test_compact_response_maps_aliases_back_to_stable_ids -v
```

Expected: FAIL because `instruction_prefix`, compact aliases, and `validate_rewritten_batch_response` do not exist.

- [ ] **Step 3: Implement compact alias serialization and validation**

Update the batching dataclasses and schema around these exact interfaces:

```python
@dataclass(frozen=True, slots=True)
class RewrittenBatchPrompt:
    prompt: str
    instruction_prefix: str
    expected_stable_ids: tuple[str, ...]
    stable_id_by_alias: Mapping[str, str]
    alias_by_stable_id: Mapping[str, str]
    source_by_stable_id: Mapping[str, str]
    response_format: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StableBatchValidation:
    valid_translations: Mapping[str, str]
    recovery_ids: tuple[str, ...]
    reason_by_stable_id: Mapping[str, tuple[str, ...]]
    parse_error: str | None = None
```

`build_translation_schema()` must emit a closed `{"t": [{"i", "t"}]}` schema whose alias enum is `['0', '1', ...]`. `rewrite_babeldoc_batch_prompt()` must preserve `prompt[:marker_index].rstrip()` byte-for-byte as `instruction_prefix`, append only the compact contract text, and serialize the source as `{"p":[{"i":"0","s":"..."}]}` without indentation.

- [ ] **Step 4: Run the compact-protocol tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_translation_batching -v
```

Expected: all batching tests pass.

- [ ] **Step 5: Add and pass a protocol-overhead regression test**

Add a literal 32-item legacy-vs-compact fixture that compares UTF-8 byte lengths while keeping identical source strings. Assert:

```python
self.assertLessEqual(compact_protocol_bytes, legacy_protocol_bytes * 0.5)
```

Run:

```bash
python3 -m unittest tests.test_translation_batching.TranslationBatchingTest.test_compact_protocol_cuts_legacy_overhead_by_half -v
```

Expected: PASS, with no assertion against the prose prompt wording.

- [ ] **Step 6: Commit compact protocol support**

```bash
git add local_babeldoc_server/translation_batching.py local_babeldoc_server/translation_runtime.py tests/test_translation_batching.py tests/test_translation_runtime.py
git commit -m "perf: compact structured translation batches"
```

### Task 2: Enlarge batches and prove the historical request plan

**Files:**
- Modify: `local_babeldoc_server/translation_batching.py:18-55`
- Modify: `local_babeldoc_server/babeldoc_compat.py:824-844`
- Modify: `local_babeldoc_server/server.py:132-142,1080-1089`
- Modify: `local_babeldoc_server/config.example.json`
- Test: `tests/test_translation_batching.py`
- Test: `tests/test_server_translation_runtime.py`

**Interfaces:**
- Consumes: `partition_batch_indices(token_counts, target_tokens, max_tokens, max_paragraphs)`.
- Produces: default limits 2400/3200/40 and a deterministic planner for the 358-paragraph five-page profile.

- [ ] **Step 1: Write failing default-limit and profile tests**

```python
def test_partition_uses_2400_3200_and_40_defaults(self):
    batches = partition_batch_indices([10] * 81)
    self.assertEqual(batches, ((0, 40), (40, 80), (80, 81)))


def test_five_page_profile_needs_at_most_ten_initial_batches(self):
    token_counts = [11] * 358
    batches = partition_batch_indices(token_counts)
    self.assertLessEqual(len(batches), 10)
    self.assertEqual(sum(end - start for start, end in batches), 358)
```

Update the server configuration assertion to expect 2400, 3200, and 40.

- [ ] **Step 2: Run the planner tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_translation_batching.TranslationBatchingTest.test_partition_uses_2400_3200_and_40_defaults tests.test_translation_batching.TranslationBatchingTest.test_five_page_profile_needs_at_most_ten_initial_batches tests.test_server_translation_runtime.ServerTranslationRuntimeTest.test_default_config_contains_hard_budget_batch_and_ocr_limits -v
```

Expected: FAIL with the current 1000/1500/16 defaults.

- [ ] **Step 3: Change only the batching defaults and config plumbing**

Set:

```python
target_tokens: int = 2400
max_tokens: int = 3200
max_paragraphs: int = 40
```

Update `DEFAULT_CONFIG`, `config.example.json`, and existing `getattr` fallbacks in the compatibility wrapper to the same values. Do not change `max_output_tokens=4096` or the translation prompt.

- [ ] **Step 4: Run planner and compatibility tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_translation_batching tests.test_server_translation_runtime tests.test_babeldoc_compat -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit enlarged batching**

```bash
git add local_babeldoc_server/translation_batching.py local_babeldoc_server/babeldoc_compat.py local_babeldoc_server/server.py local_babeldoc_server/config.example.json tests/test_translation_batching.py tests/test_server_translation_runtime.py tests/test_babeldoc_compat.py
git commit -m "perf: enlarge safe translation batches"
```

### Task 3: Recover invalid items in grouped second-attempt batches

**Files:**
- Modify: `local_babeldoc_server/translation_batching.py`
- Modify: `local_babeldoc_server/translation_runtime.py:312-548`
- Modify: `local_babeldoc_server/babeldoc_compat.py:1035-1118`
- Modify: `local_babeldoc_server/server.py:1060-1090`
- Test: `tests/test_translation_batching.py`
- Test: `tests/test_translation_runtime.py`
- Test: `tests/test_babeldoc_compat.py`

**Interfaces:**
- Consumes: compact `RewrittenBatchPrompt`, `validate_translation()`, central `TranslationRuntime.request()`, and the existing two-exposure budget.
- Produces: `build_recovery_batches()`, `RuntimeBackedTranslator.batch_item_outcome(stable_id)`, grouped `fallback` requests, and source-preserved exhaustion without a third request.

- [ ] **Step 1: Write failing recovery selection tests**

Cover reordered valid items, missing alias, duplicate alias, unknown alias, empty text, protected-token rejection, and malformed JSON. The key grouped behavior test is:

```python
def test_three_failed_items_use_one_grouped_recovery_request(self):
    adapter = SequencedAdapter([
        compact_response(valid_ids=("0",), missing_ids=("1", "2", "3")),
        compact_response(valid_ids=("0", "1", "2"), translations=("乙", "丙", "丁")),
    ])
    translator = make_runtime_translator(adapter, stable_ids=FOUR_IDS)

    output = translator.llm_translate(
        make_upstream_prompt(FOUR_SOURCES),
        rate_limit_params={"request_json_mode": True, "paragraph_token_count": 80},
    )

    self.assertEqual(len(adapter.sent_requests), 2)
    self.assertEqual(json.loads(output), [
        {"id": 0, "output": "甲"},
        {"id": 1, "output": "乙"},
        {"id": 2, "output": "丙"},
        {"id": 3, "output": "丁"},
    ])
```

- [ ] **Step 2: Run the grouped recovery test and verify RED**

Run:

```bash
python3 -m unittest tests.test_translation_runtime.TranslationRuntimeTest.test_three_failed_items_use_one_grouped_recovery_request -v
```

Expected: FAIL because failed items are currently blanked and handled one paragraph at a time.

- [ ] **Step 3: Implement recovery planning**

Add:

```python
def build_recovery_batches(
    rewritten: RewrittenBatchPrompt,
    recovery_ids: tuple[str, ...],
    *,
    split_full_batch: bool,
) -> tuple[RewrittenBatchPrompt, ...]:
```

Use the original `instruction_prefix` and source text. For a whole malformed batch with more than one item, cap each recovery batch at `ceil(len(recovery_ids) / 2)` items. Otherwise apply the normal 2400/3200/40 limits to failed items only.

In `RuntimeBackedTranslator`, send each recovery group directly through `runtime.request()` with:

```python
RequestContext(
    request_category="fallback",
    request_phase="recovery",
    paragraph_ids=recovery_stable_ids,
    semantic_attempt_number=2,
    transport_attempt_number=1,
    source_token_estimate=recovery_source_tokens,
    paragraph_count=len(recovery_stable_ids),
)
```

Validate recovery output once. Merge accepted recovery items with accepted initial items. Mark remaining IDs as `source_preserved` in thread-local batch state and return their original source text in the BabelDOC-compatible output.

- [ ] **Step 4: Prevent BabelDOC from creating a third request**

When the central quality guard is enabled, set BabelDOC's `disable_same_text_fallback=True` so unchanged source-preservation reaches the compatibility post-translation gate instead of BabelDOC's executor fallback.

At the beginning of `wrapped_post_translate()`, resolve the paragraph's stable ID and check:

```python
if batch_item_outcome(stable_id) == "source_preserved":
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
```

Extend `_record_quality_event()` with a keyword-only `source_preserved: bool = False` and persist that boolean in unresolved records.

- [ ] **Step 5: Run recovery, exposure, and compatibility tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_translation_batching tests.test_translation_runtime tests.test_babeldoc_compat -v
```

Expected: all pass; multiple invalid siblings produce one grouped recovery request, and exhausted paragraphs do not trigger a third request.

- [ ] **Step 6: Commit grouped recovery**

```bash
git add local_babeldoc_server/translation_batching.py local_babeldoc_server/translation_runtime.py local_babeldoc_server/babeldoc_compat.py local_babeldoc_server/server.py tests/test_translation_batching.py tests/test_translation_runtime.py tests/test_babeldoc_compat.py
git commit -m "fix: recover failed batch items together"
```

### Task 4: Remove remote token counting and audit protocol overhead

**Files:**
- Modify: `local_babeldoc_server/translation_types.py:131-157`
- Modify: `local_babeldoc_server/translator_adapters.py:61-69,121-173`
- Modify: `local_babeldoc_server/translation_runtime.py:76-264`
- Modify: `local_babeldoc_server/translation_audit.py:23-238`
- Test: `tests/test_translator_adapters.py`
- Test: `tests/test_translation_runtime.py`
- Test: `tests/test_translation_audit.py`

**Interfaces:**
- Consumes: complete `AdapterRequest` serialization and actual provider usage settlement.
- Produces: network-free conservative token bounds and audit fields for phase, serialized bytes, schema bytes, protocol overhead, fill ratio, and recovery reasons.

- [ ] **Step 1: Write a failing no-network token estimate test**

```python
def test_gemini_count_tokens_is_local_and_includes_schema(self):
    client = SimpleNamespace(
        models=SimpleNamespace(count_tokens=Mock(side_effect=AssertionError("network")))
    )
    adapter = GeminiInteractionsAdapter(api_key="secret", model="gemini-3.1-flash-lite", client=client)
    request = AdapterRequest(
        model=adapter.model,
        input="abc",
        response_format={"type": "text", "schema": {"type": "object"}},
    )

    bound = adapter.count_tokens(request)

    self.assertGreater(bound, len("abc"))
    client.models.count_tokens.assert_not_called()
```

- [ ] **Step 2: Run it and verify RED**

Run:

```bash
python3 -m unittest tests.test_translator_adapters.TranslatorAdaptersTest.test_gemini_count_tokens_is_local_and_includes_schema -v
```

Expected: FAIL because the current adapter invokes `client.models.count_tokens`.

- [ ] **Step 3: Switch Gemini to the conservative local bound**

Implement:

```python
def count_tokens(self, request: AdapterRequest) -> int:
    return _conservative_local_token_bound(request)
```

Set Gemini's `supports_count_tokens=False` because no provider count endpoint is used. Keep the protocol method because `TranslationRuntime` consumes a local upper bound from every adapter.

- [ ] **Step 4: Write failing audit metric tests**

Extend `RequestContext` and `ApiCallRecord` with defaults for:

```python
request_phase: str = "initial"
serialized_input_bytes: int = 0
schema_bytes: int = 0
source_text_bytes: int = 0
protocol_overhead_bytes: int = 0
batch_fill_ratio: float = 0.0
recovery_reason_counts: Mapping[str, int] = field(default_factory=dict)
```

Assert an initial and recovery call serialize these values and that `UsageSummary` counts `initial_batch_request_count`, `recovery_batch_request_count`, and summed `protocol_overhead_bytes`.

- [ ] **Step 5: Implement and pass audit metric aggregation**

Compute serialized and schema UTF-8 sizes from the actual `AdapterRequest`, never storing the prompt or translation. Compute protocol overhead as:

```python
max(0, serialized_input_bytes + schema_bytes - source_text_bytes)
```

Pass `source_text_bytes` and recovery reasons from the batch rewrite result through `RequestContext`. Update JSONL replay with backward-compatible zero defaults for old rows.

Run:

```bash
python3 -m unittest tests.test_translator_adapters tests.test_translation_runtime tests.test_translation_audit -v
```

Expected: all pass and the token counter mock is never called.

- [ ] **Step 6: Commit local estimation and metrics**

```bash
git add local_babeldoc_server/translation_types.py local_babeldoc_server/translator_adapters.py local_babeldoc_server/translation_runtime.py local_babeldoc_server/translation_audit.py tests/test_translator_adapters.py tests/test_translation_runtime.py tests/test_translation_audit.py
git commit -m "fix: account for every translation request locally"
```

### Task 5: Deliver small source-preserved exceptions as warnings

**Files:**
- Modify: `local_babeldoc_server/server.py:277-413,1097-1181`
- Test: `tests/test_local_babeldoc_server.py:466-597`

**Interfaces:**
- Consumes: tracking output, local quality `source_preserved` records, runtime abort reason, and generated PDF paths.
- Produces: `TranslationAudit.source_preserved_count`, boolean warning disposition from `ensure_translation_complete()`, and a success job message that names warning counts.

- [ ] **Step 1: Write failing warning-threshold tests**

```python
def test_one_source_preserved_item_in_large_document_is_deliverable_warning(self):
    audit = TranslationAudit(
        attempted_count=358,
        unresolved_count=1,
        source_preserved_count=1,
        empty_replacement_count=0,
    )
    self.assertTrue(ensure_translation_complete(audit, fail_on_unresolved=True))


def test_warning_threshold_rejects_four_or_more_unresolved_items(self):
    audit = TranslationAudit(
        attempted_count=600,
        unresolved_count=4,
        source_preserved_count=4,
        empty_replacement_count=0,
    )
    with self.assertRaisesRegex(TranslationCompletenessError, "4 unresolved"):
        ensure_translation_complete(audit, fail_on_unresolved=True)


def test_warning_threshold_rejects_more_than_one_percent(self):
    audit = TranslationAudit(
        attempted_count=100,
        unresolved_count=2,
        source_preserved_count=2,
        empty_replacement_count=0,
    )
    with self.assertRaisesRegex(TranslationCompletenessError, "2 unresolved"):
        ensure_translation_complete(audit, fail_on_unresolved=True)
```

- [ ] **Step 2: Run warning tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_local_babeldoc_server.TranslationCompletionAuditTest -v
```

Expected: FAIL because `source_preserved_count` and warning disposition do not exist.

- [ ] **Step 3: Implement source-preservation counting and disposition**

Add `source_preserved_count: int = 0` to `TranslationAudit`. Count tracking entries whose non-empty target exactly equals the source and merge local unresolved records marked `source_preserved=True` without double counting.

Return `True` from `ensure_translation_complete()` only when all of these hold:

```python
audit.unresolved_count > 0
audit.unresolved_count == audit.source_preserved_count
audit.unresolved_count <= 3
audit.unresolved_count / max(1, audit.attempted_count) <= 0.01
abort_reason is None
audit.empty_replacement_count == 0
```

Return `False` for a clean audit. Preserve all existing hard-failure checks.

- [ ] **Step 4: Report warnings without changing the plugin status contract**

Capture the returned boolean in `_run_babeldoc()`. Keep `status="success"` and downloadable PDF paths, but emit:

```text
Translation completed with warnings: attempted=358, recovered=N, source_preserved=1
```

for warning delivery. Keep the current audit-passed message for clean delivery.

- [ ] **Step 5: Run completion and server tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_local_babeldoc_server tests.test_server_translation_runtime -v
```

Expected: all pass; budget aborts and non-preserved unresolved content still fail.

- [ ] **Step 6: Commit warning delivery**

```bash
git add local_babeldoc_server/server.py tests/test_local_babeldoc_server.py
git commit -m "fix: deliver isolated source-preserved warnings"
```

### Task 6: Apply the correct Gemini Lite price profile

**Files:**
- Modify: `local_babeldoc_server/server.py:40-100,764-856`
- Modify: `local_babeldoc_server/config.example.json`
- Test: `tests/test_server_translation_runtime.py`

**Interfaces:**
- Consumes: the resolved provider, model name, service tier, and existing `PricingSnapshot`.
- Produces: `resolve_model_pricing(model_cfg)` with an explicit stable-model price map and a matching runtime snapshot.

- [ ] **Step 1: Write a failing UI-model-override pricing test**

```python
def test_gemini_lite_ui_override_replaces_flash_pricing(self):
    config = deep_merge(DEFAULT_CONFIG, {
        "babeldoc": {"data_dir": self.temp_dir},
        "models": {"gemini-1": {
            "api_key": "secret",
            "model": "gemini-3.6-flash",
        }},
    })
    state = AppState(config)
    job = replace(make_job(), model_config={"model": "gemini-3.1-flash-lite"})

    runtime, resolved = state._create_translation_runtime(
        job,
        Path(self.temp_dir) / "working",
        adapter=OfflineAdapter(),
    )

    self.assertEqual(resolved["model"], "gemini-3.1-flash-lite")
    self.assertEqual(runtime.pricing.input_usd_per_million, Decimal("0.25"))
    self.assertEqual(runtime.pricing.output_usd_per_million, Decimal("1.50"))
    self.assertEqual(runtime.pricing.cached_input_usd_per_million, Decimal("0.025"))
```

- [ ] **Step 2: Run the pricing test and verify RED**

Run:

```bash
python3 -m unittest tests.test_server_translation_runtime.ServerTranslationRuntimeTest.test_gemini_lite_ui_override_replaces_flash_pricing -v
```

Expected: FAIL because the runtime retains Gemini 3.6 Flash prices.

- [ ] **Step 3: Implement explicit Gemini model price profiles**

Add a captured standard-tier mapping:

```python
GEMINI_STANDARD_PRICE_PROFILES = {
    "gemini-3.6-flash": ("1.5", "7.5", "0.15"),
    "gemini-3.1-flash-lite": ("0.25", "1.50", "0.025"),
}
```

Apply it after UI connection/model overrides and before constructing `PricingSnapshot`. Add `pricing_model` to the Gemini default/example configuration. If a Google model differs from `pricing_model` and has no profile, raise a configuration error rather than inheriting stale prices.

- [ ] **Step 4: Run pricing and server tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_server_translation_runtime tests.test_local_babeldoc_server -v
```

Expected: all pass; DeepSeek and other compatible providers retain their configured prices.

- [ ] **Step 5: Commit model-aware pricing**

```bash
git add local_babeldoc_server/server.py local_babeldoc_server/config.example.json tests/test_server_translation_runtime.py
git commit -m "fix: price Gemini Lite usage accurately"
```

### Task 7: Run complete offline regression and deploy the tested backend

**Files:**
- Verify: all files changed by Tasks 0-6
- Update if behavior changed: `local_babeldoc_server/README.md`
- Deploy after approval: `/home/lbz/Local-Immersive-Translate/local_babeldoc_server/`

**Interfaces:**
- Consumes: all task-level green tests and the existing local backend installation.
- Produces: a tested workspace build, historical-profile measurements, and byte-identical deployed backend source files ready for the user's manual five-page test.

- [ ] **Step 1: Run the full Python suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: all tests pass with no new skips or warnings.

- [ ] **Step 2: Run formatting and plugin build checks**

Run:

```bash
pnpm exec prettier --check local_babeldoc_server tests src
pnpm build
```

Expected: both commands exit 0.

- [ ] **Step 3: Run historical-profile acceptance without network access**

Run the 358-item synthetic five-page planner and the existing 600-paragraph Borges profile through fake adapters. Record:

```text
initial batches <= 10 for 358 items
initial batches <= 15 for 600 x 20-token items
remote count-token calls = 0
valid fake-output recovery requests = 0
empty replacements = 0
protocol overhead reduction >= 50%
```

Use a dedicated unittest method so these values remain regression-protected.

- [ ] **Step 4: Inspect final diff and audit prompt preservation**

Run:

```bash
git diff --check HEAD~6..HEAD
git status --short
```

Review `rewrite_babeldoc_batch_prompt()` and its test to confirm the instruction prefix is unchanged and no OCR/layout files changed except existing prerequisite code and the one compatibility fallback gate.

- [ ] **Step 5: Deploy only after requesting filesystem approval**

Copy the tested `local_babeldoc_server` source and configuration files to the configured local backend installation, excluding credentials, task outputs, virtual environments, caches, and user configuration. Compare SHA-256 hashes for every copied file.

- [ ] **Step 6: Run the installed environment's offline suite**

Run:

```bash
/home/lbz/Local-Immersive-Translate/.venv/bin/python -m unittest discover -s /home/lbz/Local-Immersive-Translate/tests -p 'test_*.py'
```

Expected: the same test count and zero failures.

- [ ] **Step 7: Commit documentation or verification-only changes**

```bash
git add local_babeldoc_server/README.md tests
git commit -m "test: cover translation batch efficiency regression"
```

Skip this commit when Step 7 has no tracked changes.

- [ ] **Step 8: Stop before any real API request**

Report offline results, expected request-count bounds, deployed hashes, and the exact Gemini Lite UI values. Ask for separate authorization before the five-page canary.
