<#
.SYNOPSIS
  One-command startup for the boss demo: the connected CRM (operator_api.py,
  which starts its own read-only Python bridge adapter on 8513 as part of
  the same process) and the receive-only WhatsApp Shadow Bridge (8514),
  reusing the existing paired session. Then runs read-only health checks
  against all three.

.DESCRIPTION
  Does NOT pair a new session, does NOT print the QR code, does NOT
  enable sending -- there is no send capability anywhere in this
  codebase to enable (see whatsapp_shadow_bridge/test/no-send-static-check.test.js).
  If no paired session exists yet under .runtime\whatsapp-shadow-session\,
  this script stops and tells you to run start_pairing.ps1 first while
  physically present -- it will not attempt to pair on its own.

  Never prints WHATSAPP_SHADOW_ADAPTER_SECRET, WHATSAPP_BRIDGE_ADAPTER_SECRET,
  LUXOR_DEMO_PASSWORD, or any other secret value. Health check responses
  contain no customer data.

  Starts each process in its own new PowerShell window so you can watch
  its logs and Ctrl+C it independently, or stop everything at once with
  scripts\stop_crm.ps1 (CRM) and whatsapp_shadow_bridge\scripts\stop.ps1
  (bridge).

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\start_boss_demo.ps1
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "===================================================="  -ForegroundColor Cyan
Write-Host " LUXOR boss-demo launcher"                              -ForegroundColor Cyan
Write-Host "===================================================="  -ForegroundColor Cyan

# ------------------------------------------------------------- pre-flight -
$sessionDir = Join-Path $repoRoot ".runtime\whatsapp-shadow-session\session"
if (-not (Test-Path $sessionDir)) {
  Write-Host "[STOP] No paired WhatsApp session found at $sessionDir" -ForegroundColor Red
  Write-Host "       Run whatsapp_shadow_bridge\scripts\start_pairing.ps1 first, while physically" -ForegroundColor Red
  Write-Host "       present to scan the QR code. This launcher will not pair on its own." -ForegroundColor Red
  exit 1
}
Write-Host "[ OK ] Existing paired WhatsApp session found -- no QR will be requested." -ForegroundColor Green

# ------------------------------------------------------------------ CRM --
$crmRunning = Get-NetTCPConnection -LocalPort 8512 -State Listen -ErrorAction SilentlyContinue
if ($crmRunning) {
  Write-Host "[SKIP] Something is already listening on 8512 -- assuming the CRM is already running." -ForegroundColor Yellow
} else {
  Write-Host "Starting CRM (operator_api.py) on 8512, with its bridge adapter on 8513 ..." -ForegroundColor Cyan
  Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$repoRoot'; python operator_api.py"
}

# ---------------------------------------------------------- WhatsApp bridge
$bridgeRunning = Get-NetTCPConnection -LocalPort 8514 -State Listen -ErrorAction SilentlyContinue
if ($bridgeRunning) {
  Write-Host "[SKIP] Something is already listening on 8514 -- assuming the bridge is already running." -ForegroundColor Yellow
} else {
  Write-Host "Starting WhatsApp Shadow Bridge (receive-only, existing paired session) on 8514 ..." -ForegroundColor Cyan
  $bridgeDir = Join-Path $repoRoot "whatsapp_shadow_bridge"
  Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$bridgeDir'; powershell -ExecutionPolicy Bypass -File scripts\start_normal.ps1"
}

# --------------------------------------------------------- wait + verify --
Write-Host ""
Write-Host "Waiting a few seconds for both processes to come up ..." -ForegroundColor Cyan
Start-Sleep -Seconds 6

function Check-Endpoint($name, $port, $path) {
  $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if (-not $conn) {
    Write-Host "  [DOWN] $name (port $port) -- not listening yet" -ForegroundColor Yellow
    return $false
  }
  try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$port$path" -TimeoutSec 5
    Write-Host "  [ OK ] $name (port $port): $($resp | ConvertTo-Json -Compress)" -ForegroundColor Green
    return $true
  } catch {
    Write-Host "  [WARN] $name (port $port) is listening but did not respond to $path yet" -ForegroundColor Yellow
    return $false
  }
}

Write-Host ""
Write-Host "Health checks:" -ForegroundColor Cyan
$crmOk = Check-Endpoint "CRM (operator_api.py)"      8512 "/api/me"
$adapterOk = Check-Endpoint "Python bridge adapter"  8513 "/health"
$bridgeOk = Check-Endpoint "Node WhatsApp bridge"    8514 "/health"

Write-Host ""
if ($crmOk -or $adapterOk) {
  # /api/me returns 401 without a session, which Invoke-RestMethod treats
  # as a thrown error in Windows PowerShell 5.1 -- a non-crash here still
  # means "up", so don't treat $crmOk = $false alone as fatal.
  Write-Host "CRM/adapter reachable." -ForegroundColor Green
} else {
  Write-Host "CRM/adapter not confirmed yet -- check the CRM window for errors." -ForegroundColor Yellow
}
if ($bridgeOk) {
  Write-Host "WhatsApp bridge reachable. Open http://127.0.0.1:8512 and log in to see live status." -ForegroundColor Green
} else {
  Write-Host "WhatsApp bridge not confirmed yet -- check the bridge window; it may still be reconnecting to WhatsApp." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Demo URL: http://127.0.0.1:8512" -ForegroundColor Cyan
Write-Host "To stop everything: scripts\stop_crm.ps1  and  whatsapp_shadow_bridge\scripts\stop.ps1" -ForegroundColor Cyan
