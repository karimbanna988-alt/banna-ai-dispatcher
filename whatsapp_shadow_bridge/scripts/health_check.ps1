<#
.SYNOPSIS
  Checks whether the Node bridge admin server (8514) and Python adapter
  (8513) are up, and confirms both are loopback-only. Read-only.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1
#>

$ErrorActionPreference = "Continue"

function Check-Endpoint($name, $port, $path) {
  $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if (-not $conn) {
    Write-Host "  [DOWN] $name (port $port) -- not listening" -ForegroundColor Yellow
    return
  }
  $boundAddrs = ($conn.LocalAddress | Select-Object -Unique) -join ", "
  if ($boundAddrs -notmatch "^127\.0\.0\.1$") {
    Write-Host "  [WARN] $name (port $port) is listening but NOT loopback-only: $boundAddrs" -ForegroundColor Red
  } else {
    Write-Host "  [ OK ] $name (port $port) is listening on 127.0.0.1 only" -ForegroundColor Green
  }
  try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$port$path" -TimeoutSec 3
    Write-Host "         response: $($resp | ConvertTo-Json -Compress)"
  } catch {
    Write-Host "         (endpoint did not respond to $path -- process may still be starting up)" -ForegroundColor Yellow
  }
}

Write-Host "WhatsApp Shadow Bridge health check" -ForegroundColor Cyan
Write-Host "===================================="
Check-Endpoint "Node bridge admin server" 8514 "/health"
Check-Endpoint "Python bridge adapter"    8513 "/health"
