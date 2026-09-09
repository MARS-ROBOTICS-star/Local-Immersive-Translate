# Local Immersive Translate for Zotero

[![zotero target version](https://img.shields.io/badge/Zotero-7--10-green?style=flat-square&logo=zotero&logoColor=CC2936)](https://www.zotero.org)
[![Using Zotero Plugin Template](https://img.shields.io/badge/Using-Zotero%20Plugin%20Template-blue?style=flat-square&logo=github)](https://github.com/windingwind/zotero-plugin-template)

一个兼容 Zotero 7 至 10、基于本地 BabelDOC 后端的 PDF 翻译插件。

Local Immersive Translate 是面向 Zotero 7 至 10 的本地 PDF 翻译插件。插件通过本机 BabelDOC 后端处理 Zotero 文献 PDF，并把翻译结果导回 Zotero。

> [!NOTE]
> 本插件兼容 Zotero 7 至 10，不兼容 Zotero 6。

## 安装

推荐方式：

1. 打开 Releases 页面：<https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate/releases>
2. 下载最新版本的 `.xpi` 文件。
3. 在 Zotero 中选择 `Tools` -> `Add-ons` -> `Install Add-on From File...`，选择刚下载的 `.xpi` 文件并安装。
4. 打开插件偏好设置，点击 `安装/修复本地后端`。插件会自动检查并安装本地后端所需环境和依赖。
5. 在插件 GUI 中填写模型 API 地址、模型名和 API Key，然后点击 `Start / Test`。

如果你已经 clone 了本项目，也可以在项目根目录运行一行命令部署本地后端。

macOS/Linux:

```bash
bash install.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器会自动检查 `uv`、项目目录、BabelDOC 和 Python 依赖。通过插件 GUI 触发安装时，用户点击按钮即表示授权安装器自动完成这些步骤。

## 使用

1. 在插件偏好设置中配置目标语言、翻译模型、翻译模式和快捷键。
2. 在 Zotero 文献列表中右键 PDF 附件，选择 `使用沉浸式翻译`。
3. 在确认窗口中检查翻译设置并提交任务。
4. 在任务管理窗口查看进度。任务完成后，点击 `查看翻译结果` 打开翻译后的 PDF。

## 本地后端

本插件后端基于 [BabelDOC](https://github.com/funstory-ai/BabelDOC)。BabelDOC 负责 PDF 解析、版面保持和翻译文件生成，本项目提供 Zotero 插件界面、本地服务封装和跨平台安装脚本。

Local Immersive Translate v0.0.27 默认使用并锁定 BabelDOC v0.6.4。已有安装可在插件偏好设置中再次点击 `安装/修复本地后端`，将本地 BabelDOC 更新到当前支持的版本。

默认安装路径：

- Windows: `%USERPROFILE%\Local-Immersive-Translate`
- macOS/Linux: `$HOME/Local-Immersive-Translate`

插件通常会自动检测这些路径。只有在使用自定义安装位置，或自动检测失败时，才需要在高级设置中手动填写项目目录和 `uv` 路径。

后端配置和调试说明见 [local_babeldoc_server/README.md](local_babeldoc_server/README.md)。

## Agent 调试 Skill

仓库内置了一个供 AI 编码助手（Agent）使用的排查 skill：`zotero-translate-triage`。翻译任务失败/卡住时，把任务 ID（32 位 hex）发给已安装该 skill 的 Agent，它会按固定流程快速筛查状态接口、usage 统计、工作目录、资源缓存和服务日志，直接定位失败原因（资源下载网络失败、模型 API 错误、请求额度耗尽等），并给出对应修复。

- 本 skill 面向 opencode（也兼容 Claude Code / Codex 等支持 skill 的 Agent）。
- 仓库内文件：`.opencode/skills/zotero-translate-triage/SKILL.md`。

### 自动安装（推荐）

在插件偏好设置点击 `安装/修复本地后端` 时，安装器会自动把该 skill 复制到本机 Agent 的 skill 目录，无需手动操作。重开 opencode（或其他 Agent）后即可使用。

### 用 Agent 帮忙安装

把下面这段文字**原样复制发给你的 Agent**（opencode / Claude Code / Codex 均可），它会自动完成下载和安装：

> 请帮我安装 `zotero-translate-triage` 调试 skill：下载 <https://raw.githubusercontent.com/MARS-ROBOTICS-star/Local-Immersive-Translate/main/.opencode/skills/zotero-translate-triage/SKILL.md> 并保存到我的 Agent skill 目录（opencode 为 `~/.config/opencode/skills/zotero-translate-triage/SKILL.md`，Claude Code 为 `~/.claude/skills/zotero-translate-triage/SKILL.md`）。

安装完成后重启 Agent 即可生效。

## 快捷键

- `Ctrl+Shift+B`（macOS 为 `Cmd+Shift+B`）：翻译选中的文献。
- `Ctrl+Shift+H`（macOS 为 `Cmd+Shift+H`）：打开任务管理窗口。
- 可在插件设置页修改、清空或恢复默认快捷键；清空某一项只会停用对应动作。

## 任务恢复

如果翻译开始后关闭 Zotero，插件会保存未完成任务。再次打开 Zotero 后，插件会自动恢复未完成任务；已完成任务不会继续保存。

## FAQ

### 点击 Start / Test 失败怎么办？

请先在插件偏好设置中点击 `安装/修复本地后端`，然后确认模型 API 地址、API Key 和模型名正确。

### 翻译失败怎么办？

请检查模型 API 地址、API Key、模型名和网络连接。也可以切换模型后重新提交翻译任务。

### 不小心关闭了任务管理窗口怎么办？

可以在 Zotero 的 `查看` 菜单下，点击 `查看沉浸式翻译任务`，重新打开任务管理窗口。

## License

本项目当前使用 AGPL-3.0-or-later 协议。
