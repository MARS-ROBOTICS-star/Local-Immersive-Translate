# Local BabelDOC Backend

This folder contains the local backend used by the Zotero plugin. It exposes a Zotero-compatible API, runs BabelDOC on this machine, and returns translated PDF files to the plugin.

Local Immersive Translate v0.0.25 uses and pins BabelDOC v0.6.4. Run the installer again, or click `Install / Repair Local Backend` in Zotero preferences, to update an existing checkout to the supported version.

The backend also applies structure-aware PDF repairs by default: native table
text remains translatable, table regions without enough extractable text use
RapidOCR, `References` / `Bibliography` headings and entries remain in the
source language, and table-of-contents entries translate only their titles
while retaining numbering, dot leaders, indentation, and page numbers.

## What It Provides

- `GET /zotero/check-key`
- `GET /zotero/pdf-upload-url`
- `PUT /zotero/upload/{objectKey}`
- `POST /zotero/backend-babel-pdf`
- `GET /zotero/pdf/{pdfId}/process`
- `GET /zotero/pdf/{pdfId}/temp-url`
- `GET /zotero/download/{pdfId}/{translation|dual}`

The Zotero plugin can keep its task UI, progress polling, download, and attachment import behavior while all translation work runs locally.

## Default Install Paths

The installer uses these default locations:

- Windows: `%USERPROFILE%\Local-Immersive-Translate`
- macOS/Linux: `$HOME/Local-Immersive-Translate`

The plugin normally detects these paths automatically. In Zotero preferences, leave the advanced backend fields empty unless you installed the backend somewhere else or automatic detection fails.

In Zotero preferences, click `Install / Repair Local Backend` to deploy or repair the local environment automatically. Then keep `Local service URL` as `http://127.0.0.1:8765/zotero`, fill the model API configuration for the providers you plan to use, and click `Start / Test`.

If you cloned this repository locally, you can also run the installer from the project root:

macOS/Linux:

```bash
bash install.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

## Manual Startup

Manual CLI startup is useful for debugging.

macOS/Linux:

```bash
cd "$HOME/Local-Immersive-Translate/BabelDOC"
uv run python ../local_babeldoc_server/server.py \
  --config ../local_babeldoc_server/config.example.json
```

Windows PowerShell:

```powershell
cd "$env:USERPROFILE\Local-Immersive-Translate\BabelDOC"
uv run python ..\local_babeldoc_server\server.py `
  --config ..\local_babeldoc_server\config.example.json
```

## Model Config

The Zotero plugin preferences expose the same model keys used by the local backend:

- `kimi`
- `qwen-1`
- `deepseek`
- `glm-paid-1`
- `gpt-1`
- `gemini-1`
- `glm-free-1`

For each model you want to call, fill these fields in the Zotero plugin UI:

```json
{
  "base_url": "",
  "api_key": "",
  "model": ""
}
```

The plugin sends only the selected model's configuration with each translation task. Defaults are intentionally empty, so users must provide their own model API settings.

When configuring models in JSON instead of the plugin UI, `api_key` can be a literal key or `env:VARIABLE_NAME`.

For DeepSeek-compatible APIs, the model JSON can also include `"thinking": "enabled"` or `"thinking": "disabled"`. This is an advanced backend-only setting and does not change the Zotero preferences UI.

## Notes

- The local backend generates both translation-only and dual-language PDFs so the plugin's `dual`, `translation`, and `all` modes continue to work.
- Table OCR is region-scoped and lazy at runtime. The installer installs and prewarms RapidOCR; rerun `Install / Repair Local Backend` if an existing installation reports that RapidOCR is missing.
- Advanced JSON configuration can disable individual repairs with `enable_table_ocr`, `preserve_references`, or `preserve_toc_layout` under `babeldoc`.
- Translation completeness protection is enabled by default. Empty, truncated, unchanged, or structurally lossy model output is rejected before it can replace a source paragraph. Rejected paragraphs are retried in sentence-aligned chunks configured by `translation_retry_chunk_sizes` (default `[700, 350]`).
- `fail_on_unresolved_translation` defaults to `true`. If bounded retries still leave a translatable paragraph unresolved, the original paragraph is preserved and the task is marked failed instead of publishing a silently incomplete PDF. References intentionally preserved in their original language are excluded from this audit.
- BabelDOC is AGPL-3.0. Local personal use is straightforward; redistribution or providing a network service has source-code obligations.
