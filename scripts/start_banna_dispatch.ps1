<#
.SYNOPSIS
  Starts Banna Dispatch: the console/API (8512, which also hosts the
  loopback adapter on 8513) and the WhatsApp Shadow Bridge (8514).

.DESCRIPTION
  A thin wrapper. It does NOT replace scripts\start_crm.ps1 or
  reimplement anything -- it reuses what already exists and only adds
  "start both halves and tell me the truth about what happened".

  Anything already running is left alone: this can be run twice without
  starting a second copy of anything.

  PAIRING IS NEVER FORCED. The bridge is started in its normal run mode
  and reuses the existing linked-device session. If that session is not
  usable, this script says so ONCE, clearly, and stops -- it never loops
  pairing attempts and never prints a QR payload into a log file. The
  QR, if one is genuinely needed, appears only in the bridge's own
  terminal window, which is where the owner must look.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\start_banna_dispatch.ps1
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Test-Port($port) {
  $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host ""
Write-Host "BANNA DISPATCH" -ForegroundColor Cyan
Write-Host "Luxor Locksmith" -ForegroundColor DarkGray
Write-Host ""

# --- Console + API (8512), which starts the loopback adapter (8513) ---
if (Test-Port 8512) {
  Write-Host "[ok]    Console already running on 8512 (adapter 8513 runs inside it)." -ForegroundColor Green
} else {
  Write-Host "[start] Console + adapter ..." -ForegroundColor Cyan
  Start-Process -FilePath "python" -ArgumentList "operator_api.py","--port","8512" -WorkingDirectory $repoRoot
}

# --- WhatsApp Shadow Bridge (8514) ---
if (Test-Port 8514) {
  Write-Host "[ok]    WhatsApp bridge already running on 8514." -ForegroundColor Green
} else {
  Write-Host "[start] WhatsApp bridge ..." -ForegroundColor Cyan
  Start-Process -FilePath "node" `
    -ArgumentList "--env-file-if-exists=.env","src/index.js","--pair" `
    -WorkingDirectory (Join-Path $repoRoot "whatsapp_shadow_bridge")
}

# --- Wait briefly, then report REAL state (never a decorative green) ---
$deadline = (Get-Date).AddSeconds(45)
$consoleUp = $false
$bridgePaired = $false
while ((Get-Date) -lt $deadline -and -not ($consoleUp -and $bridgePaired)) {
  Start-Sleep -Seconds 3
  if (-not $consoleUp) {
    try { $consoleUp = (Invoke-WebRequest -Uri "http://127.0.0.1:8512/" -UseBasicParsing -TimeoutSec 4).StatusCode -eq 200 } catch {}
  }
  if (-not $bridgePaired) {
    try {
      $h = Invoke-WebRequest -Uri "http://127.0.0.1:8514/health" -UseBasicParsing -TimeoutSec 4
      $bridgePaired = ($h.Content | ConvertFrom-Json).paired
    } catch {}
  }
}

Write-Host ""
Write-Host "STATUS" -ForegroundColor Cyan
if ($consoleUp) { Write-Host "  8512 console   OK" -ForegroundColor Green }
else            { Write-Host "  8512 console   NOT RESPONDING - check its window" -ForegroundColor Red }

if (Test-Port 8513) { Write-Host "  8513 adapter   OK" -ForegroundColor Green }
else                { Write-Host "  8513 adapter   NOT LISTENING - it runs inside 8512" -ForegroundColor Red }

if ($bridgePaired) {
  Write-Host "  8514 whatsapp  OK (existing session reused)" -ForegroundColor Green
} else {
  Write-Host "  8514 whatsapp  NOT PAIRED" -ForegroundColor Yellow
  Write-Host ""
  Write-Host "  OWNER ACTION REQUIRED - WHATSAPP PAIRING" -ForegroundColor Yellow
  Write-Host "  The bridge could not confirm a usable session within 45s." -ForegroundColor Yellow
  Write-Host "  Look at the bridge terminal window. If a QR code is shown," -ForegroundColor Yellow
  Write-Host "  scan it from WhatsApp > Linked Devices. Nothing here retries" -ForegroundColor Yellow
  Write-Host "  automatically, and no QR is ever written to a log file." -ForegroundColor Yellow
}

# --- The phone URL, discovered now, never hardcoded ---
$lan = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.PrefixOrigin -eq "Dhcp" } |
        Select-Object -First 1).IPAddress
Write-Host ""
if ($lan) {
  Write-Host "  Phone (same Wi-Fi):  http://$($lan):8512/" -ForegroundColor Cyan
} else {
  Write-Host "  No DHCP LAN address found - phone access unavailable right now." -ForegroundColor Yellow
}
Write-Host "  This laptop:         http://127.0.0.1:8512/" -ForegroundColor Cyan
Write-Host ""
