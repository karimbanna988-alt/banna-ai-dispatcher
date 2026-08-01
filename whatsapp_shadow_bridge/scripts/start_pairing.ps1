<#
.SYNOPSIS
  Starts the WhatsApp Shadow Bridge in pairing mode. Prints a QR code
  directly in this terminal for you to scan yourself.

.DESCRIPTION
  Only run this while you are physically present to scan the QR code
  with the target technician's phone (WhatsApp > Linked Devices >
  Link a Device). This script does not scan anything automatically and
  cannot pair on its own -- it only starts the process and waits.

  Run scripts\preflight.ps1 first and resolve any [FAIL] lines.

.USAGE
  cd whatsapp_shadow_bridge
  powershell -ExecutionPolicy Bypass -File scripts\start_pairing.ps1
#>

$ErrorActionPreference = "Stop"
$bridgeDir = Split-Path -Parent $PSScriptRoot
Set-Location $bridgeDir

Write-Host "Starting WhatsApp Shadow Bridge in PAIRING mode." -ForegroundColor Cyan
Write-Host "A QR code will print below. Scan it now with the technician's phone:" -ForegroundColor Cyan
Write-Host "  WhatsApp app -> Settings -> Linked Devices -> Link a Device" -ForegroundColor Cyan
Write-Host ""

$env:WHATSAPP_SHADOW_ENABLED = "true"
# See start_normal.ps1 for why --env-file-if-exists is required here too.
node --env-file-if-exists=.env src/index.js --pair
