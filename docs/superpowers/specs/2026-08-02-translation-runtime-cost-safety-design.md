# Translation Runtime、OCR Provenance 与费用安全设计

## 状态与范围

- 日期：2026-08-02
- 状态：评审通过后的正式设计基线
- 关联任务：`b4248a83479c4efab5869279ccc77f8a`
- 范围：BabelDOC 本地翻译后端及 Zotero 调用链

本设计建立在现有表格 OCR 资格快照和 PDF 输出完整性修复之上，不回退或替换这些改动。

## 问题

该 61 页论文任务发出了约 730 次 API 请求。现有实现有四类请求放大器：原生表格被
重复 OCR；批次仅约 200 token 或 5 段；批量 JSON 失败后整批逐段回退；质量保护可能
连续经过 700 字符、350 字符及 `translate()` 二次调用。现有 token 统计只存在进程
内，无法审计请求来源、thinking、缓存、超时未知计费和实际费用，也不能在失控前阻断。

## 目标

1. OCR 异常在第一条翻译 API 请求前直接终止任务。
2. 六类现存请求路径全部经过同一个中央网关。
3. 以模型能力决定结构化输出、推理控制、token 计数和 usage 解析。
4. Gemini 使用无状态原生 Interactions v1；其他供应商保留 OpenAI 兼容接口。
5. 持久化每次请求、attempt、计费状态、usage、OCR 来源和价格快照。
6. 通过原子“预留—结算”控制并发请求的最大费用暴露。
7. 使用稳定段落 ID 和严格结构化输出，实现单项精准 fallback。
8. 单段语义尝试和计费暴露都不超过两次，删除旧连续重试链。

## 非目标

- 不重写 BabelDOC 的版面分析或表格识别算法。
- 不把 Gemini 原生协议用于其他兼容供应商。
- 不在日志中保存 API key、完整提示词、论文全文或完整译文。
- 不在实施和离线验收阶段发出真实 API 请求。
- 不自动运行 5 页 canary 或完整 61 页论文；两者分别需要再次授权。

## 强制不变量

- 没有 `TranslationRuntime` 上下文就不能发送模型请求。
- 每次物理 HTTP 发送都先原子预留一个请求名额和最坏费用。
- 预算或 OCR fail-fast 触发后不再发送新请求，所有在途请求已提前预留。
- 稳定段落 ID 是来源、尝试和输出匹配的唯一事实来源。
- Python 对象 ID 仅用于 part 内快速索引，绝不持久化，并在 part/task 结束清理。
- 缓存输入 token 只计一次缓存价格；缺失 usage 字段保持 `null`。
- 能力或 schema 配置错误不消耗段落语义尝试。
- SDK 自动重试关闭；显式网络重试形成新的预算预留和日志记录。

## 架构

```text
BabelDOC 六类调用路径
        │
        ▼
TranslationRuntime.request(RequestContext, AdapterRequest)
        ├── abort / OCR gate
        ├── capability policy
        ├── paragraph attempt gate
        ├── atomic budget reservation
        └── audit begin record
        │
        ▼
ProviderAdapter.send()
        ├── GeminiInteractionsAdapter
        └── OpenAICompatibleAdapter
        │
        ▼
normalized response/error/usage
        ├── budget settlement
        ├── ID/content validation
        ├── attempt accounting
        └── JSONL + summary checkpoint
```

建议职责边界：

- `translation_runtime.py`：生命周期、预算、尝试、错误和停止协调；
- `translator_adapters.py`：适配器协议、能力声明和响应归一化；
- `translation_batching.py`：稳定 ID、批次、schema 和集合校验；
- `ocr_safety.py`：来源注册、文档模式、重复检测和 fail-fast；
- `translation_audit.py`：JSONL、汇总、价格快照和原子落盘。

实施时可按现有文件规模合并模块，但这些职责不能重新分散到各调用路径。

## 能力驱动的双适配器

### 能力声明

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    supports_structured_output: bool
    supports_reasoning_control: bool
    supported_reasoning_levels: frozenset[str]
    exposes_reasoning_usage: bool
    supports_cached_usage: bool
    supports_count_tokens: bool
    supports_request_id: bool
```

能力是“供应商 + API surface + 具体模型”的结果，不由 `provider` 字符串单独决定。
运行时规则如下：

- 支持 `minimal`：每次显式设置；
- 模型没有推理模式且拒绝该参数：省略，不报错；
- 模型默认产生计费推理但无法控制：配置失败；
- 不返回 reasoning usage：允许调用，记录 `null`；
- 批量翻译不支持严格结构化输出：模型测试失败；
- 不支持 count tokens：允许本地保守上界，但必须通过预算契约测试。

能力来源依次为适配器内建模型清单、显式管理员配置、受控 `/test-model` 探测。不得仅
根据模型名称字符串猜测高风险能力。

### 适配器协议

```python
class ProviderAdapter(Protocol):
    capabilities: ProviderCapabilities

    def count_tokens(self, request: AdapterRequest) -> TokenEstimate: ...
    def send(self, request: AdapterRequest) -> AdapterResponse: ...
    def classify_error(self, error: Exception) -> TransportFailure: ...
```

中央运行时不读取供应商 SDK 对象，业务调用方也不直接调用 SDK。

### Gemini 原生适配器

锁定官方 `google-genai==2.6.0`。客户端显式配置
`http_options={"api_version": "v1"}`，不依赖默认 `v1beta`。每次调用都设置
`store=False`，不传 `previous_interaction_id`，也不把上一批 `steps` 放入下一批。

结构化输出使用当前顶层格式：

```python
response_format = {
    "type": "text",
    "mime_type": "application/json",
    "schema": schema,
}
generation_config = {
    "thinking_level": "minimal",
    "max_output_tokens": max_output_tokens,
}
```

不得发送旧的 `response_mime_type`，不得读取旧的 `outputs`，响应按当前
`steps`/`output_text` 契约解析。Gemini 3.6 请求不发送已废弃或忽略的
`temperature`、`top_p`、`top_k`。

契约测试固定：v1、顶层 `response_format`、`store=False`、无上一轮 ID、
`thinking_level=minimal`、当前 steps/usage 映射和 schema 拒绝分类。SDK 或 API surface
升级前必须先更新并通过契约测试。

`minimal` 不能保证 thinking token 为零；最坏费用预留把最大输出额度按“可见输出 +
thinking”统一按输出价格覆盖。

### OpenAI 兼容适配器

其他供应商继续调用 `chat.completions.create()`，客户端必须设置 `max_retries=0`。
适配器按能力发送 `reasoning_effort="minimal"`、供应商等价字段或完全省略推理参数。
仅靠提示词要求 JSON 而不支持严格 schema 的模型不能用于批量论文翻译。兼容层缺失
reasoning、cache 或 request ID 时保持 `null`。

## 中央请求运行时

### 请求上下文与六条入口

```python
@dataclass(frozen=True)
class RequestContext:
    document_id: str
    part_index: int | None
    request_category: Literal[
        "batch", "fallback", "quality_retry", "terminology", "model_test"
    ]
    paragraph_ids: tuple[str, ...]
    semantic_attempt_number: int | None
    transport_attempt_number: int
    source_token_estimate: int
    paragraph_count: int
```

阶段 A 覆盖现存六条路径：批量 JSON、逐段 fallback、700 字符质量重试、350 字符质量
重试、术语提取、`translate()` 二次调用。阶段 D 删除不再需要的重试行为，但删除前也
不能绕过网关。

### 三种计数

- `semantic_attempt`：供应商完成生成并返回针对段落的候选；随后内容或集合验证失败
  仍计一次。无输出的传输失败不增加，能力/schema 配置错误也不增加。
- `transport_attempt`：每次真实 HTTP 发送；429、503、连接/读取超时和显式重试都计。
- `billable_request`：确认被供应商接收，或不能排除已经接收并计费。

按稳定 ID 另维护 `billable_exposure_count`。每段同时满足
`semantic_attempt <= 2` 和 `billable_exposure_count <= 2`，因此读取超时或批次漏项
不会绕过“最多两次计费尝试”。

无法解析的完整批次响应对批内所有段落增加一次 semantic attempt。能解析但缺少 ID
时，已返回项增加 semantic attempt；缺失项仍占本次 billable exposure，但只有缺失项
进入下一次请求。

### 传输重试

运行时只允许明确的 429 或 503 最多显式重试一次，每次重新进入预算器。连接超时只有
确认请求未离开客户端时才可视为不计费；读取超时必须记录：

```json
{
  "billing_status": "unknown",
  "usage": null,
  "estimated_cost_usd": "0.018750",
  "transport_outcome": "response_timeout"
}
```

未知计费请求保留完整保守费用，不因没有 usage 而释放。

## 原子预算的预留—结算

### 状态

每个文档拥有线程安全的 `DocumentBudget`：

```text
request_limit
cost_limit_usd
reserved_cost
actual_cost
committed_cost
in_flight_requests
completed_requests
reserved_requests
aborted
```

- `reserved_cost`：已获发送许可但未结算请求的最坏费用；
- `actual_cost`：已结算的预算占用；usage 已知时为真实费用，计费未知时为保守估计；
- `committed_cost = actual_cost + reserved_cost`：当前最大已承诺费用暴露；
- 每条记录另存 `provider_reported_actual_cost`，区分真实 usage 和未知计费估计。

金额统一使用 `Decimal`。

### 原子预留

每次物理发送在同一把锁或事务中完成：

1. 检查任务未终止；
2. 检查请求名额；
3. 检查涉及段落的计费暴露次数；
4. 计算最坏费用；
5. 验证 `actual_cost + reserved_cost + next_reservation <= max_cost`；
6. 增加 reserved request、reserved cost 和 in-flight；
7. 返回一次性 reservation token，随后才允许适配器发送。

原生 count tokens 可用时，输入上界采用计数结果加协议开销和安全余量；否则采用 UTF-8
字节数加消息/schema 固定开销作为本地保守 token 上界。预留按输入完全未缓存、输出
达到 `max_output_tokens` 计算，thinking 包含在输出价格中。费用上限启用而价格未知时，
第一条请求前配置失败，不能用零价格绕过。

### 结算

- 成功且有 usage：移除预留，按真实 usage 增加 actual，释放差额；
- 确认未离开客户端：释放费用预留，物理请求计数保留；
- 供应商确认不计费的限流：释放费用预留，物理请求计数保留；
- 读取超时或是否接收不明：把完整预留转入 actual，标记
  `cost_accuracy="estimated_unknown_billing"`；
- 所有路径均减少 in-flight、增加 completed，并持久化结算。

预算触发只保证不再发出新请求，不能承诺触发后绝不产生一分钱，因为已经在途的请求
可能完成。任一线程触发 OCR 或预算停止后原子设置 `aborted=True`；取消尚未开始的
Future，尝试取消并等待在途请求结算。即使 BabelDOC 继续调度 fallback，网关也拒绝
发送，服务器最终把任务标为失败而不是部分成功。

## OCR 来源与异常阻断

### 稳定来源注册

`PdfParagraph` 使用 slots，不能动态写 `source_type`，因此使用 sidecar：

```python
origin_by_stable_id: dict[str, ParagraphOriginRecord]
stable_id_by_object_id: dict[int, str]
```

来源类型为 `native_text`、`table_ocr`、`image_ocr`。记录包含 stable ID、part、页码、
表格/OCR block 索引、量化 bbox 和规范化文本哈希。第一阶段稳定 ID 采用确定性顺序，
例如 `part-002/page-041/table-000/block-012`；普通段落使用 page/paragraph 形式。
对象 ID 只在当前 part 内查找，并在 part/task 结束清理，绝不写入日志。

### 原生字符快照与文档模式

沿用现有 `select_table_ocr_regions(document, runtime)`：在字符层仍完整时冻结每个表格的
OCR 资格，后续注入只消费快照，不在字符被样式/公式阶段消费后重新判断。

预处理分类：

```python
DocumentTextMode = Literal["born_digital", "scanned", "hybrid"]
```

- 大多数页面有可提取原生文本：`born_digital`；
- 大多数页面无原生文本但有页面图像/OCR 候选：`scanned`；
- 两类页面均显著存在或同页双层：`hybrid`。

分类结果与逐页证据进入汇总；边界情形按更保守的 hybrid 处理。

### 重复判据与阈值

OCR 与原生文本只有同时满足坐标和文本条件才重复：

```python
duplicate = (
    bbox_overlap >= 0.5
    and normalized_text_similarity >= 0.75
)
```

不同坐标的相同表头、变量名或列标题不得去重。正式默认配置：

```json
{
  "max_table_ocr_paragraphs": 200,
  "max_table_ocr_to_native_ratio": 0.5,
  "max_ocr_blocks_per_table": 100,
  "ratio_check_min_native_paragraphs": 20,
  "ratio_check_document_modes": ["born_digital", "hybrid"],
  "bbox_overlap_threshold": 0.5,
  "text_similarity_threshold": 0.75,
  "ocr_anomaly_action": "fail"
}
```

- born-digital：严格执行全局比例和绝对阈值；
- hybrid：逐页应用比例规则，再应用全局绝对阈值；
- scanned：不执行全局比例，只检查重复注入、单表 block 和全局绝对上限。

比例检查仅在比较范围内 native 段落不少于 20 时执行。native、table OCR、image OCR、
重复候选和阈值统计在任何适配器请求前完成；异常直接失败并输出诊断。

## 稳定 ID 的结构化输出

输入和输出分别为：

```json
{"paragraphs": [{"id": "part-001/page-003/paragraph-007", "text": "..."}]}
```

```json
{"translations": [{"id": "part-001/page-003/paragraph-007", "translation": "..."}]}
```

schema 使用 `required`、`additionalProperties=false`、数组 `minItems/maxItems` 和 ID
枚举。本地仍强制检查：

```python
missing_ids = expected_ids - returned_ids
unknown_ids = returned_ids - expected_ids
duplicate_ids = find_duplicates(output_ids)
```

处理规则：

- 仅缺少 ID：保留有效结果，只重试缺失段；
- 未知 ID：丢弃并记录，保留其他有效项；
- 重复 ID：对应源段进入单段 fallback，其他唯一有效项保留；
- 整体 JSON 无法解析：本批次本轮失败，批内段落计一次 semantic attempt；
- schema 参数被拒绝：模型配置失败，不消耗 semantic attempt，也不降级为提示词 JSON；
- 单项内容质量失败：只处理对应 ID，并受两次限制。

schema 参数拒绝如果已经发生物理发送，仍照常计入 transport request，并按响应证据结算
billing status；“不消耗 semantic attempt”不等于“不记录请求或费用”。

输出顺序不参与匹配，最终按输入 stable ID 排序。

## 批次与质量保护

阶段 D 正式参数：

```json
{
  "batch_target_source_tokens": 1000,
  "batch_max_source_tokens": 1500,
  "batch_max_paragraphs": 16,
  "max_output_tokens": 4096,
  "max_semantic_attempts_per_paragraph": 2,
  "max_billable_exposures_per_paragraph": 2
}
```

优先同页或相邻页组批；超过 1500 源 token 或 16 段切分。固定提示、schema 和术语计入
请求 token 估算，但批次边界目标只针对源文本。

旧链路替换为“结构化批量第一次 → 仅失败 ID 一次精准单段 fallback”。fallback 后不再
自动进入 700/350 切片。极长单段在发送前按确定性句界切分，子段共享父 ID 和稳定
suffix；这是预处理，不是失败后的额外计费重试。术语提取计入同一预算，失败不得无上限
重试，可以空术语继续但必须记录原因。

## Usage 与成本

归一化字段：

```json
{
  "prompt_tokens": 0,
  "visible_completion_tokens": 0,
  "reasoning_tokens": null,
  "cached_tokens": 0,
  "tool_use_tokens": null,
  "total_tokens": 0
}
```

Gemini 映射 `total_input_tokens`、`total_output_tokens`、`total_thought_tokens`、
`total_cached_tokens`、`total_tool_use_tokens`、`total_tokens`。兼容层字段不存在时保持
null，只有供应商明确返回零才记录 0。

任务开始冻结价格快照：

```json
{
  "provider": "google",
  "model": "gemini-3.6-flash",
  "api_surface": "interactions-v1",
  "service_tier": "standard",
  "input_usd_per_million": "1.5",
  "output_usd_per_million": "7.5",
  "cached_input_usd_per_million": "0.15",
  "usd_to_jpy": "150",
  "tax_included": false,
  "captured_at": "2026-08-02T00:00:00+08:00"
}
```

快照为带生效日期的本地配置，不在任务中途联网刷新。成本公式：

```python
uncached_input_tokens = max(0, prompt_tokens - cached_tokens)
input_cost = (
    uncached_input_tokens * input_price
    + cached_tokens * cached_input_price
)
output_cost = (
    visible_completion_tokens + reasoning_tokens_or_zero
) * output_price
```

按每百万 token 换算，缓存 token 不再收普通输入价。若兼容供应商只给不可拆分总输出，
该输出完整按输出价计算，`reasoning_tokens` 仍保持 null。

## 审计持久化

工作目录持续 checkpoint，任务结束复制到最终审计目录：

```text
.local-babeldoc/working/<document_id>/api_calls.jsonl
.local-babeldoc/working/<document_id>/usage_summary.json
.local-babeldoc/outputs/<document_id>/api_calls.jsonl
.local-babeldoc/outputs/<document_id>/usage_summary.json
```

JSONL 每次结算追加一条完整记录；summary 原子替换。崩溃恢复时以 JSONL 重放，不信任
未完成写入的 summary。请求记录至少包含本地/供应商请求 ID、category、part、stable
IDs、段落/token 数、semantic/transport attempt、billing status、transport outcome、
reservation、settled cost、cost accuracy、usage 和起止时间。

汇总至少持久化：

```json
{
  "request_count": 0,
  "transport_attempt_count": 0,
  "billable_request_count": 0,
  "unknown_billing_count": 0,
  "batch_request_count": 0,
  "fallback_request_count": 0,
  "quality_retry_count": 0,
  "terminology_request_count": 0,
  "prompt_tokens": 0,
  "visible_completion_tokens": 0,
  "reasoning_tokens": null,
  "cached_tokens": 0,
  "total_tokens": 0,
  "native_paragraphs": 0,
  "table_ocr_paragraphs": 0,
  "image_ocr_paragraphs": 0,
  "reserved_cost_usd": "0",
  "actual_cost_usd": "0",
  "committed_cost_usd": "0",
  "estimated_cost_jpy": "0",
  "in_flight_requests": 0,
  "completed_requests": 0,
  "abort_reason": null,
  "pricing_snapshot": {}
}
```

部分请求缺失 reasoning usage 时，汇总同时标记 `reasoning_usage_complete=false`，不能用
零假装完整。日志不保存秘密或正文，只保存 stable IDs、类别、计数、文本哈希和短错误
摘要。

## 默认安全配置

```json
{
  "max_requests_per_document": 150,
  "max_estimated_cost_jpy": 300,
  "max_semantic_attempts_per_paragraph": 2,
  "max_billable_exposures_per_paragraph": 2,
  "explicit_transport_retries": 1,
  "thinking_level": "minimal"
}
```

内部按冻结汇率换算 USD。任务进入线程池前验证预算为正、价格完整、能力安全和结构化
输出可用。`/test-model` 返回 capability、API surface、版本和 usage 可见性；离线测试
使用 fake adapter。只有用户主动触发模型测试或单独授权，才允许真实探测请求，且同样
经过预算网关。

## 错误分类

```text
ocr_anomaly
budget_request_limit
budget_cost_limit
paragraph_attempt_limit
uncontrolled_billable_reasoning
structured_output_unsupported
model_configuration_error
transport_rate_limited
transport_unavailable
transport_timeout_unknown_billing
response_schema_invalid
response_id_set_invalid
```

错误包含 document、part、category、stable IDs、阈值或预算快照，不含秘密或正文。
OCR、预算、不可控推理和能力错误终止任务；单项响应错误在两次限制内精准 fallback；
达到限制后失败并保留诊断，不静默漏译。Zotero 显示短原因和审计路径，未完整翻译的 PDF
不得标记成功。

## 实施顺序

### 阶段 A：不改变调用行为的中央网关

- 建立 provenance、usage、pricing、budget 和 request context 数据模型；
- 建立 `TranslationRuntime.request()`；
- 六条现存路径全部改走网关，暂时保持原提示、批次和兼容接口；
- 禁用 SDK 自动重试；
- 用 fake adapter 完成离线并发、超时、usage 和绕过检测测试。

阶段 A 的验收重点是“所有请求无处绕过”，不是降低请求数。

### 阶段 B：费用与 OCR 安全

- 完善原生字符快照、stable provenance 和对象索引清理；
- 实现模式分类、坐标 + 文本重复检测和 OCR fail-fast；
- 实现原子请求/费用预留和结算；
- 实现 semantic attempt 与 billable exposure 两次限制；
- 持久化 JSONL、summary 和价格快照。

完成后即使仍走旧 API，也不能再无边界消耗。

### 阶段 C：Gemini 原生化

- 锁定 `google-genai==2.6.0` 和 Interactions v1；
- 实现 Gemini adapter、`minimal`、`store=False` 和完整 usage 映射；
- 启用 stable ID 原生结构化输出；
- 将 `/test-model` 改为能力驱动的受控探测；
- 其他供应商继续使用 OpenAI 兼容 adapter。

### 阶段 D：吞吐优化

- 批次目标 1000、上限 1500 源 token，最多 16 段；
- 精准处理 missing、unknown、duplicate ID；
- 移除 700/350 连续重试链和重复 `translate()` 调用；
- 用 Borges 保存的段落/OCR fixture 做全量离线回归和请求数上界断言。

### 阶段 E：真实 canary

真实测试不属于自动实施验收。单独授权后的首次 canary 固定为：

```json
{
  "page_range": "包含正文、公式和原生表格的5页",
  "max_requests": 20,
  "max_estimated_cost_jpy": 50,
  "thinking_level": "minimal"
}
```

canary 验证 OCR、stable IDs、reasoning usage、请求数、费用结算和 PDF。通过后仍需另一
次明确授权才能运行完整 61 页。

## 测试策略

### 离线单元测试

- 能力矩阵覆盖可控推理、普通非推理、不可控计费推理；
- Gemini 请求契约断言 v1、`store=False`、当前 response format/steps/usage；
- OpenAI 兼容客户端断言 `max_retries=0`；
- 16 线程同时预留不能突破请求或费用上限；
- 成功、429、503、连接失败、读取超时正确结算；
- cache 成本不重复，reasoning 缺失保持 null；
- stable ID 跨对象重建确定，object ID 不落盘；
- born-digital、scanned、hybrid fixture 应用不同 OCR 规则；
- OCR 重复同时要求 bbox 与文本阈值；
- missing、unknown、duplicate、乱序、不可解析 JSON 精准处理；
- schema 配置错误不消耗 semantic attempt；
- 六条遗留路径由 spy runtime 证明全部经过网关。

### 离线集成与回归

- fake adapter 重放多 part、并发、超时和崩溃恢复；
- Borges fixture 在任何 fake 请求前完成 provenance 统计和 fail-fast；
- 断言新批次请求数上界，不回归到 200-token/5-paragraph；
- 每段 semantic attempt 和 billable exposure 都不超过 2；
- 成功/失败任务均产生可重放 JSONL 和一致 summary；
- 继续运行既有表格 OCR、PDF 合并、源侧渲染和完整性测试。

测试安装网络禁用哨兵；任何意外真实网络调用都使测试失败。

## 验收标准

- 静态搜索和 spy 测试证明所有模型请求只有一个中央发送点；
- OCR 异常发生在第一条 API 请求前；扫描版不会因 native=0 被比例规则误杀；
- Gemini 固定使用 Interactions v1、minimal、store false 和当前 schema；
- 其他模型按能力发送或省略推理参数；
- 并发下 `actual_cost + reserved_cost` 不超过硬预算；
- 未知计费超时保守占用预算并可审计；
- 每段最多两次 semantic attempt 和两次 billable exposure；
- stable ID 精准匹配，单项错误不导致整批逐段重做；
- usage、价格、category、attempt 和 OCR 来源均持久化；
- cache 输入无重复计费；
- 离线 Borges 回归不发真实请求；
- 未再次授权时不执行 canary 或全文测试。

## 兼容性与迁移

阶段 A 先包裹旧调用，不同时修改提示、批次和协议，各阶段有独立回滚边界。旧配置缺少
capability 时，明确普通非推理模型可省略 reasoning；默认计费推理且无法证明可控的模型
拒绝启动。

现有未提交的 PDF 渲染完整性和表格 OCR 资格快照修改是前置工作，实施时必须保留，
不得覆盖或回退。

## 官方契约依据

- API v1 与版本选择：<https://ai.google.dev/gemini-api/docs/api-versions>
- Interactions v1、steps 与 usage：<https://ai.google.dev/api/interactions-api-v1>
- 无状态与存储行为：<https://ai.google.dev/gemini-api/docs/interactions-overview>
- 2026 Interactions 破坏性变更：<https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026>
- 结构化输出：<https://ai.google.dev/gemini-api/docs/structured-output>
- thinking levels：<https://ai.google.dev/gemini-api/docs/generate-content/thinking>
- token：<https://ai.google.dev/gemini-api/docs/tokens>
- 定价：<https://ai.google.dev/gemini-api/docs/pricing>
- Python SDK releases：<https://github.com/googleapis/python-genai/releases>
