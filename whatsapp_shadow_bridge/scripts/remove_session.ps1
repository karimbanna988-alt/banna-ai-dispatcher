<#
.SYNOPSIS
  Deletes the local linked-device session (forces a fresh QR pairing
  next time). Requires explicit typed confirmation -- never runs
  unattended, and never touches the WhatsApp account itself (it does
  not log the account out remotely; use WhatsApp's own Linked Devices
  screen on the phone for that).

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\remove_session.ps1
#>

$ErrorActionPreference = "Stop"
$bridgeDir = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $bridgeDir
$sessionDir = Join-Path $repoRoot ".runtime\whatsapp-shadow-session"

if (-not (Test-Path $sessionDir)) {
  Write-Host "No local session found at $sessionDir -- nothing to remove." -ForegroundColor Yellow
  exit 0
}

Write-Host "This will permanently delete the local linked-device session at:" -ForegroundColor Red
Write-Host "  $sessionDir" -ForegroundColor Red
Write-Host "You will need to run scripts\start_pairing.ps1 and scan a new QR to reconnect." -ForegroundColor Red
Write-Host "This does NOT remove the device from the phone's own Linked Devices list --" -ForegroundColor Yellow
Write-Host "do that on the phone too if you want a full disconnect." -ForegroundColor Yellow
Write-Host ""
$confirm = Read-Host "Type REMOVE (in capitals) to confirm"

if ($confirm -ceq "REMOVE") {
  Remove-Item -Recurse -Force $sessionDir
  Write-Host "Session removed." -ForegroundColor Green
} else {
  Write-Host "Not confirmed -- nothing was removed." -ForegroundColor Yellow
}
