<#
.SYNOPSIS
  WhatsApp Shadow Bridge preflight check. Read-only -- never pairs an
  account, never starts the bridge, never prints or logs a secret value.

.DESCRIPTION
  Run this before ever starting pairing mode. It only inspects local
  files, local environment variables, and local port state. Exit code 0
  means every check passed; a non-zero exit code means at least one
  BLOCKING check failed (warnings alone do not fail the script).

.USAGE
  cd whatsapp_shadow_bridge
  powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1
#>

$ErrorActionPreference = "Stop"
$bridgeDir = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $bridgeDir

$blocking = @()
$warnings = @()

function Test-Pass($label) { Write-Host "  [ OK ] $label" -ForegroundColor Green }
function Test-Warn($label, $detail) { Write-Host "  [WARN] $label -- $detail" -ForegroundColor Yellow; $script:warnings += $label }
function Test-Fail($label, $detail) { Write-Host "  [FAIL] $label -- $detail" -ForegroundColor Red; $script:blocking += $label }

Write-Host "WhatsApp Shadow Bridge preflight" -ForegroundColor Cyan
Write-Host "================================="

# 1. Node and npm exist
try {
  $nodeVersion = (node --version) 2>$null
  if ($nodeVersion) { Test-Pass "node available ($nodeVersion)" } else { Test-Fail "node available" "not found on PATH" }
} catch { Test-Fail "node available" "not found on PATH" }

try {
  $npmVersion = (npm --version) 2>$null
  if ($npmVersion) { Test-Pass "npm available (v$npmVersion)" } else { Test-Fail "npm available" "not found on PATH" }
} catch { Test-Fail "npm available" "not found on PATH" }

# 2. Dependencies installed
$nodeModules = Join-Path $bridgeDir "node_modules"
if (Test-Path $nodeModules) { Test-Pass "node_modules installed" }
else { Test-Fail "node_modules installed" "run 'npm install' in whatsapp_shadow_bridge\ first" }

# 3. Local .env exists (Node side)
$bridgeEnv = Join-Path $bridgeDir ".env"
$bridgeEnvExists = Test-Path $bridgeEnv
if ($bridgeEnvExists) { Test-Pass "whatsapp_shadow_bridge\.env exists" }
else { Test-Fail "whatsapp_shadow_bridge\.env exists" "copy .env.example to .env and set WHATSAPP_SHADOW_ADAPTER_SECRET" }

# 4. Root .env exists (Python side)
$rootEnv = Join-Path $repoRoot ".env"
$rootEnvExists = Test-Path $rootEnv
if ($rootEnvExists) { Test-Pass "project .env exists" }
else { Test-Fail "project .env exists" "run operator_api.py once to auto-generate it" }

# 5. Secrets match (booleans only, values never printed/logged)
if ($bridgeEnvExists -and $rootEnvExists) {
  $rootSecret = (Select-String -Path $rootEnv -Pattern '^WHATSAPP_BRIDGE_ADAPTER_SECRET=(.+)$').Matches.Groups[1].Value
  $bridgeSecret = (Select-String -Path $bridgeEnv -Pattern '^WHATSAPP_SHADOW_ADAPTER_SECRET=(.+)$').Matches.Groups[1].Value
  if ([string]::IsNullOrWhiteSpace($rootSecret)) { Test-Fail "adapter secret configured (Python side)" "WHATSAPP_BRIDGE_ADAPTER_SECRET is empty" }
  elseif ([string]::IsNullOrWhiteSpace($bridgeSecret)) { Test-Fail "adapter secret configured (Node side)" "WHATSAPP_SHADOW_ADAPTER_SECRET is empty" }
  elseif ($rootSecret.Trim() -eq $bridgeSecret.Trim()) { Test-Pass "Node and Python adapter secrets match" }
  else { Test-Fail "Node and Python adapter secrets match" "values differ -- copy the Python side's WHATSAPP_BRIDGE_ADAPTER_SECRET into the Node side's WHATSAPP_SHADOW_ADAPTER_SECRET" }
} else {
  Test-Warn "secret match check" "skipped, one or both .env files are missing (see above)"
}

# 6. Ports 8513 (Python adapter) and 8514 (Node admin) available or correctly occupied
foreach ($portInfo in @(@{Port=8513; Owner="Python adapter (whatsapp_bridge_adapter.py)"}, @{Port=8514; Owner="Node admin server (healthServer.js)"})) {
  $conn = Get-NetTCPConnection -LocalPort $portInfo.Port -State Listen -ErrorAction SilentlyContinue
  if (-not $conn) {
    Test-Pass "port $($portInfo.Port) is free ($($portInfo.Owner) not currently running)"
  } else {
    $boundAddrs = $conn.LocalAddress | Select-Object -Unique
    if ($boundAddrs -contains "0.0.0.0" -or $boundAddrs -contains "::") {
      Test-Fail "port $($portInfo.Port) binding" "listening on all interfaces, not loopback-only -- this must never happen"
    } else {
      Test-Pass "port $($portInfo.Port) is already listening on loopback only ($($boundAddrs -join ', '))"
    }
  }
}

# 7. Source-level binding check (static, no process needs to be running)
$healthServerSrc = Join-Path $bridgeDir "src\healthServer.js"
$adapterSrc = Join-Path $repoRoot "whatsapp_bridge_adapter.py"
if ((Select-String -Path $healthServerSrc -Pattern '127\.0\.0\.1' -Quiet) -and -not (Select-String -Path $healthServerSrc -Pattern 'listen\([^)]*0\.0\.0\.0' -Quiet)) {
  Test-Pass "healthServer.js binds 127.0.0.1 only (source check)"
} else { Test-Fail "healthServer.js binding" "does not clearly bind 127.0.0.1-only in source" }
if ((Select-String -Path $adapterSrc -Pattern '\("127\.0\.0\.1"' -Quiet) -and -not (Select-String -Path $adapterSrc -Pattern '\("0\.0\.0\.0"' -Quiet)) {
  Test-Pass "whatsapp_bridge_adapter.py binds 127.0.0.1 only (source check)"
} else { Test-Fail "whatsapp_bridge_adapter.py binding" "does not clearly bind 127.0.0.1-only in source" }

# 8. node_modules and session/runtime files are gitignored
function Test-GitIgnored($relativePath) {
  Push-Location $repoRoot
  try {
    git check-ignore -q $relativePath 2>$null
    return ($LASTEXITCODE -eq 0)
  } finally { Pop-Location }
}

if (Test-GitIgnored "whatsapp_shadow_bridge/node_modules/x") { Test-Pass "node_modules is gitignored" } else { Test-Fail "node_modules is gitignored" "check .gitignore" }
if (Test-GitIgnored "whatsapp_shadow_bridge/.env") { Test-Pass "whatsapp_shadow_bridge\.env is gitignored" } else { Test-Fail "whatsapp_shadow_bridge\.env is gitignored" "check .gitignore" }
if (Test-GitIgnored ".runtime/whatsapp-shadow-session/x") { Test-Pass ".runtime/ (session data) is gitignored" } else { Test-Fail ".runtime/ is gitignored" "check .gitignore" }

# 9. WHATSAPP_SHADOW_ENABLED is not accidentally enabled globally (a real process-level env var, not just .env)
$globalEnabled = [System.Environment]::GetEnvironmentVariable("WHATSAPP_SHADOW_ENABLED", "User")
$globalEnabledMachine = [System.Environment]::GetEnvironmentVariable("WHATSAPP_SHADOW_ENABLED", "Machine")
if ($globalEnabled -eq "true" -or $globalEnabledMachine -eq "true") {
  Test-Warn "WHATSAPP_SHADOW_ENABLED global env var" "set to true at User/Machine scope -- this means EVERY terminal has it enabled by default, not just an explicit pairing session. Consider removing it and setting it per-command instead."
} else {
  Test-Pass "WHATSAPP_SHADOW_ENABLED is not set globally (User/Machine scope)"
}

Write-Host ""
Write-Host "================================="
if ($blocking.Count -eq 0) {
  Write-Host "PREFLIGHT PASSED" -ForegroundColor Green -NoNewline
  Write-Host " -- $($warnings.Count) warning(s), 0 blocking failure(s)."
  exit 0
} else {
  Write-Host "PREFLIGHT FAILED" -ForegroundColor Red -NoNewline
  Write-Host " -- $($blocking.Count) blocking failure(s): $($blocking -join '; ')"
  exit 1
}
