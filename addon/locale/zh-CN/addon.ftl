startup-begin = 插件加载中
startup-finish = 插件已就绪
menuitem-translate = 使用本地沉浸式翻译(Ctrl/Cmd+Shift+B)
menuView-tasks = 查看本地沉浸式翻译任务(Ctrl/Cmd+Shift+H)
pref-test-success = 测试成功
pref-test-failed = 测试失败
pref-test-failed-description = 请检查本地服务地址、项目目录、uv 路径或模型 API 配置
pref-test-starting = 正在启动/测试本地 BabelDOC 服务...
pref-test-model-starting = 正在测试当前模型 API...
pref-test-model-success = 当前模型 API 连接正常
pref-test-running = 本地 BabelDOC 服务连接正常
pref-test-started = 本地 BabelDOC 服务已启动并连接成功
pref-test-autostart-disabled = 本地服务未运行，且自动启动已关闭
pref-test-autostart-failed = 无法启动本地 BabelDOC 服务：{ $reason }
pref-local-model-provider = 模型
pref-local-model-base-url = API 地址
pref-local-model-name = 模型名
pref-local-model-api-key = API Key
pref-shortcut-duplicate-error = 该快捷键已被另一个插件动作使用。插件快捷键不能重复。
pref-shortcut-native-conflict-error = Shift+字母 是 Zotero 条目列表原生跳转功能，不能作为插件快捷键。
pref-shortcut-conflict-guidance = 自定义快捷键可能与 Zotero 或系统快捷键冲突。插件不会因此阻止保存，但 Zotero 或系统可能会优先处理。

prefs-title = 本地沉浸式翻译
item-filed-status = 翻译状态

translateMode-all = 双语模式 & 译文模式
translateMode-dua = 双语模式
translateMode-translation = 仅译文

translateModel-qwen = Qwen
translateModel-kimi = Kimi
translateModel-deepseek = DeepSeek
translateModel-glm-4-plus = GLM 4.7
translateModel-OpenAI = OpenAI
translateModel-Gemini = Gemini
translateModel-glm-4-flash = 智谱 4 Flash

auto = 自动
ocr_workaround_enable = 开启
ocr_workaround_disable = 关闭

font_family_serif = 宋体
font_family_sans-serif = 黑体
font_family_script = 楷体

dual_mode_lort = 左右对照：原文｜译文
dual_mode_ltro = 左右对照：译文｜原文
dual_mode_uodt = 页面交替：原文在前
dual_mode_utdo = 页面交替：译文在前

layoutModel-version-2 = 版本 2
layoutModel-version-3 = 版本 3

confirm-title = 翻译确认
confirm-options = 选项
confirm-article-single = 当前选择的文章
confirm-article-multiple = 已选择 { $count } 个条目。下方显示第一项。
confirm-article-unknown = 未命名文章
confirm-article-metadata-unknown = 作者/年份不可用
confirm-enable-compatibility = 是否启用兼容模式
confirm-enable-compatibility-description = 启用后将会改善 PDF 兼容性，但是会增大输出文件大小
confirm-enable-ocr-workaround = 是否启用 OCR 临时解决方案
confirm-enable-ocr-workaround-description = 当您的扫描/图片版 PDF 文件已进行 OCR 处理，且为白底黑字时，可以尝试启用 OCR 版的临时解决方案。该方案将在译文下方添加白色矩形块，以覆盖原文内容。
confirm-translate-model = 翻译模型
confirm-translate-mode = 翻译模式
confirm-target-language = 目标语言
confirm-yes = 确认
confirm-cancel = 取消

task-no-pdf = 没有找到可以翻译的 PDF

column-item = 条目
column-attachment = 附件
column-target-language = 目标语言
column-translate-model = 翻译模型
column-translate-mode = 翻译模式
column-pdfId =  任务 ID
column-status = 任务状态
column-stage = 当前阶段
column-progress = 翻译进度
column-error = 错误信息
column-resultAttachmentId = 结果附件 ID

task-uncomplete = 任务未完成
task-select-tip = 请选择一个任务
task-copy-success = 复制任务 ID 成功！
task-cancel-success = 取消任务成功
task-cancel-tip = 只能取消未开始的任务

task-status-queued = 未开始
task-status-uploading = 上传中
task-status-translating = 翻译中
task-status-success = 成功
task-status-failed = 失败
task-status-canceled = 已取消

task-stage-queued = 排队中
task-stage-uploading = 正在上传 PDF
task-stage-parse-pdf = 正在解析 PDF
task-stage-DetectScannedFile= 正在检测是否为扫描版
task-stage-ParseLayout= 正在解析页面布局
task-stage-ParseParagraphs= 正在解析段落
task-stage-ParseFormulasAndStyles = 正在解析公式&样式
task-stage-RemoveCharDescent = 正在修正字符偏移量
task-stage-TranslateParagraphs = 正在翻译段落
task-stage-Typesetting = 正在排版
task-stage-AddFonts = 正在添加字体
task-stage-GenerateDrawingInstructions = 正在导出 PDF 页面
task-stage-SubsetFont = 正在子集化字体
task-stage-SavePDF = 正在生成 PDF 文件
task-stage-prepareFileDownload = 正在处理文件
task-stage-ParseTable = 正在解析表格
task-stage-WaitingInLine = 正在排队
task-stage-CreateTask = 正在创建任务
task-stage-downloading = 下载中
task-stage-completed = 翻译完成

task-retry-success = 重试任务已成功加入队列
task-retry-tip = 只能重试失败的任务

# Network and download related strings
network-slow-title = 当前网络访问较慢
network-slow-message = 当前翻译服务连接不稳定，请检查网络连接或切换到本地 BabelDOC 后端。
network-slow-dont-remind = 不再提醒
download-failed = 下载失败
