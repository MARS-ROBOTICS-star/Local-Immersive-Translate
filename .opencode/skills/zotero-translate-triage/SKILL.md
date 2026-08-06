---
name: zotero-translate-triage
description: 快速排查本地 Zotero 翻译（Local-Immersive-Translate + BabelDOC）失败/卡住任务。用户给一个 pdf_id（任务编号，32 位 hex）时使用，按固定步骤筛查状态接口、usage_summary、工作目录、资源缓存、model-tests 和 journald 日志，定位失败原因（多为资源下载网络失败、模型 API 错误等）。触发词：翻译任务失败、卡在0%、不开始、任务编号、pdf_id、翻译报错。
---

# Zotero 翻译任务快速筛查

用户提供任务编号 pdf_id（32 位 hex）后，按下面的固定流程快速定位问题，**不要从头翻代码**。

## 环境事实（已确认）

- 后端目录：`/home/lbz/Local-Immersive-Translate`
- 数据目录：`/home/lbz/Local-Immersive-Translate/.local-babeldoc/`
- 服务进程：`pgrep -af "server.py --config"`（uv 父进程 + python3 子进程）
- 服务日志走 journald（`JOURNAL_STREAM`），**没有独立 log 文件**：
  - 全量：`journalctl _PID=<python3 pid> --since "今天 14:00" --no-pager`
  - 只看错误：`journalctl _PID=<pid> --no-pager | grep -iE "error|failed|retry|connect|exception" | tail -50`
- 状态接口：`curl -s http://127.0.0.1:8765/zotero/pdf/<pdf_id>/process`
- 健康检查：`curl -s http://127.0.0.1:8765/zotero/healthz`（正常返回 `{"status":"ok",...}`）
- 无 sudo、`ptrace_scope=1` → **不能**用 py-spy/gdb 抓线程栈
- 网络：Clash Verge TUN（Meta，198.18.x.x）；shell 可用 HTTP 代理 `http://127.0.0.1:7897`
- **失败任务没有重试接口**，修好后只能让用户在 Zotero 里重新点翻译

## 快速筛查流程（按序执行，每步都有决定性意义）

### 1. 查任务状态
```bash
curl -s http://127.0.0.1:8765/zotero/pdf/<pdf_id>/process
```
- `status: "failed"` + message → 直接按 message 关键字查下方"失败特征表"
- `status: ""` / `currentStageName: "Create Task"` + 0% → 任务还在早期（资源预热/版面模型加载阶段），**不一定是坏了**，等几分钟再看；若长时间不变则查日志

### 2. 查 usage_summary（判断是否到过 LLM）
```bash
cat /home/lbz/Local-Immersive-Translate/.local-babeldoc/working/<pdf_id>/usage_summary.json
```
- `request_count: 0` → 翻译根本没调用模型 → 问题在早期（资源下载 / 解析 / 模型加载），查第 3、4 步
- `request_count > 0` → LLM 已工作，问题在后段（翻译中报错 / 渲染），直接查第 6 步日志
- `pricing_snapshot` 显示实际用的模型/厂商（如 google gemini-3.1-flash-lite）

### 3. 查工作目录产物（看走到哪一步）
```bash
find /home/lbz/Local-Immersive-Translate/.local-babeldoc/working/<pdf_id>/ -maxdepth 4
```
- 出现 `part_N_output/`、`translate_tracking.json` → 已开始翻译，不是资源问题
- 只有 `input.pdf` / `input.partN.pdf` → 卡/死在早期
- 输出成品在 `/home/lbz/Local-Immersive-Translate/.local-babeldoc/outputs/<pdf_id>/`

### 4. 查资源缓存（最常见的失败点）
```bash
echo "cmap: $(ls /home/lbz/.cache/babeldoc/cmap/ 2>/dev/null | wc -l)/146"
echo "tiktoken: $(ls /home/lbz/.cache/babeldoc/tiktoken/ 2>/dev/null | wc -l)/1"
echo "fonts: $(ls /home/lbz/.cache/babeldoc/fonts/ 2>/dev/null | wc -l)/34"
ls /home/lbz/.cache/babeldoc/models/
```
- cmap < 146 或 tiktoken 为空 → 典型"资源预热网络失败"，见下方修复 A

### 5. 查模型 API 是否可用（对照实验）
```bash
ls -t /home/lbz/Local-Immersive-Translate/.local-babeldoc/working/model-tests/ | head -1
cat /home/lbz/Local-Immersive-Translate/.local-babeldoc/working/model-tests/<最新test_id>/usage_summary.json
```
- 测试有调用记录 → 模型 key/网络正常，问题不在模型配置

### 6. 查服务端日志（最终真相）
```bash
journalctl _PID=<python3 pid> --no-pager | grep -iE "error|failed|retry|connect|exception|download|400|This API" | tail -50
```

### 7.（先做这条防止白忙）确认进程跑的是否最新代码
```bash
pgrep -af "server.py --config"                 # 进程是否活着
md5sum /home/lbz/zotero-immersivetranslate/local_babeldoc_server/translation_runtime.py \
       /home/lbz/Local-Immersive-Translate/local_babeldoc_server/translation_runtime.py  # 三处一致？
```
- 后端由 Zotero 启动，**改文件后不重启 Zotero，云端还在跑旧代码**——大量"改完没用/又失败"案例根因都是这个（历史模式库 #7）。
- 判定：进程的启动时间早于文件 mtime，或文件 md5 不一致 → 先让用户重启 Zotero 再测，别急着改代码。

## 失败特征表（message → 原因 → 修复）

| 状态 message / 现象 | 原因 | 修复 |
|---|---|---|
| `asset coroutine failed: RetryError: ... ConnectError` | babeldoc 预热下载 cmap/tiktoken/字体时网络失败（经 TUN 无代理变量） | 修复 A：补资源缓存 |
| 同左 + 缓存 cmap/tiktoken 全缺 | 同上，最典型 | 修复 A |
| `request_count: 0` 且任务失败 | 未到 LLM 就挂了（预热/解析阶段） | 按第 3/4 步定位 |
| `request_count > 0` 且失败 | LLM 或翻译中报错 | 查日志 + 对比"历史模式库" |
| `status: failed / budget_request_limit` | 请求额度耗尽（短行误判、OCR 重复注入等历史根因） | 见"历史模式库" |
| `status: failed / model_configuration_error` | Gemini 区域/代理问题，150 次全部 400 `This API is not available in your current location` | 带代理重启 Zotero 或切 DeepSeek |
| 长时间 0% + `Create Task` | 预热/模型加载慢，未必是坏 | 看日志确认在下载还是真挂 |
| 进度 100% 但卡在 Save PDF 不结束 | BabelDOC `finish` 事件死锁（历史 bug，已修） | 见"历史模式库" |
| `NetworkError when attempting to fetch resource` | 后端进程已退出，job 只存内存 | 见"历史模式库：后端恢复" |

## 历史模式库（从 Codex 会话提炼的已修 bug——同症状=大概率复发同一类问题）

### 1. 任务"卡住"/进度 100% 卡在 Save PDF（06-30、07-30 修复）
- 现象：`NetworkError`（后端进程不在）；或 `100% / Save PDF` 永不结束。
- 根因①：job 状态只存内存 `AppState.jobs`，后端进程退出后任务丢失，即使 `outputs/<pdf_id>/` 已有成品也恢复不了 → 已修：`server.py:645 get_job_or_recover_finished` / `_recover_finished_job_from_outputs` 从磁盘恢复 `.mono.pdf`/`.dual.pdf`。
- 根因②（07-30）：BabelDOC `finish` 回调有 ~10ms 竞态（先入队事件、后设 `finished=true`），后端收到 finish 后仍取下一事件 → 永久死锁停在 Save PDF。已修：收到有效 finish 事件立即返回 **server.py:1270**，不再靠"轮询到两个非空文件"猜完成（那有覆盖竞态，被视为错误方案）。
- 排查要点：`outputs/<pdf_id>/` 两个 PDF 都已生成且 zotero 里附件已导入 → 就是状态恢复/事件消费问题，不是翻译问题。

### 2. 资源预热网络失败（本次 08-06，见上文修复 A）

### 3. 区域/代理导致的模型错误（08-03 会话，任务 025a4b…）
- 现象：`failed / model_configuration_error`，`request_count=150` 但 `billable_request_count=0`，journald 里 `HTTP/1.1 400` + `This API is not available in your current location`。
- 根因：Gemini API 需代理，但 Zotero 及后端子进程没继承代理变量。
- 修复：带代理重启 Zotero：
  ```bash
  env http_proxy=http://127.0.0.1:7897/ https_proxy=http://127.0.0.1:7897/ \
      all_proxy=socks5://127.0.0.1:7897/ no_proxy=localhost,127.0.0.1 \
      /usr/lib/zotero/zotero-bin   # 或桌面启动器带上代理
  ```
- 或切到 DeepSeek（DeepSeek 不需要 Google 区域代理）。

### 4. 短表格行被 BabelDOC 长度比例误判 → 请求额度耗尽（08-03，任务 `338b5…`/`132db…`/`ceb3c…`/`0053c…`）
- 现象：`failed / budget_request_limit: N untranslated paragraph attempts`，N 随修复递减（88→58→…）。
- 根因：BabelDOC 有 `0.3 < output_tokens/input_tokens < 3` 校验；短 CJK 行（如 `Main class→主要类别` 2→8 token）超 3 倍被判"过长"，反复 fallback 吃掉请求额度。
- 修复链（已在代码中）：① 短表格行不进批量、走单行翻译（`babeldoc_compat.py` 里 `_is_translatable_short_table_text` **babeldoc_compat.py:414**）；② 又改成给 `calc_token_count` 打补丁，≤30 字符含 CJK 返回 `len(text)//3`，让比例校验通过 **babeldoc_compat.py:1533**；③ 额度上限 `max_requests_per_document` 默认提为 500 **server.py:142**（config.example.json:34）。
- 排查要点：`usage_summary.json` 里 `fallback_request_count` 偏大、`fail_on_unresolved_translation` 检查：见 `server.py:158`（默认 false）。

### 5. 表格 OCR 重复注入 / 原文左列乱码 / 表格重叠（07-31、08-02 session，任务 `b4248a…`）
- 现象：730 次 API 调用、费用远高于预期；或左列乱码；或表格文字重叠、正文缺失。
- 根因①：OCR 判断发生在 BabelDOC **已消费原生表格字符之后**，于是把原生表格误判成图片表格 → 额外注入 169+117+388 OCR 段落，整篇重复翻译。
- 根因②：61 页按 20 页/part 拆分合并时对已子集化的 Type 1 字体**再次子集化** → 左列字形映射损坏乱码。修复逻辑在兼容层 `babeldoc_compat.py`（表格 OCR 资格预判 **`register_document_origins_and_validate` babeldoc_compat.py:108**、OCR 段落注入 **:522**、原生表格短字段本地重建 **:399**）。
- 排查：`working/<pdf_id>/` 的 `translate_tracking.json` 里 `table_ocr_paragraphs` 是否异常（如占全部段落一半以上）。

### 6. 单段落空译文 → 完整性检查卡死任务（08-03 session，任务 `250cac…`）
- 现象：`failed`，journald 提到 empty replacement；只有 1 段空白。
- 根因：完整性校验无条件失败，哪怕只有 1 个空段。
- 已修：`fail_on_unresolved_translation` 默认 false，少量失败保留原文 + 标"完成(有警告)"，任务仍产出（server.py:158, :1166）；大面积仍阻断。

### 7. 代码已改但无效果（多会话反复出现）——**先查这个再深挖**
- 症状：明明改了 `local_babeldoc_server/*.py` 并同步到 `/home/lbz/Local-Immersive-Translate/...`，重发任务仍旧行为/旧报错。
- 根因：**后端进程还跑着旧代码**（服务由 Zotero 启动，改文件不会自动热加载）。08-03 多个任务（`3897b…`/`d46663…`）死因都是没重启；另一例是同步时部分文件没覆盖成功。
- 标准动作：
  - 三处文件一致：`/home/lbz/zotero-immersivetranslate/local_babeldoc_server/*` == `/home/lbz/Local-Immersive-Translate/local_babeldoc_server/*`（`md5sum` 对比）
  - `pgrep -af "server.py --config"` 看进程是否还活着；
  - **必须彻底退出并重开 Zotero** 让后端子进程重启，才能验证新代码。
- 这也解释了"为什么没改任何东西任务却失败/恢复"(旧进程遗留)。

## 修复 A：补 babeldoc 资源缓存（带代理跑一次 warmup）

```bash
cd /home/lbz/Local-Immersive-Translate/BabelDOC && \
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY no_proxy && \
HTTPS_PROXY=http://127.0.0.1:7897 HTTP_PROXY=http://127.0.0.1:7897 \
  .venv/bin/python -c "import asyncio; from babeldoc.assets.assets import async_warmup; asyncio.run(async_warmup())"
```
- **必须**先 unset 全部代理变量再设 http/https：shell 里已有的 `ALL_PROXY=socks://127.0.0.1:7897` 会让 httpx 抛 `Unknown scheme for proxy URL`（除非装了 httpx[socks]）
- 跑完核对：cmap 146、tiktoken 1、fonts 34
- 若 `enable_table_ocr` 开且需 rapidocr 模型，再单独取一次 `get_table_detection_rapidocr_model_path_async()`

## 收尾

- 修复后无重试接口 → **让用户在 Zotero 里对该 PDF 重新点翻译**，然后轮询状态接口确认离开 0%
- 若代码有改动：md5 三处一致 + **彻底重启 Zotero** 后再重试（不重启 = 白测）
- 若再次失败：查 journalctl 最新错误 + 新任务 usage_summary，按特征表/历史模式库再走一轮
- 用户要求"上传到 GitHub"时：先 `git -C /home/lbz/zotero-immersivetranslate status`、确认只含意图文件，提交 message 匹配仓库风格（参考历史：`fix: ...`、`perf: ...`、`docs: ...`），不建 Release/不 bump 版本除非用户明说
