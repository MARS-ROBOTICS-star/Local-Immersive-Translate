# Local Immersive Translate for Zotero

[![zotero target version](https://img.shields.io/badge/Zotero-7-green?style=flat-square&logo=zotero&logoColor=CC2936)](https://www.zotero.org)
[![Using Zotero Plugin Template](https://img.shields.io/badge/Using-Zotero%20Plugin%20Template-blue?style=flat-square&logo=github)](https://github.com/windingwind/zotero-plugin-template)

一个基于本地 BabelDOC 后端的 Zotero 7 PDF 翻译插件。

Local Immersive Translate 是面向 Zotero 7 的本地 PDF 翻译插件。插件通过本机 BabelDOC 后端处理 Zotero 文献 PDF，并把翻译结果导回 Zotero。

> [!NOTE]
> 本插件基于 Zotero 7 开发，不兼容 Zotero 6。

## 安装

1. 打开 Releases 页面：<https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate/releases>
2. 下载最新版本的 `.xpi` 文件。
3. 在 Zotero 7 中选择 `Tools` -> `Add-ons` -> `Install Add-on From File...`，选择刚下载的 `.xpi` 文件并安装。
4. 运行本地后端安装脚本。

macOS/Linux:

```bash
curl -fsSLO https://raw.githubusercontent.com/MARS-ROBOTICS-star/Local-Immersive-Translate/main/scripts/install-local-backend.sh
bash install-local-backend.sh
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/MARS-ROBOTICS-star/Local-Immersive-Translate/main/scripts/install-local-backend.ps1 -OutFile install-local-backend.ps1
powershell -ExecutionPolicy Bypass -File .\install-local-backend.ps1
```

5. 打开 Zotero 插件偏好设置，填写模型 API 配置，然后点击 `Start / Test`。

如果系统尚未安装 `uv`，安装脚本会先提示确认，然后再下载并执行官方 `uv` 安装器。

## 使用

1. 在插件偏好设置中配置目标语言、翻译模型、翻译模式和快捷键。
2. 在 Zotero 文献列表中右键 PDF 附件，选择 `使用沉浸式翻译`。
3. 在确认窗口中检查翻译设置并提交任务。
4. 在任务管理窗口查看进度。任务完成后，点击 `查看翻译结果` 打开翻译后的 PDF。

## 本地后端

本插件后端基于 [BabelDOC](https://github.com/funstory-ai/BabelDOC)。BabelDOC 负责 PDF 解析、版面保持和翻译文件生成，本项目提供 Zotero 插件界面、本地服务封装和跨平台安装脚本。

默认安装路径：

- Windows: `%USERPROFILE%\Local-Immersive-Translate`
- macOS/Linux: `$HOME/Local-Immersive-Translate`

插件通常会自动检测这些路径。只有在使用自定义安装位置，或自动检测失败时，才需要在高级设置中手动填写项目目录和 `uv` 路径。

后端配置和调试说明见 [local_babeldoc_server/README.md](local_babeldoc_server/README.md)。

## 快捷键

- `Ctrl+Shift+B`（macOS 为 `Cmd+Shift+B`）：翻译选中的文献。
- `Ctrl+Shift+H`（macOS 为 `Cmd+Shift+H`）：打开任务管理窗口。
- 可在插件设置页修改、清空或恢复默认快捷键；清空某一项只会停用对应动作。

## 任务恢复

如果翻译开始后关闭 Zotero，插件会保存未完成任务。再次打开 Zotero 后，插件会自动恢复未完成任务；已完成任务不会继续保存。

## FAQ

### 点击 Start / Test 失败怎么办？

请确认本地后端安装脚本已经运行完成，偏好设置中的项目目录和 `uv` 路径正确，且当前模型 API 配置可用。

### 翻译失败怎么办？

请检查模型 API 地址、API Key、模型名和网络连接。也可以切换模型后重新提交翻译任务。

### 不小心关闭了任务管理窗口怎么办？

可以在 Zotero 的 `查看` 菜单下，点击 `查看沉浸式翻译任务`，重新打开任务管理窗口。

## License

本项目当前使用 AGPL-3.0-or-later 协议。


简单来说，AGPL 更强调“修改后的代码也继续开源”，MIT 更强调“使用限制尽量少”。
