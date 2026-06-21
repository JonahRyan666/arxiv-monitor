param(
    [string]$RepoDir = $PSScriptRoot
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $RepoDir

$envFile = Join-Path $RepoDir ".env.local"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing .env.local. Copy .env.local.example to .env.local and fill FEISHU_WEBHOOK_URL and SILICONFLOW_API_KEY."
}

Get-Content -LiteralPath $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    $parts = $line.Split("=", 2)
    if ($parts.Count -ne 2) {
        return
    }
    [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
}

$env:PYTHONUNBUFFERED = "1"
python arxiv_daily_report.py

