<#
    Banna Dispatch -- keep the three services alive.

    Idempotent by design: it starts only what is NOT already running, so
    it is safe to run at logon AND on a repeating timer. The timer is
    what makes this a watchdog rather than a launcher -- a crashed
    service comes back within the repeat interval without anyone
    noticing it died.

    Starts nothing new and changes no application behaviour. Each
    command here is exactly the one that was already being run by hand.
#>

param([switch]$Watch, [int]$IntervalSeconds = 300)

# -Watch turns this from a one-shot launcher into the watchdog. It is
# a loop rather than a Scheduled Task because registering a task needs
# Administrator and this must work without it. Started from the user's
# Startup folder, it covers both cases that matter: nothing running
# after a reboot, and something dying mid-shift.

$ErrorActionPreference = "Stop"
$Root   = "C:\Users\child\Desktop\ai dispatcher"
$Python = "C:\Users\child\AppData\Local\Programs\Python\Python312\python.exe"
$Node   = "C:\Program Files\nodejs\node.exe"
$Log    = Join-Path $Root "ops\banna_services.log"
$Status = Join-Path $Root "ops\service_status.json"

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function Test-Listening($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Test-ProcRunning($pattern) {
    [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -like $pattern })
}

function Write-Status($api, $bridge, $quo) {
    # The CRM cannot ask the bridge whether it is alive -- operator_api
    # is forbidden from making outbound HTTP, and that rule is right.
    # The watchdog already knows the truth about all three services, so
    # it records it here and the CRM reads the file. The timestamp
    # matters as much as the flags: if THIS file goes stale, the
    # watchdog itself has died, and the CRM says so rather than
    # reporting the last thing it happened to see.
    $payload = [ordered]@{
        checked_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        api        = [bool]$api
        bridge     = [bool]$bridge
        quo        = [bool]$quo
    }
    $tmp = "$Status.tmp"
    $payload | ConvertTo-Json | Set-Content -Path $tmp -Encoding utf8
    Move-Item -Path $tmp -Destination $Status -Force
}

function Invoke-Once {
# --- 1. operator API / CRM (port 8512, also hosts 8513) --------------
if (Test-Listening 8512) {
    Write-Log "api: already listening on 8512"
} else {
    Start-Process -FilePath $Python -ArgumentList 'operator_api.py','--port','8512' `
                  -WorkingDirectory $Root -WindowStyle Minimized
    Write-Log "api: STARTED"
}

# --- 2. WhatsApp shadow bridge (port 8514) ---------------------------
# --pair is required; without it the bridge starts and then sits idle.
# The stored LocalAuth session means this reconnects without a QR.
if (Test-Listening 8514) {
    Write-Log "bridge: already listening on 8514"
} else {
    Start-Process -FilePath $Node `
                  -ArgumentList '--env-file-if-exists=.env','src/index.js','--pair' `
                  -WorkingDirectory (Join-Path $Root "whatsapp_shadow_bridge") -WindowStyle Minimized
    Write-Log "bridge: STARTED"
}

# --- 3. Quo poller ---------------------------------------------------
# Forward-only: it never rewinds the checkpoint, so a restart cannot
# re-import the backlog as if it were new work.
if (Test-ProcRunning '*run_quo_poller*') {
    Write-Log "quo: already running"
} else {
    Start-Process -FilePath $Python -ArgumentList 'scripts/run_quo_poller.py' `
                  -WorkingDirectory $Root -WindowStyle Minimized
    Write-Log "quo: STARTED"
}

# Recorded AFTER any restart above, so the file reflects the state the
# dispatcher will actually meet, not the state a moment before repair.
Start-Sleep -Seconds 2
Write-Status (Test-Listening 8512) (Test-Listening 8514) (Test-ProcRunning '*run_quo_poller*')
}

if ($Watch) {
    Write-Log "watchdog: started (every $IntervalSeconds s)"
    while ($true) {
        try { Invoke-Once } catch { Write-Log ("watchdog error: " + $_.Exception.Message) }
        Start-Sleep -Seconds $IntervalSeconds
    }
} else {
    Invoke-Once
}
