startup-begin = Addon is loading
startup-finish = Addon is ready
menuitem-translate = Translate with Local Immersive Translate(Ctrl/Cmd+Shift+B)
menuView-tasks = View Local Immersive Translate tasks(Ctrl/Cmd+Shift+H)
pref-test-success = Test successfully
pref-test-failed = Test failed
pref-test-failed-description = Please check the local service URL, project directory, uv path, or model API configuration.
pref-test-starting = Starting/testing the local BabelDOC service...
pref-test-model-starting = Testing the selected model API...
pref-test-model-success = Selected model API is reachable.
pref-test-running = Local BabelDOC service is reachable.
pref-test-started = Local BabelDOC service started and is reachable.
pref-test-autostart-disabled = The local service is not running and automatic startup is disabled.
pref-test-autostart-failed = Failed to start local BabelDOC service: { $reason }
pref-local-model-provider = Model
pref-local-model-base-url = API URL
pref-local-model-name = Model name
pref-local-model-api-key = API Key
pref-shortcut-duplicate-error = This shortcut is already used by another plugin action. Plugin shortcuts must be unique.
pref-shortcut-native-conflict-error = Shift+letter is reserved by Zotero item-list navigation and cannot be used as a plugin shortcut.
pref-shortcut-conflict-guidance = Custom shortcuts may conflict with Zotero or system shortcuts. Conflicting values are allowed, but Zotero or the system may handle them first.

prefs-title = Local Immersive Translate
item-filed-status = Translation Status

translateMode-all = Bilingual mode & Translation only
translateMode-dua = Bilingual mode
translateMode-translation = Translation only

translateModel-qwen = Qwen
translateModel-kimi = Kimi
translateModel-deepseek = DeepSeek
translateModel-glm-4-plus = GLM 4.7
translateModel-OpenAI = OpenAI
translateModel-Gemini = Gemini
translateModel-glm-4-flash = GLM-4-Flash

auto = Auto
ocr_workaround_enable = Enable
ocr_workaround_disable = Disable

font_family_serif = Serif
font_family_sans-serif = Sans-serif
font_family_script = Script

dual_mode_lort = Side-by-side: Original | Translation
dual_mode_ltro = Side-by-side: Translation | Original
dual_mode_uodt = Page Alternation: Original Text First
dual_mode_utdo = Page Alternation: Translation First

layoutModel-version-2 = Version 2
layoutModel-version-3 = Version 3

confirm-title = Translate Confirm
confirm-options = Options
confirm-article-single = Selected article
confirm-article-multiple = { $count } selected items. First item shown below.
confirm-article-unknown = Untitled article
confirm-article-metadata-unknown = Author/year unavailable
confirm-enable-compatibility = Enable compatibility mode
confirm-enable-compatibility-description = Enabling this will improve PDF compatibility, but will increase the output file size.
confirm-enable-ocr-workaround = Enable OCR temporary solution
confirm-enable-ocr-workaround-description = When your scanned/image-based PDF file has undergone OCR processing and is in black text on a white background, you can try enabling the OCR version of the temporary solution. This solution will add white rectangular blocks below the translated text to cover the original content.
confirm-translate-model = Translation model
confirm-translate-mode = Translation mode
confirm-target-language = Target language
confirm-yes = Confirm
confirm-cancel = Cancel

task-no-pdf = No PDF found for translation

column-item = Item
column-attachment = Attachment
column-target-language = Target Language
column-translate-model = Translation Model
column-translate-mode = Translation Mode
column-pdfId =  Task ID
column-status = Task Status
column-stage = Current Stage
column-progress = Translate Progress
column-error = Error Message

task-uncomplete = Task not completed
task-select-tip = Please select a task
task-copy-success = Task ID Copied!
task-cancel-success = Task canceled successfully
task-cancel-tip = Only unstarted tasks can be canceled

task-status-queued = Pending start
task-status-uploading = Uploading
task-status-translating = Translating
task-status-success = Success
task-status-failed = Failed
task-status-canceled = Canceled

task-stage-queued = Queuing
task-stage-uploading = Uploading PDF
task-stage-parse-pdf = Parsing PDF
task-stage-DetectScannedFile= Checking if it is a scanned version
task-stage-ParseLayout= Parsing page layout
task-stage-ParseParagraphs= Parsing paragraphs
task-stage-ParseFormulasAndStyles = Parsing formulas & styles
task-stage-RemoveCharDescent = Correcting character offset
task-stage-TranslateParagraphs = Translating paragraphs
task-stage-Typesetting = Typesetting
task-stage-AddFonts = Adding fonts
task-stage-GenerateDrawingInstructions = Exporting PDF
task-stage-SubsetFont = Subsetting font
task-stage-SavePDF = Generating PDF
task-stage-prepareFileDownload = Processing file
task-stage-ParseTable = Parsing table
task-stage-WaitingInLine = Queuing
task-stage-CreateTask = Creating task
task-stage-downloading = Downloading
task-stage-completed = Translate completed

task-retry-success = Task retry queued successfully
task-retry-tip = Only failed tasks can be retried

# Network and download related strings
network-slow-title = Slow Network Access
network-slow-message = The translation service connection is unstable. Check your network connection or switch to the local BabelDOC backend.
network-slow-dont-remind = Don't remind again
download-failed = Download failed
