param(
  [string]$RepoUrl = "https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate.git",
  [string]$InstallDir = (Join-Path $env:USERPROFILE "Local-Immersive-Translate"),
  [string]$BabelDocUrl = "https://github.com/funstory-ai/BabelDOC.git",
  [string]$RepoRef = "",
  [string]$BabelDocRef = "",
  [switch]$AssumeYes,
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

function Clone-Or-Update {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,
    [Parameter(Mandatory = $true)]
    [string]$TargetDir,
    [string]$RepositoryRef = ""
  )

  $gitDir = Join-Path $TargetDir ".git"

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
    Fail "$TargetDir already exists but is not a git repository."
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
  Require-Command "git"

  Clone-Or-Update -RepositoryUrl $RepoUrl -TargetDir $InstallDir -RepositoryRef $RepoRef

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
  Clone-Or-Update -RepositoryUrl $BabelDocUrl -TargetDir $babelDocDir -RepositoryRef $BabelDocRef
  Invoke-Checked -FilePath "uv" -Arguments @("--directory", $babelDocDir, "sync")

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
