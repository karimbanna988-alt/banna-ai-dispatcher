<#
.SYNOPSIS
  Safely stops the WhatsApp Shadow Bridge Node process without touching
  the paired session (you can restart normally afterward with
  scripts\start_normal.ps1).

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
#>

$ErrorActionPreference = "Continue"

$conn = Get-NetTCPConnection -LocalPort 8514 -State Listen -ErrorAction SilentlyContinue
if (-not $conn) {
  Write-Host "Nothing appears to be running on port 8514 (Node bridge admin server). Nothing to stop." -ForegroundColor Yellow
  exit 0
}

$processIds = $conn.OwningProcess | Select-Object -Unique
foreach ($processId in $processIds) {
  $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($proc -and $proc.ProcessName -match "node") {
    Write-Host "Stopping Node process $processId ($($proc.ProcessName))..." -ForegroundColor Cyan
    Stop-Process -Id $processId -Force
    Write-Host "Stopped. The paired session under .runtime/whatsapp-shadow-session/ was not touched." -ForegroundColor Green
  } else {
    Write-Host "Port 8514 is held by process $processId, which is not a node.exe process -- not stopping it automatically. Investigate manually." -ForegroundColor Red
  }
}
