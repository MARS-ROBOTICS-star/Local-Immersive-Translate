import { getString } from "../utils/locale";
import { getPref, setPref } from "../utils/prefs";

const DEFAULT_LOCAL_BACKEND_URL = "http://127.0.0.1:8765/zotero";
const LOCAL_BACKEND_DIR_NAME = "Local-Immersive-Translate";
const INSTALL_SCRIPT_BASE_URL =
  "https://raw.githubusercontent.com/MARS-ROBOTICS-star/Local-Immersive-Translate/main/scripts";

type EnsureLocalBackendResult = {
  ok: boolean;
  started: boolean;
  message: string;
};

type InstallLocalBackendResult = {
  ok: boolean;
  message: string;
};

function isUnsetValue(value: unknown) {
  if (typeof value !== "string") {
    return value === undefined || value === null;
  }
  const normalized = value.trim().toLowerCase();
  return !normalized || normalized === "undefined" || normalized === "null";
}

function normalizeBaseUrl(url: string) {
  return (url || DEFAULT_LOCAL_BACKEND_URL).replace(/\/+$/, "");
}

function getPathSeparator() {
  return Zotero.isWin ? "\\" : "/";
}

function joinPath(...parts: string[]) {
  const filteredParts = parts.filter(Boolean);
  if (!filteredParts.length) {
    return "";
  }
  if (
    typeof PathUtils !== "undefined" &&
    typeof PathUtils.join === "function"
  ) {
    return PathUtils.join(...filteredParts);
  }

  const separator = getPathSeparator();
  const edgeSlashes = /[\\/]+$/;
  const surroundingSlashes = /^[\\/]+|[\\/]+$/g;
  const firstPart = filteredParts[0];
  const normalizedFirst =
    !Zotero.isWin && /^\/+$/.test(firstPart)
      ? separator
      : firstPart.replace(edgeSlashes, "");
  const normalizedRest = filteredParts
    .slice(1)
    .map((part) => part.replace(surroundingSlashes, ""))
    .filter(Boolean);
  if (!normalizedRest.length) {
    return normalizedFirst;
  }
  if (normalizedFirst === separator) {
    return `${separator}${normalizedRest.join(separator)}`;
  }
  return [normalizedFirst, ...normalizedRest].filter(Boolean).join(separator);
}

function pathToFile(path: string): nsIFile {
  return Zotero.File.pathToFile(path);
}

function getEnv(name: string) {
  try {
    if (
      typeof Services === "undefined" ||
      !Services.env ||
      !Services.env.exists(name)
    ) {
      return "";
    }
    return Services.env.get(name) || "";
  } catch (error) {
    ztoolkit.log(`Failed to read environment variable ${name}:`, error);
    return "";
  }
}

function getHomeDir() {
  return (
    Zotero.isWin ? getEnv("USERPROFILE") || getEnv("HOME") : getEnv("HOME")
  ).trim();
}

function getPathEntries() {
  return getEnv("PATH")
    .split(Zotero.isWin ? ";" : ":")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function normalizeStringPref(value: unknown) {
  return isUnsetValue(value) || typeof value !== "string" ? "" : value.trim();
}

function shouldAutoDetectProject(value: unknown) {
  const projectDir = normalizeStringPref(value);
  return !projectDir;
}

function shouldAutoDetectUv(value: unknown) {
  const uvPath = normalizeStringPref(value);
  return !uvPath;
}

function getUvExecutableName() {
  return Zotero.isWin ? "uv.exe" : "uv";
}

function isReadablePath(path: string) {
  if (!path) {
    return false;
  }
  try {
    const file = pathToFile(path);
    return file.exists() && file.isReadable();
  } catch {
    return false;
  }
}

function isReadableDirectory(path: string) {
  if (!isReadablePath(path)) {
    return false;
  }
  try {
    return pathToFile(path).isDirectory();
  } catch {
    return false;
  }
}

function isReadableExecutable(path: string) {
  if (!isReadablePath(path)) {
    return false;
  }
  try {
    const file = pathToFile(path);
    return !file.isDirectory() && file.isExecutable();
  } catch {
    return false;
  }
}

function hasReadableBabelDocDir(projectDir: string) {
  return isReadableDirectory(joinPath(projectDir, "BabelDOC"));
}

function findProjectDir(existing: unknown) {
  const existingProjectDir = normalizeStringPref(existing);
  if (existingProjectDir && hasReadableBabelDocDir(existingProjectDir)) {
    return existingProjectDir;
  }

  const homeDir = getHomeDir();
  if (homeDir) {
    const detectedProjectDir = joinPath(homeDir, LOCAL_BACKEND_DIR_NAME);
    if (hasReadableBabelDocDir(detectedProjectDir)) {
      return detectedProjectDir;
    }
  }

  return existingProjectDir || "";
}

function findUvPath(existing: unknown) {
  const existingUvPath = normalizeStringPref(existing);
  if (existingUvPath && isReadableExecutable(existingUvPath)) {
    return existingUvPath;
  }

  const executableName = getUvExecutableName();
  for (const pathEntry of getPathEntries()) {
    const detectedUvPath = joinPath(pathEntry, executableName);
    if (isReadableExecutable(detectedUvPath)) {
      return detectedUvPath;
    }
  }

  const homeDir = getHomeDir();
  if (homeDir) {
    const detectedUvPath = joinPath(homeDir, ".local", "bin", executableName);
    if (isReadableExecutable(detectedUvPath)) {
      return detectedUvPath;
    }
  }

  return existingUvPath || "";
}

function findExecutableInPath(names: string[]) {
  for (const name of names) {
    for (const pathEntry of getPathEntries()) {
      const executablePath = joinPath(pathEntry, name);
      if (isReadableExecutable(executablePath)) {
        return executablePath;
      }
    }
  }
  return "";
}

function findFirstExecutable(candidates: string[]) {
  for (const candidate of candidates) {
    if (isReadableExecutable(candidate)) {
      return candidate;
    }
  }
  return "";
}

function getShellCommand() {
  if (Zotero.isWin) {
    const systemRoot =
      getEnv("SystemRoot") || getEnv("WINDIR") || "C:\\Windows";
    const powerShellPath =
      findExecutableInPath(["pwsh.exe", "powershell.exe"]) ||
      findFirstExecutable([
        joinPath(
          systemRoot,
          "System32",
          "WindowsPowerShell",
          "v1.0",
          "powershell.exe",
        ),
      ]);
    if (!powerShellPath) {
      throw new Error("PowerShell was not found in PATH.");
    }

    const scriptUrl = `${INSTALL_SCRIPT_BASE_URL}/install-local-backend.ps1`;
    const command = [
      "$ErrorActionPreference = 'Stop'",
      "$ProgressPreference = 'SilentlyContinue'",
      "$script = Join-Path ([System.IO.Path]::GetTempPath()) ('local-immersive-translate-install-' + [System.Guid]::NewGuid() + '.ps1')",
      `Invoke-WebRequest -UseBasicParsing -Uri '${scriptUrl}' -OutFile $script`,
      "try { & $script -AssumeYes } finally { Remove-Item -LiteralPath $script -Force -ErrorAction SilentlyContinue }",
    ].join("; ");

    return {
      executablePath: powerShellPath,
      args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
    };
  }

  const shellPath =
    findExecutableInPath(["bash"]) ||
    findFirstExecutable(["/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash"]);
  if (!shellPath) {
    throw new Error("bash was not found in PATH.");
  }

  const scriptUrl = `${INSTALL_SCRIPT_BASE_URL}/install-local-backend.sh`;
  const command = [
    'install_script="$(mktemp "${TMPDIR:-/tmp}/local-immersive-translate-install.XXXXXX.sh")"',
    `curl -fsSL '${scriptUrl}' -o "$install_script"`,
    'ASSUME_YES=1 bash "$install_script"',
    'rm -f "$install_script"',
  ].join(" && ");

  return {
    executablePath: shellPath,
    args: ["-lc", command],
  };
}

function runProcessToCompletion(
  executablePath: string,
  args: string[],
): Promise<void> {
  return new Promise((resolve, reject) => {
    const process = (Components.classes as any)[
      "@mozilla.org/process/util;1"
    ].createInstance(Components.interfaces.nsIProcess) as nsIProcess;
    const executableFile = assertExecutablePath(
      executablePath,
      "Installer shell",
    );
    process.init(executableFile);
    process.startHidden = true;
    process.noShell = true;
    addon.data.localBackendInstallProcess = process;

    const observer = {
      observe(_subject: any, topic: string) {
        addon.data.localBackendInstallProcess = undefined;
        if (topic === "process-finished" && process.exitValue === 0) {
          resolve();
          return;
        }
        reject(
          new Error(
            topic === "process-failed"
              ? "installer process failed to start"
              : `installer exited with code ${process.exitValue}`,
          ),
        );
      },
    };

    try {
      (process as any).runAsync(args, args.length, observer, false);
    } catch (error) {
      addon.data.localBackendInstallProcess = undefined;
      reject(error);
    }
  });
}

function assertReadablePath(path: string, label: string) {
  const file = pathToFile(path);
  if (!file.exists()) {
    throw new Error(`${label} does not exist: ${path}`);
  }
  if (!file.isReadable()) {
    throw new Error(`${label} is not readable: ${path}`);
  }
  return file;
}

function assertExecutablePath(path: string, label: string) {
  const file = assertReadablePath(path, label);
  if (file.isDirectory()) {
    throw new Error(`${label} is not executable: ${path}`);
  }
  try {
    if (typeof file.isExecutable === "function" && !file.isExecutable()) {
      throw new Error(`${label} is not executable: ${path}`);
    }
  } catch (error: any) {
    if (error?.message === `${label} is not executable: ${path}`) {
      throw error;
    }
    ztoolkit.log(`Failed to check whether ${label} is executable:`, error);
  }
  return file;
}

async function delay(ms: number) {
  await Zotero.Promise.delay(ms);
}

export async function isLocalBackendHealthy(): Promise<boolean> {
  const healthUrl = `${normalizeBaseUrl(getPref("localBackendUrl"))}/healthz`;
  try {
    const response = await fetch(healthUrl, { method: "GET" });
    return response.ok;
  } catch (error) {
    ztoolkit.log("Local BabelDOC health check failed:", error);
    return false;
  }
}

export function normalizeLocalBackendPrefs() {
  const localBackendUrl = getPref("localBackendUrl");
  const currentProjectDir = getPref("localBackendProjectDir");
  const currentUvPath = getPref("localBackendUvPath");

  if (isUnsetValue(localBackendUrl)) {
    setPref("localBackendUrl", DEFAULT_LOCAL_BACKEND_URL);
  }
  if (shouldAutoDetectProject(currentProjectDir)) {
    const detectedProjectDir = findProjectDir(currentProjectDir);
    setPref("localBackendProjectDir", detectedProjectDir);
  }
  if (shouldAutoDetectUv(currentUvPath)) {
    const detectedUvPath = findUvPath(currentUvPath);
    setPref("localBackendUvPath", detectedUvPath);
  }
  if (typeof getPref("localBackendAutoStart") !== "boolean") {
    setPref("localBackendAutoStart", true);
  }
}

export async function installOrRepairLocalBackend(): Promise<InstallLocalBackendResult> {
  if (addon.data.localBackendInstallProcess?.isRunning) {
    return {
      ok: false,
      message: getString("pref-install-already-running"),
    };
  }

  try {
    const { executablePath, args } = getShellCommand();
    ztoolkit.log("Running Local BabelDOC installer:", executablePath, args);
    await runProcessToCompletion(executablePath, args);

    setPref("localBackendProjectDir", "");
    setPref("localBackendUvPath", "");
    normalizeLocalBackendPrefs();

    return {
      ok: true,
      message: getString("pref-install-success"),
    };
  } catch (error: any) {
    ztoolkit.log("Failed to install Local BabelDOC backend:", error);
    return {
      ok: false,
      message: getString("pref-install-failed", {
        args: { reason: error?.message || String(error) },
      }),
    };
  }
}

function startLocalBackendProcess() {
  normalizeLocalBackendPrefs();
  const projectDir = (getPref("localBackendProjectDir") || "").trim();
  const uvPath = (getPref("localBackendUvPath") || "").trim();
  if (!projectDir) {
    throw new Error("Project directory is empty");
  }
  if (!uvPath) {
    throw new Error("uv path is empty");
  }

  const babeldocDir = joinPath(projectDir, "BabelDOC");
  const serverPath = joinPath(projectDir, "local_babeldoc_server", "server.py");
  const configPath = joinPath(
    projectDir,
    "local_babeldoc_server",
    "config.example.json",
  );

  const uvFile = assertExecutablePath(uvPath, "uv");
  const babeldocDirFile = assertReadablePath(babeldocDir, "BabelDOC directory");
  if (!babeldocDirFile.isDirectory()) {
    throw new Error(`BabelDOC path is not a directory: ${babeldocDir}`);
  }
  assertReadablePath(serverPath, "Local BabelDOC server");
  assertReadablePath(configPath, "Local BabelDOC config");

  const args = [
    "--directory",
    babeldocDir,
    "run",
    "python",
    serverPath,
    "--config",
    configPath,
  ];

  const process = (Components.classes as any)[
    "@mozilla.org/process/util;1"
  ].createInstance(Components.interfaces.nsIProcess) as nsIProcess;
  process.init(uvFile);
  process.startHidden = true;
  process.noShell = true;
  process.runAsync(args, args.length);
  addon.data.localBackendProcess = process;
  ztoolkit.log("Started Local BabelDOC process:", uvPath, args);
}

export async function ensureLocalBackendRunning({
  forceStart = false,
}: {
  forceStart?: boolean;
} = {}): Promise<EnsureLocalBackendResult> {
  normalizeLocalBackendPrefs();

  if (await isLocalBackendHealthy()) {
    return {
      ok: true,
      started: false,
      message: getString("pref-test-running"),
    };
  }

  if (!forceStart && !getPref("localBackendAutoStart")) {
    return {
      ok: false,
      started: false,
      message: getString("pref-test-autostart-disabled"),
    };
  }

  try {
    if (!addon.data.localBackendProcess?.isRunning) {
      startLocalBackendProcess();
    }

    for (let i = 0; i < 240; i++) {
      await delay(500);
      if (await isLocalBackendHealthy()) {
        return {
          ok: true,
          started: true,
          message: getString("pref-test-started"),
        };
      }
      if (
        addon.data.localBackendProcess &&
        addon.data.localBackendProcess.isRunning === false
      ) {
        break;
      }
    }

    throw new Error("service did not become reachable within 120 seconds");
  } catch (error: any) {
    ztoolkit.log("Failed to start Local BabelDOC:", error);
    return {
      ok: false,
      started: false,
      message: getString("pref-test-autostart-failed", {
        args: { reason: error?.message || String(error) },
      }),
    };
  }
}
