param(
    [string]$TaskName = "GPlayKeywordRadarDaily",
    [string]$RunAt = "09:00"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectDir "scripts\run_daily.ps1"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $projectDir

$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Daily Google Play Keyword Opportunity Radar scan (US English)" `
    -Force

Write-Output "Registered Windows Scheduled Task '$TaskName' to run daily at $RunAt."
Write-Output "Tip: Make sure your proxy (e.g. 127.0.0.1:7897) is running or configured in config.yaml."
