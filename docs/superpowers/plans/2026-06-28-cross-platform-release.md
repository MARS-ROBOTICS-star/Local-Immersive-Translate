# Cross-Platform Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the Zotero plugin from `MARS-ROBOTICS-star/Local-Immersive-Translate` and make its local BabelDOC backend installable on Windows, macOS, and Linux without hard-coded developer paths.

**Architecture:** Keep the Zotero `.xpi` as the plugin release artifact. Add separate installer scripts that clone/update the backend project into a standard user directory, install `uv` when missing, and prepare the BabelDOC runtime. Update the plugin to auto-detect the standard project directory and `uv` path while retaining advanced manual override fields.

**Tech Stack:** Zotero 7 plugin scaffold, TypeScript, XUL/XHTML preferences UI, GitHub Actions, Bash, PowerShell, Python/BabelDOC, `uv`.

---

## File Structure

- Modify `package.json`: public package metadata, repository URLs, author, XPI name if needed.
- Modify `.github/workflows/ci.yml`: artifact path and token usage.
- Modify `.github/workflows/release.yml`: release token usage, installer asset availability, release trigger behavior.
- Create `scripts/install-local-backend.sh`: macOS/Linux network installer.
- Create `scripts/install-local-backend.ps1`: Windows network installer.
- Modify `src/modules/local-backend.ts`: portable detection for project directory and `uv`.
- Modify `addon/prefs.js`: remove `/home/lbz/...` defaults.
- Modify `addon/content/preferences.xhtml`: mark project path and `uv` path as advanced fallback settings.
- Modify locale files under `addon/locale/en-US` and `addon/locale/zh-CN`: remove feedback labels and add portable backend guidance.
- Modify `addon/content/taskManager.xhtml`: remove feedback button.
- Modify `src/modules/translate/task-manager.ts`: remove feedback click handler and unused imports.
- Modify `README.md`: user install flow for XPI plus backend installer.
- Modify `local_babeldoc_server/README.md`: replace `/home/lbz/...` examples with portable install instructions.

## Task 1: Release Metadata and GitHub Workflows

**Files:**

- Modify: `package.json`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Update package metadata**

In `package.json`, replace repository metadata with:

```json
{
  "name": "local-immersive-translate",
  "description": "Zotero local BabelDOC PDF translation plugin.",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate.git"
  },
  "author": "MARS-ROBOTICS-star",
  "bugs": {
    "url": "https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate/issues"
  },
  "homepage": "https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate#readme"
}
```

Use the new local plugin identity in `config.addonID`, `config.addonRef`, and `config.prefsPrefix` so releases do not retain the upstream add-on namespace.

- [ ] **Step 2: Fix workflow token casing**

In `.github/workflows/ci.yml` and `.github/workflows/release.yml`, use:

```yaml
env:
  GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Remove references to `${{ secrets.GitHub_TOKEN }}`.

- [ ] **Step 3: Fix CI artifact path**

In `.github/workflows/ci.yml`, replace:

```yaml
path: |
  build
```

with:

```yaml
path: |
  dist
```

- [ ] **Step 4: Verify metadata and workflow syntax**

Run:

```bash
pnpm build
```

Expected: build completes and TypeScript reports no errors.

- [ ] **Step 5: Commit**

```bash
git add package.json .github/workflows/ci.yml .github/workflows/release.yml
git commit -m "chore: update release metadata for new repository"
```

## Task 2: macOS/Linux Local Backend Installer

**Files:**

- Create: `scripts/install-local-backend.sh`

- [ ] **Step 1: Create installer script**

Create `scripts/install-local-backend.sh` with executable Bash code that:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/Local-Immersive-Translate}"
BABELDOC_URL="${BABELDOC_URL:-https://github.com/funstory-ai/BabelDOC.git}"

need_command() {
  command -v "$1" >/dev/null 2>&1
}

if ! need_command git; then
  echo "Git is required. Install Git, then run this installer again." >&2
  exit 1
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

if ! need_command uv; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! need_command uv; then
  echo "uv installation did not finish successfully. Install uv manually from https://docs.astral.sh/uv/ and rerun this script." >&2
  exit 1
fi

if [ -d "$INSTALL_DIR/BabelDOC/.git" ]; then
  git -C "$INSTALL_DIR/BabelDOC" pull --ff-only
else
  rm -rf "$INSTALL_DIR/BabelDOC"
  git clone "$BABELDOC_URL" "$INSTALL_DIR/BabelDOC"
fi

uv --directory "$INSTALL_DIR/BabelDOC" sync

cat <<EOF
Local backend installed.

Project directory:
$INSTALL_DIR

uv path:
$(command -v uv)

Open Zotero plugin preferences and click Start / Test.
EOF
```

- [ ] **Step 2: Make script executable**

Run:

```bash
chmod +x scripts/install-local-backend.sh
```

- [ ] **Step 3: Syntax check**

Run:

```bash
bash -n scripts/install-local-backend.sh
```

Expected: no output and exit code 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/install-local-backend.sh
git commit -m "feat: add macos linux backend installer"
```

## Task 3: Windows Local Backend Installer

**Files:**

- Create: `scripts/install-local-backend.ps1`

- [ ] **Step 1: Create PowerShell installer**

Create `scripts/install-local-backend.ps1` with:

```powershell
param(
  [string]$RepoUrl = "https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate.git",
  [string]$InstallDir = "$env:USERPROFILE\Local-Immersive-Translate",
  [string]$BabelDocUrl = "https://github.com/funstory-ai/BabelDOC.git"
)

$ErrorActionPreference = "Stop"

function Require-Command($Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name is required. Install $Name and run this installer again."
  }
}

Require-Command git

if (Test-Path "$InstallDir\.git") {
  git -C $InstallDir pull --ff-only
} else {
  git clone $RepoUrl $InstallDir
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv installation did not finish successfully. Install uv from https://docs.astral.sh/uv/ and rerun this script."
}

if (Test-Path "$InstallDir\BabelDOC\.git") {
  git -C "$InstallDir\BabelDOC" pull --ff-only
} else {
  if (Test-Path "$InstallDir\BabelDOC") {
    Remove-Item "$InstallDir\BabelDOC" -Recurse -Force
  }
  git clone $BabelDocUrl "$InstallDir\BabelDOC"
}

uv --directory "$InstallDir\BabelDOC" sync

Write-Host ""
Write-Host "Local backend installed."
Write-Host ""
Write-Host "Project directory:"
Write-Host $InstallDir
Write-Host ""
Write-Host "uv path:"
Write-Host (Get-Command uv).Source
Write-Host ""
Write-Host "Open Zotero plugin preferences and click Start / Test."
```

- [ ] **Step 2: Parse check**

Run on a machine with PowerShell:

```bash
pwsh -NoProfile -Command '$null = [scriptblock]::Create((Get-Content -Raw scripts/install-local-backend.ps1)); "ok"'
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add scripts/install-local-backend.ps1
git commit -m "feat: add windows backend installer"
```

## Task 4: Portable Local Backend Path Detection

**Files:**

- Modify: `src/modules/local-backend.ts`
- Modify: `addon/prefs.js`
- Modify: `typings/prefs.d.ts` only if preference names change

- [ ] **Step 1: Remove machine-specific pref defaults**

In `addon/prefs.js`, replace:

```js
pref("localBackendProjectDir", "<legacy developer project path>");
pref("localBackendUvPath", "<legacy developer uv path>");
```

with:

```js
pref("localBackendProjectDir", "");
pref("localBackendUvPath", "");
```

- [ ] **Step 2: Add portable path helpers**

In `src/modules/local-backend.ts`, replace `DEFAULT_PROJECT_DIR` and
`DEFAULT_UV_PATH` with helper functions:

```ts
const LEGACY_PROJECT_DIR = "<legacy developer project path>";
const LEGACY_UV_PATH = "<legacy developer uv path>";
const DEFAULT_LOCAL_BACKEND_URL = "http://127.0.0.1:8765/zotero";

function getEnv(name: string): string {
  try {
    return Services.env.get(name) || "";
  } catch {
    return "";
  }
}

function getHomeDir(): string {
  return getEnv("HOME") || getEnv("USERPROFILE");
}

function pathJoin(...parts: string[]) {
  if (typeof PathUtils !== "undefined") {
    return PathUtils.join(...parts);
  }
  const sep = Zotero.isWin ? "\\" : "/";
  return parts
    .filter(Boolean)
    .map((part, index) =>
      index === 0
        ? part.replace(/[\\/]+$/, "")
        : part.replace(/^[\\/]+|[\\/]+$/g, ""),
    )
    .join(sep);
}

function readableFile(path: string): boolean {
  try {
    const file = Zotero.File.pathToFile(path);
    return file.exists() && file.isReadable();
  } catch {
    return false;
  }
}

function readableDirectory(path: string): boolean {
  try {
    const file = Zotero.File.pathToFile(path);
    return file.exists() && file.isReadable() && file.isDirectory();
  } catch {
    return false;
  }
}

function isLegacyPath(path: unknown, legacy: string): boolean {
  return typeof path === "string" && path.trim() === legacy;
}

function findProjectDir(existing: unknown): string {
  const current = typeof existing === "string" ? existing.trim() : "";
  if (
    current &&
    !isLegacyPath(current, LEGACY_PROJECT_DIR) &&
    readableDirectory(pathJoin(current, "BabelDOC"))
  ) {
    return current;
  }
  const home = getHomeDir();
  const candidate = home ? pathJoin(home, "Local-Immersive-Translate") : "";
  if (candidate && readableDirectory(pathJoin(candidate, "BabelDOC"))) {
    return candidate;
  }
  return current && !isLegacyPath(current, LEGACY_PROJECT_DIR) ? current : "";
}

function findExecutableOnPath(name: string): string {
  const pathValue = getEnv("PATH");
  const sep = Zotero.isWin ? ";" : ":";
  for (const dir of pathValue.split(sep)) {
    const candidate = pathJoin(dir, name);
    if (readableFile(candidate)) {
      return candidate;
    }
  }
  return "";
}

function findUvPath(existing: unknown): string {
  const current = typeof existing === "string" ? existing.trim() : "";
  if (
    current &&
    !isLegacyPath(current, LEGACY_UV_PATH) &&
    readableFile(current)
  ) {
    return current;
  }
  const fromPath = findExecutableOnPath(Zotero.isWin ? "uv.exe" : "uv");
  if (fromPath) {
    return fromPath;
  }
  const home = getHomeDir();
  const candidate = home
    ? pathJoin(home, ".local", "bin", Zotero.isWin ? "uv.exe" : "uv")
    : "";
  if (candidate && readableFile(candidate)) {
    return candidate;
  }
  return current && !isLegacyPath(current, LEGACY_UV_PATH) ? current : "";
}
```

- [ ] **Step 3: Normalize prefs with detected values**

Update `normalizeLocalBackendPrefs()`:

```ts
export function normalizeLocalBackendPrefs() {
  const localBackendUrl = getPref("localBackendUrl");
  const detectedProjectDir = findProjectDir(getPref("localBackendProjectDir"));
  const detectedUvPath = findUvPath(getPref("localBackendUvPath"));

  if (isUnsetValue(localBackendUrl)) {
    setPref("localBackendUrl", DEFAULT_LOCAL_BACKEND_URL);
  }
  if (detectedProjectDir !== getPref("localBackendProjectDir")) {
    setPref("localBackendProjectDir", detectedProjectDir);
  }
  if (detectedUvPath !== getPref("localBackendUvPath")) {
    setPref("localBackendUvPath", detectedUvPath);
  }
  if (typeof getPref("localBackendAutoStart") !== "boolean") {
    setPref("localBackendAutoStart", true);
  }
}
```

- [ ] **Step 4: Use cross-platform path joining for backend paths**

Replace uses of `joinPath(...)` in `startLocalBackendProcess()` with `pathJoin(...)`.

- [ ] **Step 5: Build verification**

Run:

```bash
pnpm build
```

Expected: TypeScript compiles with no errors.

- [ ] **Step 6: Commit**

```bash
git add src/modules/local-backend.ts addon/prefs.js typings/prefs.d.ts
git commit -m "feat: auto-detect local backend paths"
```

## Task 5: Preferences UI Guidance

**Files:**

- Modify: `addon/content/preferences.xhtml`
- Modify: `addon/locale/en-US/preferences.ftl`
- Modify: `addon/locale/zh-CN/preferences.ftl`

- [ ] **Step 1: Add backend installer guidance text**

In `addon/content/preferences.xhtml`, under the local backend group title, add:

```xml
<p data-l10n-id="pref-local-backend-description" />
```

Before the project directory field, add:

```xml
<p data-l10n-id="pref-local-backend-advanced-description" />
```

- [ ] **Step 2: Add English locale text**

In `addon/locale/en-US/preferences.ftl`, add:

```ftl
pref-local-backend-description = Run the local backend installer first, then click Start / Test. The plugin will detect the standard install path automatically.
pref-local-backend-advanced-description = Advanced fallback settings. Fill these only if you installed the backend in a custom location or automatic detection failed.
```

- [ ] **Step 3: Add Chinese locale text**

In `addon/locale/zh-CN/preferences.ftl`, add:

```ftl
pref-local-backend-description = 请先运行本地后端安装脚本，然后点击“启动/测试”。插件会自动识别标准安装路径。
pref-local-backend-advanced-description = 高级兜底设置。只有在你把后端安装到自定义位置，或自动识别失败时才需要填写。
```

- [ ] **Step 4: Build verification**

Run:

```bash
pnpm build
```

Expected: build completes.

- [ ] **Step 5: Commit**

```bash
git add addon/content/preferences.xhtml addon/locale/en-US/preferences.ftl addon/locale/zh-CN/preferences.ftl
git commit -m "docs: clarify backend path settings"
```

## Task 6: Remove In-Plugin Feedback Action

**Files:**

- Modify: `addon/content/taskManager.xhtml`
- Modify: `src/modules/translate/task-manager.ts`
- Modify: `addon/locale/en-US/taskManager.ftl`
- Modify: `addon/locale/zh-CN/taskManager.ftl`
- Modify: `README.md`

- [ ] **Step 1: Remove task manager feedback button**

In `addon/content/taskManager.xhtml`, delete:

```xml
<button id="feedback" data-l10n-id="feedback"></button>
```

- [ ] **Step 2: Remove feedback click handler**

In `src/modules/translate/task-manager.ts`, delete the block:

```ts
feedbackButton.addEventListener("click", (ev) => {
  ...
});
```

Also delete any now-unused `feedbackButton`, `APP_SITE_URL`, and
`TEST_APP_SITE_URL` references.

- [ ] **Step 3: Remove feedback locale strings**

In both task manager locale files, delete:

```ftl
feedback =
    .label = ...
    .tooltiptext = ...
```

Change copy task ID text to neutral diagnostics text:

```ftl
copy-pdf-id =
    .label = Copy Task ID
    .tooltiptext = Copy the backend task ID
```

Chinese:

```ftl
copy-pdf-id =
    .label = 复制任务 ID
    .tooltiptext = 复制本地后端任务 ID
```

- [ ] **Step 4: Build verification**

Run:

```bash
pnpm build
```

Expected: no missing identifier errors in `task-manager.ts`.

- [ ] **Step 5: Commit**

```bash
git add addon/content/taskManager.xhtml src/modules/translate/task-manager.ts addon/locale/en-US/taskManager.ftl addon/locale/zh-CN/taskManager.ftl README.md
git commit -m "feat: remove task feedback action"
```

## Task 7: Documentation Rewrite

**Files:**

- Modify: `README.md`
- Modify: `local_babeldoc_server/README.md`

- [ ] **Step 1: Rewrite user install instructions**

In `README.md`, include this installation flow:

````md
## 安装

1. 打开 Releases 页面：
   https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate/releases
2. 下载最新版本的 `.xpi`。
3. 在 Zotero 7 中打开 `Tools -> Add-ons`，从文件安装 `.xpi`。
4. 运行本地后端安装脚本。

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/MARS-ROBOTICS-star/Local-Immersive-Translate/main/scripts/install-local-backend.sh | bash
```
````

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/MARS-ROBOTICS-star/Local-Immersive-Translate/main/scripts/install-local-backend.ps1 | iex
```

5. 打开 Zotero 插件设置，填写模型 API 配置，点击 `启动/测试`。

````

- [ ] **Step 2: Remove original service and feedback references**

Delete README text that points to:

```text
original cloud app URL
original account/profile URL
original community URL
original upstream repository URL
````

- [ ] **Step 3: Update backend README**

In `local_babeldoc_server/README.md`, replace developer-machine path examples
with:

```md
Default installer paths:

- Windows: `%USERPROFILE%\Local-Immersive-Translate`
- macOS/Linux: `$HOME/Local-Immersive-Translate`

The Zotero plugin normally detects these paths automatically.
```

- [ ] **Step 4: Grep verification**

Run:

```bash
rg -n "<original upstream URL>|<original service domain>|<legacy developer path>" README.md local_babeldoc_server addon src package.json
```

Expected: no matches except internal names that are deliberately retained, such as `addonRef`, `prefsPrefix`, icon file names, or the new repository name.

- [ ] **Step 5: Commit**

```bash
git add README.md local_babeldoc_server/README.md
git commit -m "docs: document cross-platform local install"
```

## Task 8: Final Verification

**Files:**

- No new files

- [ ] **Step 1: Build**

Run:

```bash
pnpm build
```

Expected: build succeeds.

- [ ] **Step 2: Lint**

Run:

```bash
pnpm lint:check
```

Expected: prettier and eslint pass. If they fail only because of files modified in this plan, run `pnpm lint:fix`, inspect the diff, and rerun `pnpm lint:check`.

- [ ] **Step 3: Installer syntax checks**

Run:

```bash
bash -n scripts/install-local-backend.sh
```

Expected: no output.

If `pwsh` is available, run:

```bash
pwsh -NoProfile -Command '$null = [scriptblock]::Create((Get-Content -Raw scripts/install-local-backend.ps1)); "ok"'
```

Expected: `ok`.

- [ ] **Step 4: Final grep**

Run:

```bash
rg -n "<original upstream URL>|<original cloud app URL>|<original account URL>|<legacy developer path>" .
```

Expected: no matches in plugin defaults, docs, or user-facing links.

- [ ] **Step 5: Commit any verification-only formatting changes**

If lint changed formatting:

```bash
git add .
git commit -m "style: format release deployment changes"
```

If there were no formatting changes, skip this commit.
