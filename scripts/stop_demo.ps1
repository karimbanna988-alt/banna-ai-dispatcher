<#
.SYNOPSIS
  Stops the sanitized demo CRM (port 8517) started by start_demo.ps1.
  Does not touch the real CRM (port 8512), the WhatsApp bridge, or the
  real database. Does not delete the demo database file, so re-running
  start_demo.ps1 later just re-seeds it fresh.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1
#>

$ErrorActionPreference = "Continue"

$demoPort = 8517
$conn = Get-NetTCPConnection -LocalPort $demoPort -State Listen -ErrorAction SilentlyContinue
if (-not $conn) {
  Write-Host "Nothing is listening on port $demoPort. Nothing to stop." -ForegroundColor Yellow
  exit 0
}

$processIds = $conn.OwningProcess | Select-Object -Unique
foreach ($processId in $processIds) {
  $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($proc -and $proc.ProcessName -match "python") {
    Write-Host "Stopping demo CRM process $processId ($($proc.ProcessName))..." -ForegroundColor Cyan
    Stop-Process -Id $processId -Force
    Write-Host "Stopped. The real CRM, real database, and WhatsApp bridge were not touched." -ForegroundColor Green
  } else {
    Write-Host "Port $demoPort is held by process $processId, which is not a python.exe process -- not stopping it automatically. Investigate manually." -ForegroundColor Red
  }
}
