# Translation Runtime、OCR Provenance 与费用安全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立所有翻译请求不能绕过的中央运行时，在请求前阻断 OCR 异常，以能力驱动的 Gemini/OpenAI 双适配器执行请求，并持久化可对账 usage、attempt 和费用。

**Architecture:** 本地 `RuntimeBackedTranslator` 替代 BabelDOC 的 `OpenAITranslator` 作为唯一模型出口，`TranslationRuntime` 在适配器发送前执行能力、段落尝试和原子预算检查。BabelDOC 0.6.4 的批次和 OCR 阶段通过现有兼容层补丁接入稳定 ID、结构化输出及 provenance，不修改被忽略的上游源码。

**Tech Stack:** Python 3.11+、BabelDOC 0.6.4、OpenAI Python SDK 2.46.0、`google-genai==2.6.0`、`unittest`、JSONL

## Global Constraints

- 所有测试使用 fake client/adapter；未经单独授权不得发送真实 API 请求。
- 保留当前未提交的表格 OCR 资格快照、PDF 合并字体修复和源侧渲染审计。
- Gemini 固定 Interactions v1、`thinking_level="minimal"`、`store=False`、无 `previous_interaction_id`。
- OpenAI 兼容客户端固定 `max_retries=0`；仅运行时可对明确 429/503 重试一次。
- 默认文档预算为 150 请求、300 JPY；每段最多两次 semantic attempt 和两次 billable exposure。
- 批次目标 1000、上限 1500 源 token、最多 16 段、最大输出 4096 token。
- native/OCR 重复必须同时满足 bbox overlap >= 0.5 和文本相似度 >= 0.75。
- 价格、金额和汇率使用 `Decimal`；缺失 usage 字段保持 `None`。
- 不持久化 API key、完整提示词、完整源文或完整译文。

---

### Task 1: 建立统一类型、能力和成本模型

**Files:**
- Create: `local_babeldoc_server/translation_types.py`
- Create: `tests/test_translation_types.py`

**Interfaces:**
- Produces: `ProviderCapabilities`, `NormalizedUsage`, `PricingSnapshot`, `AdapterRequest`, `AdapterResponse`, `RequestContext`, `BillingStatus`, `TransportOutcome`
- Produces: `calculate_usage_cost(usage, pricing) -> Decimal`

- [ ] **Step 1: 写 usage 和缓存成本失败测试**

```python
PRICING = PricingSnapshot(
    provider="google",
    model="gemini-3.6-flash",
    api_surface="interactions-v1",
    service_tier="standard",
    input_usd_per_million=Decimal("1.5"),
    output_usd_per_million=Decimal("7.5"),
    cached_input_usd_per_million=Decimal("0.15"),
    usd_to_jpy=Decimal("150"),
    tax_included=False,
    captured_at="2026-08-02T00:00:00+08:00",
)

def test_cached_input_is_not_charged_twice(self):
    usage = NormalizedUsage(
        prompt_tokens=1000,
        visible_completion_tokens=100,
        reasoning_tokens=50,
        cached_tokens=400,
        tool_use_tokens=0,
        total_tokens=1150,
    )
    self.assertEqual(calculate_usage_cost(usage, PRICING), Decimal("0.002085"))

def test_missing_reasoning_usage_stays_none(self):
    usage = NormalizedUsage.from_openai_usage(SimpleNamespace(
        prompt_tokens=10, completion_tokens=4, total_tokens=14
    ))
    self.assertIsNone(usage.reasoning_tokens)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_translation_types -v`

Expected: FAIL because `translation_types` does not exist.

- [ ] **Step 3: 实现不可变类型和成本函数**

```python
@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_structured_output: bool
    supports_reasoning_control: bool
    supported_reasoning_levels: frozenset[str]
    exposes_reasoning_usage: bool
    supports_cached_usage: bool
    supports_count_tokens: bool
    supports_request_id: bool

def calculate_usage_cost(usage: NormalizedUsage, pricing: PricingSnapshot) -> Decimal:
    cached = Decimal(usage.cached_tokens or 0)
    uncached = Decimal(max(0, (usage.prompt_tokens or 0) - int(cached)))
    output = Decimal(usage.visible_completion_tokens or 0)
    thought = Decimal(usage.reasoning_tokens or 0)
    return (
        uncached * pricing.input_usd_per_million
        + cached * pricing.cached_input_usd_per_million
        + (output + thought) * pricing.output_usd_per_million
    ) / Decimal(1_000_000)
```

OpenAI usage 兼容读取 `completion_tokens_details.reasoning_tokens` 和
`prompt_tokens_details.cached_tokens`；属性不存在时写 `None`。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `python3 -m unittest tests.test_translation_types -v`

- [ ] **Step 5: 提交新文件**

```bash
git add local_babeldoc_server/translation_types.py tests/test_translation_types.py
git commit -m "feat: add translation usage and capability types"
```

### Task 2: 实现线程安全的预算预留与结算

**Files:**
- Create: `local_babeldoc_server/translation_budget.py`
- Create: `tests/test_translation_budget.py`

**Interfaces:**
- Consumes: `PricingSnapshot`, `NormalizedUsage`, `calculate_usage_cost`
- Produces: `DocumentBudget.reserve(context, worst_case_cost) -> Reservation`
- Produces: `DocumentBudget.settle(reservation, settlement) -> BudgetSnapshot`
- Produces: `BudgetExceeded`, `AttemptLimitExceeded`

- [ ] **Step 1: 写原子并发和未知计费失败测试**

```python
CONTEXT = RequestContext(
    document_id="doc-1",
    part_index=0,
    request_category="batch",
    paragraph_ids=("p001",),
    semantic_attempt_number=1,
    transport_attempt_number=1,
    source_token_estimate=100,
    paragraph_count=1,
)

def test_concurrent_reservations_never_exceed_cost_limit(self):
    budget = DocumentBudget(max_requests=100, max_cost_usd=Decimal("1.00"))
    def try_reserve(_):
        try:
            return budget.reserve(CONTEXT, Decimal("0.11"))
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(try_reserve, range(32)))
    self.assertEqual(sum(result is not None for result in results), 9)
    self.assertLessEqual(budget.snapshot().committed_cost, Decimal("1.00"))

def test_unknown_billing_converts_full_reservation_to_actual(self):
    budget = DocumentBudget(max_requests=2, max_cost_usd=Decimal("1.00"))
    reservation = budget.reserve(CONTEXT, Decimal("0.25"))
    snapshot = budget.settle_unknown(reservation, "response_timeout")
    self.assertEqual(snapshot.actual_cost, Decimal("0.25"))
    self.assertEqual(snapshot.reserved_cost, Decimal("0"))
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_translation_budget -v`

- [ ] **Step 3: 实现锁内预留、一次性 token 和结算**

`reserve()` 在同一 `RLock` 内检查 aborted、request limit、stable ID 的 billable
exposure 及 `actual + reserved + next <= limit`。`Reservation` 带 UUID 且只能结算一次。
已知 usage 释放差额；确认未发送结算零；timeout 把完整预留转入 actual。

- [ ] **Step 4: 增加 semantic attempt 与 billable exposure 边界测试并实现**

```python
budget.record_semantic_attempt(("p001",))
budget.record_semantic_attempt(("p001",))
with self.assertRaises(AttemptLimitExceeded):
    budget.assert_semantic_attempt_available(("p001",))
```

- [ ] **Step 5: 运行预算测试并确认 GREEN**

Run: `python3 -m unittest tests.test_translation_budget -v`

- [ ] **Step 6: 提交新文件**

```bash
git add local_babeldoc_server/translation_budget.py tests/test_translation_budget.py
git commit -m "feat: reserve and settle translation budgets"
```

### Task 3: 持久化 JSONL 和汇总

**Files:**
- Create: `local_babeldoc_server/translation_audit.py`
- Create: `tests/test_translation_audit.py`

**Interfaces:**
- Produces: `TranslationAuditWriter.record(ApiCallRecord) -> None`
- Produces: `TranslationAuditWriter.checkpoint(summary) -> None`
- Produces: `TranslationAuditWriter.publish(output_dir) -> None`
- Produces: `replay_api_calls(path) -> UsageSummary`

- [ ] **Step 1: 写成功、timeout 和崩溃重放失败测试**

使用 `TemporaryDirectory` 写两条记录，断言 JSONL 每行可独立解析、summary 使用临时文件
原子替换、重放后 request/billable/unknown/category/token/cost 计数一致。检查序列化内容不含
`prompt`、`api_key`、`source_text` 或 `translation` 字段。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_translation_audit -v`

- [ ] **Step 3: 实现 append + flush/fsync 和 summary replace**

```python
with self.jsonl_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
temporary.replace(self.summary_path)
```

汇总保留 `reasoning_usage_complete`，任一请求缺字段时 reasoning 聚合为 `None`。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `python3 -m unittest tests.test_translation_audit -v`

- [ ] **Step 5: 提交新文件**

```bash
git add local_babeldoc_server/translation_audit.py tests/test_translation_audit.py
git commit -m "feat: persist translation request audits"
```

### Task 4: 实现能力驱动适配器

**Files:**
- Create: `local_babeldoc_server/translator_adapters.py`
- Create: `tests/test_translator_adapters.py`
- Modify: `local_babeldoc_server/requirements.txt`
- Modify: `tests/test_local_babeldoc_server.py`

**Interfaces:**
- Consumes: Task 1 request/response/usage/capability types
- Produces: `ProviderAdapter`, `OpenAICompatibleAdapter`, `GeminiInteractionsAdapter`
- Produces: `resolve_adapter(model_config, client_factory=None) -> ProviderAdapter`
- Produces: `UncontrolledBillableReasoningError`, `ModelConfigurationError`

- [ ] **Step 1: 写 Gemini 请求契约失败测试**

fake client 捕获 `interactions.create` 参数并返回完整 `steps/output_text/usage` fixture。
断言 client 使用 `api_version="v1"`，请求有 `store=False`、无 previous ID、顶层当前
`response_format`、`thinking_level="minimal"`，并完整映射 thought/cache/tool usage。

- [ ] **Step 2: 写 OpenAI 兼容能力失败测试**

断言 client factory 收到 `max_retries=0`；普通非推理模型不发送 reasoning；支持 minimal
的模型发送 `reasoning_effort="minimal"`；默认计费推理但不可控时抛
`UncontrolledBillableReasoningError`；缺失 reasoning usage 保持 None。

- [ ] **Step 3: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_translator_adapters -v`

- [ ] **Step 4: 实现 lazy-import 适配器**

Gemini 构造等价于：

```python
client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(api_version="v1"),
)
interaction = client.interactions.create(
    model=model,
    input=request.input,
    response_format=request.response_format,
    generation_config={
        "thinking_level": "minimal",
        "max_output_tokens": request.max_output_tokens,
    },
    store=False,
)
```

兼容 client 构造 `openai.OpenAI(..., max_retries=0)`。两者均不在 adapter 内自动重试。

- [ ] **Step 5: 锁定 SDK 并运行测试**

在 requirements 新增 `google-genai==2.6.0`。Run:
`python3 -m unittest tests.test_translator_adapters tests.test_local_babeldoc_server.InstallerVersionTest -v`

- [ ] **Step 6: 提交适配器文件**

```bash
git add local_babeldoc_server/translator_adapters.py local_babeldoc_server/requirements.txt tests/test_translator_adapters.py tests/test_local_babeldoc_server.py
git commit -m "feat: add capability-driven translation adapters"
```

### Task 5: 建立中央运行时和 BabelDOC translator bridge

**Files:**
- Create: `local_babeldoc_server/translation_runtime.py`
- Create: `tests/test_translation_runtime.py`

**Interfaces:**
- Consumes: adapter、budget、audit、types
- Produces: `TranslationRuntime.request(context, request) -> AdapterResponse`
- Produces: `RuntimeBackedTranslator.translate(...)`, `.llm_translate(...)`, `.do_translate(...)`, `.do_llm_translate(...)`

- [ ] **Step 1: 写“所有物理发送先预留”失败测试**

fake adapter 在 `send()` 内检查 budget snapshot 已有一个 in-flight；成功后断言 in-flight
归零、usage 已落盘。另测试 aborted runtime 拒绝调用 adapter。

- [ ] **Step 2: 写 transport/semantic/billing 分离失败测试**

fake adapter 依次抛明确 503 和成功，断言 transport=2、semantic=1、每次均有独立
reservation。读取超时断言 billing unknown、usage None、保守费用不释放且不会让同段
超过两次 exposure。

- [ ] **Step 3: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_translation_runtime -v`

- [ ] **Step 4: 实现 request 生命周期和最多一次 429/503 重试**

每个 loop 先 reserve，再 `adapter.send()`，随后按 normalized result/error settle 和 record。
配置/schema 错误不调用 `record_semantic_attempt`；有候选输出即记录 semantic attempt。

- [ ] **Step 5: 实现 RuntimeBackedTranslator 的两个 BabelDOC API**

普通翻译生成 system/user messages，category 默认为 fallback；LLM 翻译接受
`rate_limit_params` 中的 category、stable IDs、schema、source token 和 max output。
保留 BabelDOC 所需的 token counters 和 cache identity，但所有真实发送只调用 runtime。

- [ ] **Step 6: 运行测试并确认 GREEN**

Run: `python3 -m unittest tests.test_translation_runtime -v`

- [ ] **Step 7: 提交新文件**

```bash
git add local_babeldoc_server/translation_runtime.py tests/test_translation_runtime.py
git commit -m "feat: centralize billable translation requests"
```

### Task 6: 建立 stable provenance 和 OCR fail-fast

**Files:**
- Create: `local_babeldoc_server/ocr_safety.py`
- Create: `tests/test_ocr_safety.py`
- Modify: `local_babeldoc_server/babeldoc_compat.py`
- Modify: `tests/test_babeldoc_compat.py`

**Interfaces:**
- Produces: `ParagraphOriginRegistry`, `ParagraphOriginRecord`, `DocumentTextMode`
- Produces: `classify_document_text_mode(page_stats) -> DocumentTextMode`
- Produces: `is_duplicate_text_region(native, ocr) -> bool`
- Produces: `evaluate_ocr_safety(snapshot, thresholds) -> OcrSafetyReport`
- Consumes: existing `select_table_ocr_regions` and `inject_ocr_table_paragraphs`

- [ ] **Step 1: 写 stable ID 和对象清理失败测试**

```python
record = registry.register_table_ocr(
    paragraph, part_index=2, page_number=41, table_index=0, block_index=12
)
self.assertEqual(record.stable_id, "part-002/page-041/table-000/block-012")
self.assertNotIn(str(id(paragraph)), json.dumps(record.to_dict()))
registry.clear_part(2)
self.assertIsNone(registry.lookup_object(paragraph))
```

- [ ] **Step 2: 写三种 PDF 模式和双条件去重失败测试**

fixture 覆盖：native=0/OCR=500 的 scanned 不因 ratio 失败；born-digital 超过 0.5 失败；
hybrid 逐页判断；同文不同 bbox 不重复；bbox 重叠但文本不同不重复；两个条件均达到才重复。

- [ ] **Step 3: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_ocr_safety -v`

- [ ] **Step 4: 实现 provenance、模式和阈值判定**

默认 absolute limit 为 200 OCR 段、100 blocks/table；ratio 只在 native >= 20 且模式为
born-digital/hybrid 时执行。文本比较使用规范化文本的 `SequenceMatcher.ratio()`，bbox
使用交集面积除较小区域面积。

- [ ] **Step 5: 把现有 OCR 注入接入 registry 和 part gate**

`wrapped_layout_process` 冻结原生字符与 table eligibility；`wrapped_styles_process` 为原生
和 OCR 段注册 stable ID，过滤坐标+文本重复项，完成当前 part 统计后调用
`runtime.approve_ocr_part(part_index, report)`。异常调用 `runtime.abort("ocr_anomaly", ...)`
并在进入 term extraction/translation 前抛错。

- [ ] **Step 6: 运行 OCR/compat 测试并确认 GREEN**

Run: `python3 -m unittest tests.test_ocr_safety tests.test_table_ocr tests.test_babeldoc_compat -v`

- [ ] **Step 7: 提交不重叠的新文件；保留 compat 既有用户改动**

```bash
git add local_babeldoc_server/ocr_safety.py tests/test_ocr_safety.py
git commit -m "feat: detect unsafe OCR provenance before translation"
```

`babeldoc_compat.py` 与既有测试当前含用户未提交修复，不整文件暂存；最终交付时单独列出。

### Task 7: 用 stable ID 接管 BabelDOC 批量翻译

**Files:**
- Create: `local_babeldoc_server/translation_batching.py`
- Create: `tests/test_translation_batching.py`
- Modify: `local_babeldoc_server/babeldoc_compat.py`
- Modify: `tests/test_babeldoc_compat.py`

**Interfaces:**
- Consumes: `ParagraphOriginRegistry`, `RuntimeBackedTranslator`
- Produces: `build_translation_schema(expected_ids) -> dict`
- Produces: `validate_translation_set(payload, expected_ids) -> BatchValidation`
- Produces: `install_llm_batch_runtime_patch(...)`

- [ ] **Step 1: 写 schema 与集合校验失败测试**

fixture 分别返回乱序完整项、missing、unknown、duplicate 和非法 JSON。断言乱序通过并按
输入排序；missing 只列缺失；unknown 被丢弃；duplicate 只标记对应 ID；非法 JSON 标记
全批失败。schema 断言 required、additionalProperties false、min/max items 和 ID enum。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_translation_batching -v`

- [ ] **Step 3: 实现纯函数和 stable payload**

```python
payload = {
    "paragraphs": [
        {"id": registry.stable_id(paragraph), "text": input_text}
        for paragraph, input_text in prepared
    ]
}
```

输出固定为 `{"translations": [{"id": ..., "translation": ...}]}`。

- [ ] **Step 4: 写 BabelDOC 精准 fallback 失败测试**

用 fake `ILTranslatorLLMOnly` batch 包含 p001/p002；fake runtime 只返回 p001。断言 p001
直接 post-translate，只向 executor 提交 p002 fallback。duplicate p001 时也只 fallback
p001；schema 配置错误直接抛出，不提交段落 fallback。

- [ ] **Step 5: 在 compat 中替换 `ILTranslatorLLMOnly.translate_paragraph`**

本地 patch 复用上游的 pre/post translate、tracker、prompt 上下文和 executor，但构造 stable
payload/schema，并把 category=batch、stable IDs 和 source tokens 传给 runtime translator。
校验后仅为失败 ID 调用上游单段 `ILTranslator.translate_paragraph`。

- [ ] **Step 6: 运行 batching/compat 测试并确认 GREEN**

Run: `python3 -m unittest tests.test_translation_batching tests.test_babeldoc_compat -v`

- [ ] **Step 7: 提交新文件**

```bash
git add local_babeldoc_server/translation_batching.py tests/test_translation_batching.py
git commit -m "feat: match batch translations by stable id"
```

### Task 8: 扩大批次并删除连续质量重试链

**Files:**
- Modify: `local_babeldoc_server/babeldoc_compat.py`
- Modify: `local_babeldoc_server/translation_quality.py`
- Modify: `tests/test_babeldoc_compat.py`
- Modify: `tests/test_translation_quality.py`

**Interfaces:**
- Produces: compat patch of `ILTranslatorLLMOnly.process_page`
- Produces: `split_oversized_paragraph(text, token_counter, max_tokens) -> list[str]`

- [ ] **Step 1: 写批次边界失败测试**

构造 17 个各 90 token 段，断言第一批不超过 1500 token/16 段且不在 200 token 或 5 段
提前切分。构造超长单段，断言发送前获得确定性 `chunk-000` suffix，内容不丢失不重排。

- [ ] **Step 2: 写最多两次且无 700/350 链失败测试**

首次 batch 对 p001 质量失败，单段 fallback 再失败。断言不再调用 `llm_translate` 或
`translate` 第三次，quality audit 记录 unresolved。旧配置中的 `[700, 350]` 不再驱动
失败后循环。

- [ ] **Step 3: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_translation_quality tests.test_babeldoc_compat -v`

- [ ] **Step 4: patch process_page 并简化 quality wrapper**

批次使用 `batch_target_source_tokens=1000`、硬上限 1500、最多 16 段。移除
`wrapped_translate` 内的 chunk-size 循环和二次 `translate()`；只保留 post-translate 内容
验证，失败交由 Task 7 的一次精准 fallback。

- [ ] **Step 5: 运行测试并确认 GREEN**

Run: `python3 -m unittest tests.test_translation_quality tests.test_babeldoc_compat -v`

该任务修改已有用户变更文件，不单独整文件 commit；在最终交付中保留 diff 和测试证据。

### Task 9: 接入服务器生命周期、配置和审计发布

**Files:**
- Modify: `local_babeldoc_server/server.py`
- Modify: `local_babeldoc_server/config.example.json`
- Modify: `tests/test_local_babeldoc_server.py`
- Modify: `local_babeldoc_server/README.md`

**Interfaces:**
- Consumes: Tasks 1–8
- Produces: `AppState._create_translation_runtime(job, dirs)`
- Changes: `_create_translator(..., runtime)` returns `RuntimeBackedTranslator`

- [ ] **Step 1: 写配置和 translator factory 失败测试**

Gemini model config 的 `provider="google"` 产生 Gemini adapter；其他配置产生 compatible
adapter。普通非推理 capability 省略 minimal；不可控计费推理在 `_run_babeldoc` 调用
`async_translate` 前失败。默认配置断言预算、OCR 阈值、批次和重试值与设计一致。

- [ ] **Step 2: 写成功/失败都发布审计失败测试**

fake `async_translate` 成功时断言 runtime summary 被复制到 output；OCR 或 budget 异常时
job status=failed、message 含短错误码、working JSONL 保留，且没有后续 adapter send。

- [ ] **Step 3: 运行测试并确认 RED**

Run: `python3 -m unittest tests.test_local_babeldoc_server -v`

- [ ] **Step 4: 创建 runtime 并注入 TranslationConfig**

`_run_babeldoc` 在创建 translator 前冻结 pricing/config，创建 budget/audit/adapter/runtime；
把 runtime、origin registry、part index 和 batch limits 作为 `config.local_*` 属性供 compat
读取。translator 和 term extractor 使用同一个 RuntimeBackedTranslator。

- [ ] **Step 5: 结束时结算并发布 summary**

成功、异常和取消都在 `finally` 调用 `runtime.close()`，等待 in-flight 归零并 checkpoint。
成功复制审计至 output；失败保留 working audit。任务 message 显示 abort code 和路径。

- [ ] **Step 6: 更新示例配置与 README**

明确 provider/capabilities/pricing、默认 150 requests/300 JPY、OCR 三模式、usage 文件、
Gemini v1/stateless/minimal 和真实 canary 必须授权。

- [ ] **Step 7: 运行 server 回归并确认 GREEN**

Run: `python3 -m unittest tests.test_local_babeldoc_server -v`

`server.py` 当前含用户 PDF 审计改动，不整文件单独 commit。

### Task 10: 安装契约、全量离线回归与费用上界

**Files:**
- Modify: `scripts/install-local-backend.sh`
- Modify: `scripts/install-local-backend.ps1`
- Modify: `tests/test_local_babeldoc_server.py`
- Create: `tests/fixtures/translation_runtime/borges_61_page_profile.json`

**Interfaces:**
- Verifies: exact SDK installation and no-network Borges replay

- [ ] **Step 1: 写 installer 契约失败测试**

断言 requirements 精确包含 `google-genai==2.6.0`，bash/PowerShell 安装 requirements 后
执行不联网的 `from google import genai` import check，同时保留 RapidOCR prewarm。

- [ ] **Step 2: 写 Borges 离线 replay 上界测试**

fixture 固定为 61 页、600 个 native 段（每段 20 source tokens）、已知原生表格页
11/13/14/21/42/43/45/46/47 各 4 个 native-table 段，stable ID 和 source hash 使用字面
值，不保存论文全文。fake adapter 对全部 ID 返回确定性译文；断言批次 <=16/1500、
文档请求 <=150、每段 exposure <=2。另一个内嵌 anomaly snapshot 使用 250 个 table OCR
段，断言在 send_count=0 时失败。

- [ ] **Step 3: 运行新测试并确认 RED**

Run: `python3 -m unittest tests.test_local_babeldoc_server tests.test_translation_runtime -v`

- [ ] **Step 4: 更新安装脚本并加入离线 fixture**

安装检查仅 import SDK，不创建 client、不读取 key、不访问网络。提交上述确定性的 61 页
metadata fixture；它验证本文档的请求数和 OCR 安全不变量，不宣称替代将来真实 canary。

- [ ] **Step 5: 运行完整测试套件**

Run: `python3 -m unittest discover -s tests -p 'test_*.py' -v`

Expected: all tests pass；仅环境确实缺少 PyMuPDF 时保留已有 3 个明确 skip。

- [ ] **Step 6: 用 BabelDOC venv 运行 PDF 专项测试**

Run: `BabelDOC/.venv/bin/python -m unittest tests.test_pdf_output_quality tests.test_babeldoc_compat -v`

Expected: all tests pass, no skips and no network traffic.

- [ ] **Step 7: 静态绕过检查**

Run:
`rg -n "chat\.completions\.create|interactions\.create" local_babeldoc_server`

Expected: physical send sites only exist inside `translator_adapters.py`；server、compat、batching
和 quality modules have none.

- [ ] **Step 8: 核对未授权真实测试未执行**

确认没有 5 页或 61 页模型调用记录。canary 固定 5 页、20 requests、50 JPY，留待用户
下一次明确授权。

- [ ] **Step 9: 最终提交策略**

提交所有不与既有 dirty hunks 重叠的新文件。对 `server.py`、`babeldoc_compat.py` 及其既有
测试只报告完整 diff，不把无法可靠区分归属的旧改动擅自纳入 commit。
