<#
.SYNOPSIS
  Starts the WhatsApp Shadow Bridge in normal receive-only mode, using
  an already-paired session. Does NOT open pairing/QR mode.

.DESCRIPTION
  Only meaningful after scripts\start_pairing.ps1 has been used once and
  pairing succeeded (a session exists under .runtime/whatsapp-shadow-session/).
  This reconnects to that existing session -- it does not create a new
  one and does not print a QR code.

.USAGE
  cd whatsapp_shadow_bridge
  powershell -ExecutionPolicy Bypass -File scripts\start_normal.ps1
#>

$ErrorActionPreference = "Stop"
$bridgeDir = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $bridgeDir
Set-Location $bridgeDir

$sessionDir = Join-Path $repoRoot ".runtime\whatsapp-shadow-session"
if (-not (Test-Path $sessionDir)) {
  Write-Host "No paired session found at $sessionDir" -ForegroundColor Yellow
  Write-Host "Run scripts\start_pairing.ps1 first (while physically present to scan the QR)." -ForegroundColor Yellow
  exit 1
}

Write-Host "Starting WhatsApp Shadow Bridge in normal receive-only mode (reusing existing paired session)." -ForegroundColor Cyan
$env:WHATSAPP_SHADOW_ENABLED = "true"
node src/index.js --pair
