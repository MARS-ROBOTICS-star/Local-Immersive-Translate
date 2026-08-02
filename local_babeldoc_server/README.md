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

For Gemini, fill the API key and model in the Zotero plugin UI. The backend uses
the pinned Google Gen AI SDK, the stateless Interactions v1 surface
(`store=false`), strict JSON schema responses, and `thinking_level=minimal`.

For an OpenAI-compatible provider, fill these connection fields in the Zotero
plugin UI:

```json
{
  "base_url": "",
  "api_key": "",
  "model": ""
}
```

The plugin sends only the selected model's connection configuration with each
translation task. Defaults are intentionally empty, so users must provide their
own model API settings.

When configuring models in JSON instead of the plugin UI, `api_key` can be a literal key or `env:VARIABLE_NAME`.

OpenAI-compatible models also require an authoritative server-side capability
and price declaration. Unknown prices are rejected instead of silently disabling
the cost budget. A non-reasoning model should omit reasoning control; a model
whose default billable reasoning cannot be controlled is rejected.

```json
{
  "provider": "compatible",
  "api_surface": "openai-chat-completions",
  "input_usd_per_million": "replace-with-current-price",
  "output_usd_per_million": "replace-with-current-price",
  "cached_input_usd_per_million": "replace-with-current-price",
  "default_billable_reasoning": false,
  "capabilities": {
    "supports_structured_output": true,
    "supports_reasoning_control": false,
    "supported_reasoning_levels": [],
    "exposes_reasoning_usage": false,
    "supports_cached_usage": false,
    "supports_count_tokens": false,
    "supports_request_id": true
  }
}
```

`POST /zotero/test-model` now exercises the same budgeted runtime and strict
schema contract as document translation. It performs a real provider request
only when the user explicitly invokes the model test.

## Cost and Usage Audit

Every physical provider request goes through one runtime gateway. Each job keeps
`api_calls.jsonl` and an atomic `usage_summary.json` under its working directory;
successful jobs copy both files to their output directory. The audit records the
request category, stable paragraph IDs, semantic and transport attempt numbers,
billing certainty, normalized usage, reserved and settled cost, and a frozen
price snapshot. Source text and prompts are not persisted—only hashes are kept.

The default document limits are 150 physical requests, JPY 300 estimated cost,
two semantic attempts per paragraph, and two potentially billable exposures per
paragraph. Reservations include in-flight requests and worst-case output cost.
On an unknown-billing timeout, the full reservation remains charged
conservatively.

## Notes

- The local backend generates both translation-only and dual-language PDFs so the plugin's `dual`, `translation`, and `all` modes continue to work.
- Table OCR is region-scoped and lazy at runtime. The installer installs and prewarms RapidOCR; rerun `Install / Repair Local Backend` if an existing installation reports that RapidOCR is missing.
- Advanced JSON configuration can disable individual repairs with `enable_table_ocr`, `preserve_references`, or `preserve_toc_layout` under `babeldoc`.
- Translation completeness protection is enabled by default. Empty, truncated, unchanged, or structurally lossy model output is rejected before it can replace a source paragraph. Under the budgeted runtime the old chained 700/350-character quality retries are disabled; batch failures receive one stable-ID single-paragraph fallback, so a paragraph can have at most two billable semantic attempts.
- OCR provenance is classified before term extraction or translation. Born-digital and hybrid documents use ratio checks, scanned documents skip the global OCR/native ratio, and duplicate OCR injection requires both coordinate overlap and text similarity. Unsafe OCR aborts the runtime before any provider request.
- If a model returns complete text but drops malformed rich-text-only `<style>` markers, recovery may render that paragraph with its base font style. Formula placeholders, citations, URLs, DOI values, and independent numeric values remain mandatory; content completeness takes priority over isolated font styling.
- `fail_on_unresolved_translation` defaults to `true`. If bounded retries still leave a translatable paragraph unresolved, the original paragraph is preserved and the task is marked failed instead of publishing a silently incomplete PDF. References intentionally preserved in their original language are excluded from this audit.
- BabelDOC is AGPL-3.0. Local personal use is straightforward; redistribution or providing a network service has source-code obligations.
