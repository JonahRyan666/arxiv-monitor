param(
    [string]$TaskName = "ArxivMonitorLocalDaily",
    [string]$RepoDir = $PSScriptRoot,
    [string]$At = "09:00"
)

$ErrorActionPreference = "Stop"

$runner = Join-Path $RepoDir "run_local_daily.ps1"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Missing runner: $runner"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -RepoDir `"$RepoDir`""

$trigger = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Daily local literature monitor with JPSJ support" `
    -Force

Write-Host "Installed scheduled task '$TaskName' at $At."

