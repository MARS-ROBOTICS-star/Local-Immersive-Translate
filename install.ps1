param(
  [switch]$AssumeYes
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($env:INSTALL_DIR)) {
  $env:INSTALL_DIR = $scriptDir
}
$env:SKIP_PROJECT_UPDATE = "1"

$installer = Join-Path $scriptDir "scripts\install-local-backend.ps1"
if ($AssumeYes) {
  & $installer -InstallDir $env:INSTALL_DIR -AssumeYes
}
else {
  & $installer -InstallDir $env:INSTALL_DIR
}
