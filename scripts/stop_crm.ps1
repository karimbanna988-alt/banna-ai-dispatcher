<#
.SYNOPSIS
  Safely stops the connected CRM (operator_api.py) if it's running.
  Does not touch the WhatsApp bridge or any tunnel.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\stop_crm.ps1
#>

$ErrorActionPreference = "Continue"

$conn = Get-NetTCPConnection -LocalPort 8512 -State Listen -ErrorAction SilentlyContinue
if (-not $conn) {
  Write-Host "Nothing is listening on port 8512. Nothing to stop." -ForegroundColor Yellow
  exit 0
}

$processIds = $conn.OwningProcess | Select-Object -Unique
foreach ($processId in $processIds) {
  $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($proc -and $proc.ProcessName -match "python") {
    Write-Host "Stopping CRM process $processId ($($proc.ProcessName))..." -ForegroundColor Cyan
    Stop-Process -Id $processId -Force
    Write-Host "Stopped. The real database was not touched by this script." -ForegroundColor Green
  } else {
    Write-Host "Port 8512 is held by process $processId, which is not a python.exe process -- not stopping it automatically. Investigate manually." -ForegroundColor Red
  }
}
