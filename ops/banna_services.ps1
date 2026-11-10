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
$ProbeState = Join-Path $Root "ops\bridge_probe_state.json"

# Restart policy. One failed probe is not evidence of a broken session:
# a slow answer under load, or a probe fired mid-pairing, both look like
# a failure and both fix themselves. Restarting on the first one would
# interrupt a healthy bridge and, in the worst case, loop forever.
$FailuresBeforeRestart = 3     # consecutive, not cumulative
$RestartCooldownMinutes = 15   # no second restart inside this window
$MaxRestartsPerWindow = 3      # ...
$RestartWindowHours = 6        # ...within this many hours, then stop trying

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

function Read-ProbeState {
    try {
        if (Test-Path $ProbeState) {
            $j = Get-Content $ProbeState -Raw -Encoding utf8 | ConvertFrom-Json
            return @{
                failures    = [int]$j.consecutiveFailures
                lastRestart = $j.lastRestartAt
                restarts    = @($j.recentRestarts)
            }
        }
    } catch { }
    return @{ failures = 0; lastRestart = $null; restarts = @() }
}

function Write-ProbeState($state) {
    $payload = [ordered]@{
        consecutiveFailures = [int]$state.failures
        lastRestartAt       = $state.lastRestart
        recentRestarts      = @($state.restarts)
        updatedAt           = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    $tmp = "$ProbeState.tmp"
    $payload | ConvertTo-Json | Set-Content -Path $tmp -Encoding utf8
    Move-Item -Path $tmp -Destination $ProbeState -Force
}

function Test-BridgeReady {
    # "Listening on 8514" is not the same as "WhatsApp capture works".
    # The bridge's own /health answers from a flag set once at connect,
    # so a dead page still reports connected -- that is exactly how
    # group discovery broke silently while the port stayed open and the
    # supervisor restarted nothing. /ready exercises the real client.
    #
    # READ ONLY. The probe's entire contact with WhatsApp is one
    # getChats() call; the bridge has no send capability at all and a
    # static check fails the build if one ever appears.
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8514/ready" -UseBasicParsing -TimeoutSec 30
        $j = $r.Content | ConvertFrom-Json
        if ($j.ready) { return @{ ok = $true; reason = "ready (groups: $($j.groups))" } }
        return @{ ok = $false; reason = $j.reason }
    } catch {
        return @{ ok = $false; reason = "probe_unreachable: $($_.Exception.Message)" }
    }
}

function Restart-Bridge($why) {
    Write-Log "bridge: RESTARTING -- $why"
    $b = Get-NetTCPConnection -LocalPort 8514 -State Listen -ErrorAction SilentlyContinue
    if ($b) { Stop-Process -Id ($b | Select-Object -First 1).OwningProcess -Force -ErrorAction SilentlyContinue }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-NetTCPConnection -LocalPort 8514 -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }

    # THE SESSION IS NEVER DELETED. This stops a browser PROCESS that is
    # still holding the session profile open; the stored linked-device
    # credentials under .runtime/ are not touched, so the bridge
    # reconnects without a QR. Only run when something is actually
    # holding the profile, so a healthy browser is never killed.
    $orphans = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
               Where-Object { $_.CommandLine -like '*whatsapp-shadow-session*' }
    if ($orphans) {
        Write-Log "bridge: releasing $(@($orphans).Count) browser process(es) still holding the session profile (session data untouched)"
        $orphans | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }

    Start-Process -FilePath $Node `
                  -ArgumentList '--env-file-if-exists=.env','src/index.js','--pair' `
                  -WorkingDirectory (Join-Path $Root "whatsapp_shadow_bridge") -WindowStyle Minimized `
                  -RedirectStandardOutput (Join-Path $Root "ops\bridge.out.log") `
                  -RedirectStandardError  (Join-Path $Root "ops\bridge.err.log")
    Write-Log "bridge: restarted"
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

# A listening bridge that cannot answer is worse than a stopped one:
# it looks healthy. Probed every cycle -- but a single failure is never
# acted on, because a slow answer under load or a probe fired while the
# session is still pairing both look exactly like a broken session and
# both recover on their own.
$bridgeReady = $true
if (Test-Listening 8514) {
    $probe = Test-BridgeReady
    $st = Read-ProbeState

    if ($probe.ok) {
        if ($st.failures -gt 0) { Write-Log "bridge: recovered after $($st.failures) failed probe(s)" }
        else { Write-Log "bridge: $($probe.reason)" }
        $st.failures = 0
        Write-ProbeState $st
    } else {
        $st.failures = $st.failures + 1
        $bridgeReady = $false
        Write-Log "bridge: probe FAILED ($($st.failures)/$FailuresBeforeRestart) -- reason: $($probe.reason)"

        # Prune restarts out of the rolling window before counting.
        $cutoff = (Get-Date).ToUniversalTime().AddHours(-$RestartWindowHours)
        $recent = @($st.restarts | Where-Object {
            $_ -and ([datetime]::Parse($_, [Globalization.CultureInfo]::InvariantCulture,
                     [Globalization.DateTimeStyles]::AdjustToUniversal) -gt $cutoff) })
        $st.restarts = $recent

        $cooling = $false
        if ($st.lastRestart) {
            $since = ((Get-Date).ToUniversalTime() - [datetime]::Parse($st.lastRestart,
                      [Globalization.CultureInfo]::InvariantCulture,
                      [Globalization.DateTimeStyles]::AdjustToUniversal)).TotalMinutes
            if ($since -lt $RestartCooldownMinutes) { $cooling = $true }
        }

        if ($st.failures -lt $FailuresBeforeRestart) {
            Write-Log "bridge: not restarting yet -- $FailuresBeforeRestart consecutive failures required"
        } elseif ($cooling) {
            Write-Log "bridge: in cooldown -- last restart was less than $RestartCooldownMinutes minutes ago"
        } elseif (@($recent).Count -ge $MaxRestartsPerWindow) {
            # The loop stopper. If restarting has not fixed it this many
            # times, restarting again will not either -- it only hides a
            # fault that needs a human. The banner keeps reporting the
            # outage because bridgeReady stays false.
            Write-Log "bridge: GIVING UP -- $(@($recent).Count) restarts in the last $RestartWindowHours h have not fixed it. Needs a human. Reason: $($probe.reason)"
        } else {
            Restart-Bridge $probe.reason
            $now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            $st.lastRestart = $now
            $st.restarts = @($recent) + @($now)
            $st.failures = 0   # give the fresh process a clean slate
        }
        Write-ProbeState $st
    }
}

# Recorded AFTER any restart above, so the file reflects the state the
# dispatcher will actually meet, not the state a moment before repair.
Start-Sleep -Seconds 2
Write-Status (Test-Listening 8512) ((Test-Listening 8514) -and $bridgeReady) (Test-ProcRunning '*run_quo_poller*')
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
