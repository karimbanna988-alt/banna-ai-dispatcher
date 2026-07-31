<#
.SYNOPSIS
  Starts the canonical connected CRM (operator_api.py) locally. Does not
  start any tunnel and does not touch the WhatsApp bridge.

.DESCRIPTION
  NOTE: tools\windows_launcher\ (Start Dispatcher.bat etc.) currently
  starts the older luxor_desk.py Streamlit interface, not this one --
  that's a separate, already-known gap outside this script's scope.
  This script is the correct way to start the canonical operator_api.py
  CRM used by operator_ui_v2_connected/.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\start_crm.ps1
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$existing = Get-NetTCPConnection -LocalPort 8512 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
  Write-Host "Something is already listening on port 8512 -- the CRM may already be running." -ForegroundColor Yellow
  Write-Host "Run scripts\crm_health_check.ps1 to confirm before starting a second copy." -ForegroundColor Yellow
  exit 0
}

Write-Host "Starting the connected CRM (operator_api.py) on http://127.0.0.1:8512 ..." -ForegroundColor Cyan
Write-Host "Leave this window open. Press Ctrl+C to stop it, or use scripts\stop_crm.ps1 from another window." -ForegroundColor Cyan
python operator_api.py
