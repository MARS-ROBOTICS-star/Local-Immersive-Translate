param(
  [string]$RepoUrl = "https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate.git",
  [string]$InstallDir = (Join-Path $env:USERPROFILE "Local-Immersive-Translate"),
  [string]$BabelDocUrl = "https://github.com/funstory-ai/BabelDOC.git",
  [string]$RepoRef = "",
  [string]$BabelDocRef = "v0.6.4",
  [switch]$AssumeYes,
  [switch]$SkipProjectUpdate,
  [string]$UvInstallUrl = "https://astral.sh/uv/install.ps1"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$uvInstallScript = ""

function Fail {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Message
  )

  Write-Error -Message "Error: $Message" -ErrorAction Continue
  exit 1
}

function Require-Command {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
    Fail "'$Name' is required but was not found in PATH."
  }
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string[]]$Arguments = @()
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    $commandLine = (@($FilePath) + $Arguments) -join " "
    Fail "Command failed with exit code $LASTEXITCODE`: $commandLine"
  }
}

function Install-AgentSkill {
  param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
  )

  $skillSrc = Join-Path $InstallDir ".opencode\skills\zotero-translate-triage\SKILL.md"
  $skillRoot = if ($env:OPENCODE_SKILLS_DIR) { $env:OPENCODE_SKILLS_DIR } else { Join-Path $env:USERPROFILE ".config\opencode\skills" }
  $skillDir = Join-Path $skillRoot "zotero-translate-triage"
  if (Test-Path -LiteralPath $skillSrc) {
    New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
    Copy-Item -LiteralPath $skillSrc -Destination (Join-Path $skillDir "SKILL.md") -Force
    Write-Host "Agent debugging skill installed to: $skillDir\SKILL.md"
    Write-Host "Restart opencode (if running) for the skill to take effect."
  }
  else {
    Write-Host "Skill file not found in $InstallDir; skipping agent skill installation."
  }
}

function Invoke-CaptureChecked {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string[]]$Arguments = @()
  )

  $output = & $FilePath @Arguments 2>$null
  if ($LASTEXITCODE -ne 0) {
    $commandLine = (@($FilePath) + $Arguments) -join " "
    Fail "Command failed with exit code $LASTEXITCODE`: $commandLine"
  }

  return (($output -join "`n").Trim())
}

function Get-PowerShellPath {
  $currentProcess = Get-Process -Id $PID
  if (-not [string]::IsNullOrWhiteSpace($currentProcess.Path)) {
    return $currentProcess.Path
  }

  $pwshCommand = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($null -ne $pwshCommand) {
    return $pwshCommand.Source
  }

  $powershellCommand = Get-Command powershell -ErrorAction SilentlyContinue
  if ($null -ne $powershellCommand) {
    return $powershellCommand.Source
  }

  Fail "Could not find a PowerShell executable to run the uv installer."
}

function Get-OriginDefaultBranch {
  param(
    [Parameter(Mandatory = $true)]
    [string]$TargetDir
  )

  $originHead = & git -C $TargetDir symbolic-ref refs/remotes/origin/HEAD 2>$null
  if ($LASTEXITCODE -eq 0) {
    $originHead = (($originHead -join "`n").Trim())
    $prefix = "refs/remotes/origin/"
    if ($originHead.StartsWith($prefix)) {
      $branchName = $originHead.Substring($prefix.Length)
      if (-not [string]::IsNullOrWhiteSpace($branchName)) {
        return $branchName
      }
    }
  }

  return "main"
}

function Get-GitHubArchiveUrl {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,
    [string]$RepositoryRef = "main"
  )

  $normalizedUrl = $RepositoryUrl -replace '\.git$', ''
  if ($normalizedUrl.StartsWith("git@github.com:")) {
    $normalizedUrl = "https://github.com/" + $normalizedUrl.Substring("git@github.com:".Length)
  }

  return "$normalizedUrl/archive/refs/heads/$RepositoryRef.zip"
}

function Install-FromArchive {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,
    [Parameter(Mandatory = $true)]
    [string]$TargetDir,
    [string]$RepositoryRef = "main",
    [string]$MarkerFile = ""
  )

  if (Test-Path -LiteralPath $TargetDir -PathType Container) {
    if (-not [string]::IsNullOrWhiteSpace($MarkerFile) -and (Test-Path -LiteralPath (Join-Path $TargetDir $MarkerFile))) {
      Write-Host "$TargetDir already exists and is not a Git repository; using it as-is."
      return
    }
    Fail "$TargetDir already exists but is not a recognized installation directory."
  }
  elseif (Test-Path -LiteralPath $TargetDir) {
    Fail "$TargetDir already exists but is not a directory."
  }

  $archiveUrl = Get-GitHubArchiveUrl -RepositoryUrl $RepositoryUrl -RepositoryRef $RepositoryRef
  $archiveFile = Join-Path ([System.IO.Path]::GetTempPath()) ("local-immersive-translate-{0}.zip" -f [System.Guid]::NewGuid())
  $extractDir = Join-Path ([System.IO.Path]::GetTempPath()) ("local-immersive-translate-{0}" -f [System.Guid]::NewGuid())

  Write-Host "git was not found; downloading archive from: $archiveUrl"
  Invoke-WebRequest -Uri $archiveUrl -OutFile $archiveFile
  New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
  Expand-Archive -LiteralPath $archiveFile -DestinationPath $extractDir -Force
  $extractedRoot = Get-ChildItem -LiteralPath $extractDir -Directory | Select-Object -First 1
  if ($null -eq $extractedRoot) {
    Fail "Downloaded archive did not contain a project directory."
  }

  New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
  Copy-Item -Path (Join-Path $extractedRoot.FullName "*") -Destination $TargetDir -Recurse -Force
  Remove-Item -LiteralPath $archiveFile -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
}

function Clone-Or-Update {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,
    [Parameter(Mandatory = $true)]
    [string]$TargetDir,
    [string]$RepositoryRef = "",
    [string]$MarkerFile = ""
  )

  $gitDir = Join-Path $TargetDir ".git"

  if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
    $archiveRef = if ([string]::IsNullOrWhiteSpace($RepositoryRef)) { "main" } else { $RepositoryRef }
    Install-FromArchive -RepositoryUrl $RepositoryUrl -TargetDir $TargetDir -RepositoryRef $archiveRef -MarkerFile $MarkerFile
    return
  }

  if (Test-Path -LiteralPath $gitDir) {
    $originUrl = Invoke-CaptureChecked -FilePath "git" -Arguments @("-C", $TargetDir, "remote", "get-url", "origin")
    if ([string]::IsNullOrWhiteSpace($originUrl)) {
      Fail "$TargetDir already contains a Git repository, but its origin remote could not be read."
    }
    if ($originUrl -ne $RepositoryUrl) {
      Fail "$TargetDir already contains a different Git repository. Expected origin: $RepositoryUrl; actual origin: $originUrl"
    }

    Invoke-Checked -FilePath "git" -Arguments @("-C", $TargetDir, "fetch", "--tags", "origin")

    if (-not [string]::IsNullOrWhiteSpace($RepositoryRef)) {
      Invoke-Checked -FilePath "git" -Arguments @("-C", $TargetDir, "checkout", $RepositoryRef)
      return
    }

    $defaultBranch = Get-OriginDefaultBranch -TargetDir $TargetDir
    $currentBranch = & git -C $TargetDir symbolic-ref --quiet --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0) {
      $currentBranch = (($currentBranch -join "`n").Trim())
    }
    else {
      $currentBranch = ""
    }

    if ($currentBranch -ne $defaultBranch) {
      Invoke-Checked -FilePath "git" -Arguments @("-C", $TargetDir, "checkout", $defaultBranch)
    }

    Invoke-Checked -FilePath "git" -Arguments @("-C", $TargetDir, "pull", "--ff-only")
  }
  elseif (Test-Path -LiteralPath $TargetDir) {
    if (-not [string]::IsNullOrWhiteSpace($MarkerFile) -and (Test-Path -LiteralPath (Join-Path $TargetDir $MarkerFile))) {
      Write-Host "$TargetDir already exists and is not a Git repository; using it as-is."
      return
    }
    Fail "$TargetDir already exists but is not a recognized installation directory."
  }
  else {
    Invoke-Checked -FilePath "git" -Arguments @("clone", $RepositoryUrl, $TargetDir)

    if (-not [string]::IsNullOrWhiteSpace($RepositoryRef)) {
      Invoke-Checked -FilePath "git" -Arguments @("-C", $TargetDir, "fetch", "--tags", "origin")
      Invoke-Checked -FilePath "git" -Arguments @("-C", $TargetDir, "checkout", $RepositoryRef)
    }
  }
}

try {
  if (-not $SkipProjectUpdate -and $env:SKIP_PROJECT_UPDATE -notmatch '^(?i:1|true)$') {
    Clone-Or-Update -RepositoryUrl $RepoUrl -TargetDir $InstallDir -RepositoryRef $RepoRef -MarkerFile "package.json"
  }

  if ($null -eq (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (-not $AssumeYes) {
      Write-Host "uv is not installed."
      Write-Host "This installer will download and execute the official uv installer from:"
      Write-Host $UvInstallUrl
      $confirmation = Read-Host "Continue? [y/N]"
      if ($confirmation -notmatch '^(?i:y|yes)$') {
        Fail "Aborted. Install uv manually from https://docs.astral.sh/uv/ or rerun with -AssumeYes."
      }
    }

    $uvInstallScript = Join-Path ([System.IO.Path]::GetTempPath()) ("uv-install-{0}.ps1" -f [System.Guid]::NewGuid())
    Write-Host "Downloading uv installer from: $UvInstallUrl"
    Invoke-WebRequest -Uri $UvInstallUrl -OutFile $uvInstallScript

    Write-Host "Executing uv installer from temporary file: $uvInstallScript"
    $powerShellPath = Get-PowerShellPath
    Invoke-Checked -FilePath $powerShellPath -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $uvInstallScript)

    $uvBinDir = Join-Path $env:USERPROFILE ".local\bin"
    $env:PATH = "$uvBinDir$([System.IO.Path]::PathSeparator)$env:PATH"
  }

  $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
  if ($null -eq $uvCommand) {
    Fail "uv installation failed or uv is not available in PATH."
  }

  $babelDocDir = Join-Path $InstallDir "BabelDOC"
  Clone-Or-Update -RepositoryUrl $BabelDocUrl -TargetDir $babelDocDir -RepositoryRef $BabelDocRef -MarkerFile "pyproject.toml"
  Invoke-Checked -FilePath "uv" -Arguments @("--directory", $babelDocDir, "sync")
  $backendRequirements = Join-Path $InstallDir "local_babeldoc_server\requirements.txt"
  $babelDocPython = Invoke-CaptureChecked -FilePath "uv" -Arguments @("--directory", $babelDocDir, "run", "python", "-c", "import sys; print(sys.executable)")
  Invoke-Checked -FilePath "uv" -Arguments @("pip", "install", "--python", $babelDocPython, "-r", $backendRequirements)
  Invoke-Checked -FilePath $babelDocPython -Arguments @("-c", "from google import genai; assert genai is not None")
  Invoke-Checked -FilePath $babelDocPython -Arguments @("-c", "from rapidocr import RapidOCR; RapidOCR()")

  Install-AgentSkill -InstallDir $InstallDir

  Write-Host ""
  Write-Host "Project directory: $InstallDir"
  Write-Host "uv path: $($uvCommand.Source)"
  Write-Host "Open Zotero preferences, then click Start / Test for the local backend."
}
finally {
  if (-not [string]::IsNullOrWhiteSpace($uvInstallScript) -and (Test-Path -LiteralPath $uvInstallScript)) {
    Remove-Item -LiteralPath $uvInstallScript -Force -ErrorAction SilentlyContinue
  }
}
