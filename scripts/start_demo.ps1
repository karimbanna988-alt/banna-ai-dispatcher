<#
.SYNOPSIS
  One-command, fully isolated sales/training demo. Never touches
  data\dispatcher.db, never connects to the real Quo account, never
  starts the WhatsApp bridge (so it cannot connect to WhatsApp either).

.DESCRIPTION
  - Seeds a throwaway SQLite database at .runtime\demo\demo.db with
    clearly fictional jobs/technicians/customers, through the same real
    DispatcherService/route-handler code paths the real app uses (see
    scripts\seed_command_center_demo_db.py) -- not hand-built rows.
  - Deletes and recreates that database every run, so the demo always
    starts from the same known state.
  - Starts operator_api.py on port 8517 (a dedicated demo port, distinct
    from the real CRM's 8512) with:
      * OPERATOR_API_DB_PATH pointed at the throwaway demo database
      * LUXOR_DEMO_MODE=true, which makes the CRM show a red "DEMO MODE"
        banner in the UI (see route_me / route_login in operator_api.py)
      * --no-bridge-adapter, so the Python WhatsApp bridge adapter
        listener never even starts for this process
      * QUO_API_KEY cleared for this process only, so even a manual
        "Poll Quo now" click in demo mode cannot reach the real Quo
        account -- it fails cleanly as "not configured" instead
  - Never starts whatsapp_shadow_bridge (the Node process) at all, so
    there is no WhatsApp connection of any kind while the demo runs.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "===================================================="  -ForegroundColor Cyan
Write-Host " LUXOR sanitized demo launcher (DEMO MODE)"              -ForegroundColor Cyan
Write-Host "===================================================="  -ForegroundColor Cyan

$demoDbDir = Join-Path $repoRoot ".runtime\demo"
$demoDbPath = Join-Path $demoDbDir "demo.db"
New-Item -ItemType Directory -Force -Path $demoDbDir | Out-Null

# Hard, structural guarantee this can never be the real database --
# scripts\seed_command_center_demo_db.py itself also refuses to seed
# storage.DB_PATH, so this is defense in depth, not the only guard.
$realDbPath = (Join-Path $repoRoot "data\dispatcher.db")
if ((Resolve-Path -LiteralPath $demoDbPath -ErrorAction SilentlyContinue) -and
    ((Resolve-Path -LiteralPath $demoDbPath).Path -eq (Resolve-Path -LiteralPath $realDbPath -ErrorAction SilentlyContinue).Path)) {
  Write-Host "[STOP] Refusing to continue -- demo DB path resolved to the real database path." -ForegroundColor Red
  exit 1
}

$demoPort = 8517
$existing = Get-NetTCPConnection -LocalPort $demoPort -State Listen -ErrorAction SilentlyContinue
if ($existing) {
  Write-Host "[SKIP] Something is already listening on port $demoPort -- assuming the demo is already running." -ForegroundColor Yellow
  Write-Host "Demo URL: http://127.0.0.1:$demoPort" -ForegroundColor Cyan
  exit 0
}

Write-Host "Seeding fresh synthetic demo data at $demoDbPath ..." -ForegroundColor Cyan
if (Test-Path $demoDbPath) { Remove-Item -Force $demoDbPath }
$env:OPERATOR_API_DB_PATH = $demoDbPath
python scripts\seed_command_center_demo_db.py --db-path $demoDbPath
if ($LASTEXITCODE -ne 0) {
  Write-Host "[FAIL] Seeding the demo database failed -- see the error above." -ForegroundColor Red
  exit 1
}

Write-Host "Starting the demo CRM on http://127.0.0.1:$demoPort (isolated demo database, no WhatsApp bridge, no real Quo access) ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
cd '$repoRoot'
`$env:OPERATOR_API_DB_PATH = '$demoDbPath'
`$env:LUXOR_DEMO_MODE = 'true'
`$env:QUO_API_KEY = ''
python operator_api.py --port $demoPort --no-bridge-adapter
"@

Write-Host ""
Write-Host "Waiting a few seconds for the demo server to come up ..." -ForegroundColor Cyan
Start-Sleep -Seconds 4

try {
  $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoPort/api/me" -TimeoutSec 5
  Write-Host "[ OK ] Demo CRM reachable." -ForegroundColor Green
} catch {
  Write-Host "[WARN] Demo CRM not confirmed yet -- check the new window for errors." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Demo URL: http://127.0.0.1:$demoPort" -ForegroundColor Cyan
Write-Host "Login with the same password as the real CRM (LUXOR_DEMO_PASSWORD in .env)." -ForegroundColor Cyan
Write-Host "The app will show a red DEMO MODE banner across the top." -ForegroundColor Cyan
Write-Host "To stop: powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1" -ForegroundColor Cyan
