# Local BabelDOC Backend

This folder contains the local backend used by the Zotero plugin. It exposes a Zotero-compatible API, runs BabelDOC on this machine, and returns translated PDF files to the plugin.

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

Keep `Local service URL` as `http://127.0.0.1:8765/zotero`, fill the model API configuration for the providers you plan to use, then click `Start / Test`.

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

## Notes

- The local backend generates both translation-only and dual-language PDFs so the plugin's `dual`, `translation`, and `all` modes continue to work.
- BabelDOC is AGPL-3.0. Local personal use is straightforward; redistribution or providing a network service has source-code obligations.
