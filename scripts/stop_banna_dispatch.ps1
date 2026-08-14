<#
.SYNOPSIS
  Stops Banna Dispatch: the console/API (8512, and the adapter inside it)
  and the WhatsApp Shadow Bridge (8514).

.DESCRIPTION
  Deliberately narrow. It only stops processes that actually hold 8512
  or 8514, and only if they are the expected python/node executable --
  so it cannot kill an unrelated Python or Node workload the owner has
  running.

  SQLite safety: both processes are stopped, never the database. SQLite
  is durable across process termination here (each write is committed
  before the process returns), so stopping this way does not corrupt the
  file. Nothing deletes, moves or truncates data\dispatcher.db.

  SESSION safety: the WhatsApp linked-device session lives in
  .runtime\whatsapp-shadow-session\ and is NOT touched. Stopping the
  bridge is not logging out -- the next start reuses the same session.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\stop_banna_dispatch.ps1
#>

$ErrorActionPreference = "Continue"

function Stop-OnPort($port, $expectedName, $label) {
  $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if (-not $conn) {
    Write-Host "  $label ($port): not running." -ForegroundColor DarkGray
    return
  }
  foreach ($processId in ($conn.OwningProcess | Select-Object -Unique)) {
    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -match $expectedName) {
      Stop-Process -Id $processId -Force
      Write-Host "  $label ($port): stopped process $processId." -ForegroundColor Green
    } else {
      $actual = if ($proc) { $proc.ProcessName } else { "unknown" }
      Write-Host "  $label ($port): held by '$actual' (pid $processId), not '$expectedName'." -ForegroundColor Red
      Write-Host "    Not stopping it automatically. Investigate manually." -ForegroundColor Red
    }
  }
}

Write-Host ""
Write-Host "Stopping Banna Dispatch ..." -ForegroundColor Cyan
Stop-OnPort 8512 "python" "console + adapter"
Stop-OnPort 8514 "node"   "whatsapp bridge"
Write-Host ""
Write-Host "  Database untouched. WhatsApp session preserved -- the next" -ForegroundColor DarkGray
Write-Host "  start reuses it and will not ask for a QR code." -ForegroundColor DarkGray
Write-Host ""
