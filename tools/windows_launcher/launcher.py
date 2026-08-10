"""Windows one-click launcher for the LUXOR dispatcher app.

Starts/stops/reports on exactly two processes -- the canonical
`operator_api.py` CRM (a plain stdlib http.server process, not
Streamlit -- see scripts/start_crm.ps1, the reference invocation this
matches) and one `cloudflared` tunnel pointed at it -- for a
non-technical user who should never need to open a terminal. Prior to
this fix, this module started the retired `luxor_desk.py` Streamlit
interface instead (a known, documented gap --
docs/CURRENT_SPRINT_CHECKPOINT.md and tools/system_readiness.py's own
"Startup scripts point to current CRM" check both flagged it). Internal
identifiers below (STREAMLIT_PID_FILE, streamlit_pid, etc.) are kept
as-is post-fix -- purely internal naming, not user-facing, and
renaming them everywhere would be a larger diff than this fix needs.
This module contains only process-lifecycle logic; it never
imports `dispatcher.*`, never reads `.env`, and never touches the
SQLite database. All real OS interaction (starting a process, checking
whether a PID is alive, reading its command line, terminating it,
health-checking a URL, opening a browser) is funneled through a single
`ProcessBackend` so tests can substitute a fake one -- no real process
is ever started, stopped, or network-called by the test suite.

Duplicate-prevention and "never kill an unrelated process" both rest on
the same mechanism: a small JSON PID file per process
(.deploy_logs/launcher/*.pid.json) that records not just the PID but a
command-line "signature" that must still match before this module will
ever treat that PID as "ours" -- a stale PID file pointing at a since-
reused PID (a different, unrelated process) is never mistaken for a
live dispatcher/tunnel process, and this module never does a broad
process-name sweep (e.g. "kill every python.exe").
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = PROJECT_ROOT / ".deploy_logs" / "launcher"

STREAMLIT_PID_FILE = RUN_DIR / "streamlit.pid.json"
TUNNEL_PID_FILE = RUN_DIR / "tunnel.pid.json"
STREAMLIT_OUT_LOG = RUN_DIR / "streamlit.out.log"
STREAMLIT_ERR_LOG = RUN_DIR / "streamlit.err.log"
TUNNEL_OUT_LOG = RUN_DIR / "tunnel.out.log"
TUNNEL_ERR_LOG = RUN_DIR / "tunnel.err.log"
TUNNEL_URL_FILE = RUN_DIR / "tunnel_url.txt"

# Matched as a substring against a live process's actual command line --
# never a bare process-name match. Keep in sync with the real command
# lines built in _start_streamlit()/_start_tunnel() below.
STREAMLIT_SIGNATURE = "operator_api.py"
TUNNEL_SIGNATURE = "cloudflared"

# Matches operator_api.py's own default (`--port` argparse default) and
# scripts/start_crm.ps1 / tools/system_readiness.py's port-8512 checks --
# keep all three in sync if this ever changes.
DEFAULT_PORT = 8512
HEALTH_CHECK_TIMEOUT_SECONDS = 30
HEALTH_CHECK_INTERVAL_SECONDS = 1
TUNNEL_URL_WAIT_TIMEOUT_SECONDS = 20
TUNNEL_URL_WAIT_INTERVAL_SECONDS = 1
TUNNEL_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------- Process backend
@dataclass
class ProcessBackend:
    """Every real OS/network call this module makes goes through one of
    these methods. Production code uses `real_backend()`; tests build a
    `ProcessBackend` with fake callables instead -- launcher.py itself
    never calls subprocess/urllib/webbrowser directly outside this
    class's real implementations, so a test can never accidentally
    start a real process or hit a real network address."""

    popen: Callable[[list, Path, Path, Path], int]
    is_pid_alive: Callable[[int], bool]
    get_commandline: Callable[[int], Optional[str]]
    terminate: Callable[[int], None]
    urlopen_ok: Callable[[str], bool]
    open_browser: Callable[[str], None]
    read_log: Callable[[Path], str]


def _real_popen(args: list, cwd: Path, out_log: Path, err_log: Path) -> int:
    out_log.parent.mkdir(parents=True, exist_ok=True)
    with open(out_log, "ab") as out_f, open(err_log, "ab") as err_f:
        proc = subprocess.Popen(args, cwd=str(cwd), stdout=out_f, stderr=err_f, stdin=subprocess.DEVNULL)
    return proc.pid


def _real_is_pid_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return str(pid) in result.stdout


def _real_get_commandline(pid: int) -> Optional[str]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _real_terminate(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)


def _real_urlopen_ok(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # any HTTP response (even 4xx/5xx) means the server is up
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _real_read_log(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def real_backend() -> ProcessBackend:
    return ProcessBackend(
        popen=_real_popen, is_pid_alive=_real_is_pid_alive, get_commandline=_real_get_commandline,
        terminate=_real_terminate, urlopen_ok=_real_urlopen_ok, open_browser=webbrowser.open,
        read_log=_real_read_log,
    )


# --------------------------------------------------------------- PID files
def _read_pid_file(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_pid_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _tracked_pid_if_alive(pid_file: Path, expected_signature: str, backend: ProcessBackend) -> Optional[int]:
    """The PID recorded in `pid_file` IF it's still alive AND its actual
    command line still contains `expected_signature` -- None otherwise
    (covers: no PID file, process exited, or the PID was reused by an
    unrelated process since)."""
    record = _read_pid_file(pid_file)
    if not record or "pid" not in record:
        return None
    pid = record["pid"]
    if not backend.is_pid_alive(pid):
        return None
    cmdline = backend.get_commandline(pid) or ""
    if expected_signature not in cmdline:
        return None
    return pid


# ------------------------------------------------------------------- start
def _start_streamlit(backend: ProcessBackend, port: int) -> int:
    # operator_api.py is a plain stdlib http.server process (see its own
    # main()/argparse block) -- no Streamlit-specific flags apply. Never
    # passes --no-bridge-adapter here: a real one-click launch is meant
    # to behave like scripts/start_crm.ps1's `python operator_api.py`,
    # which starts the WhatsApp bridge *adapter* (the receive-only
    # Python listener on port 8513) normally -- this is unrelated to
    # the separate Node WhatsApp Shadow Bridge process/pairing, which
    # this launcher has never started or touched.
    args = [sys.executable, "operator_api.py", "--port", str(port)]
    return backend.popen(args, PROJECT_ROOT, STREAMLIT_OUT_LOG, STREAMLIT_ERR_LOG)


def _start_tunnel(backend: ProcessBackend, port: int) -> int:
    args = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
    return backend.popen(args, PROJECT_ROOT, TUNNEL_OUT_LOG, TUNNEL_ERR_LOG)


def _wait_for_health(
    backend: ProcessBackend, port: int, timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS,
    interval: float = HEALTH_CHECK_INTERVAL_SECONDS, sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    url = f"http://localhost:{port}"
    ticks = max(1, int(timeout / interval))
    for _ in range(ticks):
        if backend.urlopen_ok(url):
            return True
        sleep_fn(interval)
    return backend.urlopen_ok(url)


def _wait_for_tunnel_url(
    backend: ProcessBackend, timeout: float = TUNNEL_URL_WAIT_TIMEOUT_SECONDS,
    interval: float = TUNNEL_URL_WAIT_INTERVAL_SECONDS, sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[str]:
    ticks = max(1, int(timeout / interval))
    for _ in range(ticks):
        text = backend.read_log(TUNNEL_OUT_LOG) + backend.read_log(TUNNEL_ERR_LOG)
        match = TUNNEL_URL_PATTERN.search(text)
        if match:
            return match.group(0)
        sleep_fn(interval)
    text = backend.read_log(TUNNEL_OUT_LOG) + backend.read_log(TUNNEL_ERR_LOG)
    match = TUNNEL_URL_PATTERN.search(text)
    return match.group(0) if match else None


def start(backend: Optional[ProcessBackend] = None, port: int = DEFAULT_PORT, sleep_fn: Callable[[float], None] = time.sleep) -> dict:
    backend = backend or real_backend()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    messages: list = []

    streamlit_pid = _tracked_pid_if_alive(STREAMLIT_PID_FILE, STREAMLIT_SIGNATURE, backend)
    if streamlit_pid is not None:
        messages.append(f"Dispatcher already running (PID {streamlit_pid}) -- not starting a second copy.")
    else:
        try:
            streamlit_pid = _start_streamlit(backend, port)
            _write_pid_file(STREAMLIT_PID_FILE, {"pid": streamlit_pid, "port": port, "signature": STREAMLIT_SIGNATURE, "started_at": _now_iso()})
            messages.append(f"Started dispatcher (PID {streamlit_pid}).")
        except (OSError, FileNotFoundError) as exc:
            messages.append(f"Could not start the dispatcher: {exc}")
            return {"streamlit_pid": None, "tunnel_pid": None, "tunnel_url": None, "healthy": False, "messages": messages}

    healthy = _wait_for_health(backend, port, sleep_fn=sleep_fn)
    messages.append("Dispatcher is responding." if healthy else "Dispatcher did not respond within the timeout -- check streamlit.err.log.")

    tunnel_pid = _tracked_pid_if_alive(TUNNEL_PID_FILE, TUNNEL_SIGNATURE, backend)
    if tunnel_pid is not None:
        messages.append(f"Tunnel already running (PID {tunnel_pid}) -- not starting a second copy.")
    else:
        try:
            tunnel_pid = _start_tunnel(backend, port)
            _write_pid_file(TUNNEL_PID_FILE, {"pid": tunnel_pid, "port": port, "signature": TUNNEL_SIGNATURE, "started_at": _now_iso()})
            messages.append(f"Started tunnel (PID {tunnel_pid}).")
        except (OSError, FileNotFoundError) as exc:
            messages.append(f"Could not start the tunnel (is cloudflared installed and on PATH?): {exc}")
            return {"streamlit_pid": streamlit_pid, "tunnel_pid": None, "tunnel_url": None, "healthy": healthy, "messages": messages}

    tunnel_url = _wait_for_tunnel_url(backend, sleep_fn=sleep_fn)
    if tunnel_url:
        TUNNEL_URL_FILE.write_text(tunnel_url, encoding="utf-8")
        messages.append(f"Tunnel URL: {tunnel_url}")
        backend.open_browser(tunnel_url)
    else:
        messages.append("Could not detect the tunnel URL yet -- check tunnel.out.log, or run Dispatcher Status again shortly.")

    return {"streamlit_pid": streamlit_pid, "tunnel_pid": tunnel_pid, "tunnel_url": tunnel_url, "healthy": healthy, "messages": messages}


# -------------------------------------------------------------------- stop
def stop(backend: Optional[ProcessBackend] = None) -> dict:
    backend = backend or real_backend()
    messages: list = []
    stopped = {"streamlit": False, "tunnel": False}

    for name, pid_file, signature in (
        ("streamlit", STREAMLIT_PID_FILE, STREAMLIT_SIGNATURE),
        ("tunnel", TUNNEL_PID_FILE, TUNNEL_SIGNATURE),
    ):
        pid = _tracked_pid_if_alive(pid_file, signature, backend)
        if pid is not None:
            backend.terminate(pid)
            stopped[name] = True
            messages.append(f"Stopped {name} (PID {pid}).")
        else:
            messages.append(f"{name.capitalize()} was not running (or its PID file was stale) -- nothing to stop.")
        if pid_file.exists():
            pid_file.unlink()

    return {"stopped": stopped, "messages": messages}


# ------------------------------------------------------------------ status
def status(backend: Optional[ProcessBackend] = None) -> dict:
    backend = backend or real_backend()
    streamlit_pid = _tracked_pid_if_alive(STREAMLIT_PID_FILE, STREAMLIT_SIGNATURE, backend)
    tunnel_pid = _tracked_pid_if_alive(TUNNEL_PID_FILE, TUNNEL_SIGNATURE, backend)
    tunnel_url = None
    if TUNNEL_URL_FILE.exists():
        try:
            tunnel_url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip() or None
        except OSError:
            tunnel_url = None

    return {
        "streamlit_running": streamlit_pid is not None,
        "streamlit_pid": streamlit_pid,
        "tunnel_running": tunnel_pid is not None,
        "tunnel_pid": tunnel_pid,
        "tunnel_url": tunnel_url,
    }


# -------------------------------------------------------------------- open
def open_dispatcher(backend: Optional[ProcessBackend] = None, port: int = DEFAULT_PORT) -> dict:
    backend = backend or real_backend()
    current = status(backend)
    if current["tunnel_url"]:
        backend.open_browser(current["tunnel_url"])
        return {"opened": current["tunnel_url"], "messages": [f"Opened {current['tunnel_url']}"]}
    if current["streamlit_running"]:
        local_url = f"http://localhost:{port}"
        backend.open_browser(local_url)
        return {"opened": local_url, "messages": [f"Tunnel URL not known yet -- opened the local address {local_url} instead."]}
    return {"opened": None, "messages": ["Dispatcher is not running -- use Start Dispatcher first."]}


# --------------------------------------------------------------------- CLI
def _print_messages(result: dict) -> None:
    for line in result.get("messages", []):
        print(line)


def main(argv: Optional[list] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: launcher.py {start|stop|status|open}")
        return 2
    verb = argv[0]
    if verb == "start":
        _print_messages(start())
        return 0
    if verb == "stop":
        _print_messages(stop())
        return 0
    if verb == "status":
        s = status()
        print(f"Dispatcher: {'running (PID ' + str(s['streamlit_pid']) + ')' if s['streamlit_running'] else 'not running'}")
        print(f"Tunnel: {'running (PID ' + str(s['tunnel_pid']) + ')' if s['tunnel_running'] else 'not running'}")
        print(f"URL: {s['tunnel_url'] or '(not known yet)'}")
        return 0
    if verb == "open":
        _print_messages(open_dispatcher())
        return 0
    print(f"Unknown command: {verb!r}. Usage: launcher.py {{start|stop|status|open}}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
