param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$Weekly
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

$logDir = Join-Path $ProjectDir "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$logFile = Join-Path $logDir "radar_$stamp.log"

$env:PYTHONPATH = Join-Path $ProjectDir "src"

if ($Weekly) {
    python -m gplay_radar.cli run weekly *>> $logFile
} else {
    python -m gplay_radar.cli run daily *>> $logFile
}

Write-Output "Done. Log written to $logFile"
