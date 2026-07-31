<#
.SYNOPSIS
  Read-only health check for the connected CRM (operator_api.py) on
  port 8512. Never logs in, never sends credentials.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\crm_health_check.ps1
#>

$ErrorActionPreference = "Continue"

$conn = Get-NetTCPConnection -LocalPort 8512 -State Listen -ErrorAction SilentlyContinue
if (-not $conn) {
  Write-Host "[DOWN] Nothing is listening on port 8512. Run scripts\start_crm.ps1." -ForegroundColor Yellow
  exit 1
}

try {
  $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8512/api/me" -TimeoutSec 3
  # A 200 here (Windows PowerShell 5.1 does not throw on 2xx) means an
  # active session cookie is present in this shell -- not the normal case.
  Write-Host "[ OK ] CRM is up and responding (an active session cookie is present in this shell)." -ForegroundColor Green
} catch [System.Net.WebException] {
  $statusCode = $null
  if ($_.Exception.Response) { $statusCode = [int]$_.Exception.Response.StatusCode }
  if ($statusCode -eq 401) {
    Write-Host "[ OK ] CRM is up and responding (not logged in, as expected without a session)." -ForegroundColor Green
  } else {
    Write-Host "[WARN] CRM responded with unexpected status $statusCode." -ForegroundColor Yellow
  }
} catch {
  Write-Host "[FAIL] Port 8512 is listening but did not respond correctly: $($_.Exception.Message)" -ForegroundColor Red
}
