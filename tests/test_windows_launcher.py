"""Tests for tools/windows_launcher/launcher.py -- the Windows one-click
launcher for luxor_desk.py + its Cloudflare tunnel. Every test uses a
fake ProcessBackend; no real process is ever started, stopped, or
network-called, and PID/log files are always written under a temporary
directory, never the project's real .deploy_logs/launcher/. Launcher.py
itself never imports dispatcher.* or touches the real database -- see
test_launcher_never_touches_dispatcher_or_database below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "windows_launcher"))
import launcher  # noqa: E402


class FakeOS:
    """A tiny in-memory model of "the operating system" as far as
    launcher.py needs it: a dict of alive PID -> command line, captured
    terminate/popen/open_browser calls, and canned log file contents.
    """

    def __init__(self):
        self.next_pid = 1000
        self.alive: dict = {}
        self.terminated: list = []
        self.opened_urls: list = []
        self.logs: dict = {}
        self.popen_calls: list = []
        self.raise_on_popen_containing: str | None = None
        self.healthy = True

    def popen(self, args, cwd, out_log, err_log):
        cmdline = " ".join(str(a) for a in args)
        self.popen_calls.append(cmdline)
        if self.raise_on_popen_containing and self.raise_on_popen_containing in cmdline:
            raise FileNotFoundError(f"[fake] executable not found for: {args[0]!r}")
        pid = self.next_pid
        self.next_pid += 1
        self.alive[pid] = cmdline
        return pid

    def is_pid_alive(self, pid):
        return pid in self.alive

    def get_commandline(self, pid):
        return self.alive.get(pid)

    def terminate(self, pid):
        self.terminated.append(pid)
        self.alive.pop(pid, None)

    def urlopen_ok(self, url):
        assert url.startswith("http://localhost:"), f"launcher must only health-check localhost, got {url!r}"
        return self.healthy

    def open_browser(self, url):
        self.opened_urls.append(url)

    def read_log(self, path):
        return self.logs.get(path, "")

    def backend(self) -> launcher.ProcessBackend:
        return launcher.ProcessBackend(
            popen=self.popen, is_pid_alive=self.is_pid_alive, get_commandline=self.get_commandline,
            terminate=self.terminate, urlopen_ok=self.urlopen_ok, open_browser=self.open_browser,
            read_log=self.read_log,
        )


NO_SLEEP = lambda seconds: None  # noqa: E731 -- tests never really sleep


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    run_dir = tmp_path / "launcher_run"
    monkeypatch.setattr(launcher, "RUN_DIR", run_dir)
    monkeypatch.setattr(launcher, "STREAMLIT_PID_FILE", run_dir / "streamlit.pid.json")
    monkeypatch.setattr(launcher, "TUNNEL_PID_FILE", run_dir / "tunnel.pid.json")
    monkeypatch.setattr(launcher, "STREAMLIT_OUT_LOG", run_dir / "streamlit.out.log")
    monkeypatch.setattr(launcher, "STREAMLIT_ERR_LOG", run_dir / "streamlit.err.log")
    monkeypatch.setattr(launcher, "TUNNEL_OUT_LOG", run_dir / "tunnel.out.log")
    monkeypatch.setattr(launcher, "TUNNEL_ERR_LOG", run_dir / "tunnel.err.log")
    monkeypatch.setattr(launcher, "TUNNEL_URL_FILE", run_dir / "tunnel_url.txt")
    return run_dir


def _seed_tunnel_url(fake: FakeOS, url: str) -> None:
    fake.logs[launcher.TUNNEL_OUT_LOG] = f"Your quick Tunnel has been created! Visit it at: {url}\n"


# --------------------------------------------------------------- start ---
def test_start_when_nothing_running(isolated_paths):
    fake = FakeOS()
    _seed_tunnel_url(fake, "https://one-two-three.trycloudflare.com")
    result = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)

    assert result["streamlit_pid"] is not None
    assert result["tunnel_pid"] is not None
    assert result["healthy"] is True
    assert result["tunnel_url"] == "https://one-two-three.trycloudflare.com"
    assert fake.opened_urls == ["https://one-two-three.trycloudflare.com"]
    assert launcher.STREAMLIT_PID_FILE.exists()
    assert launcher.TUNNEL_PID_FILE.exists()
    assert launcher.TUNNEL_URL_FILE.read_text(encoding="utf-8") == "https://one-two-three.trycloudflare.com"

    # Exactly one of each process was actually started.
    streamlit_calls = [c for c in fake.popen_calls if "streamlit" in c]
    tunnel_calls = [c for c in fake.popen_calls if "cloudflared" in c]
    assert len(streamlit_calls) == 1
    assert len(tunnel_calls) == 1


def test_repeated_start_does_not_create_duplicates(isolated_paths):
    fake = FakeOS()
    _seed_tunnel_url(fake, "https://repeat-test.trycloudflare.com")
    first = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)
    second = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)

    assert second["streamlit_pid"] == first["streamlit_pid"]
    assert second["tunnel_pid"] == first["tunnel_pid"]
    assert any("already running" in m for m in second["messages"])
    streamlit_calls = [c for c in fake.popen_calls if "streamlit" in c]
    tunnel_calls = [c for c in fake.popen_calls if "cloudflared" in c]
    assert len(streamlit_calls) == 1  # not started a second time
    assert len(tunnel_calls) == 1


def test_streamlit_startup_failure_reported_not_crashed(isolated_paths):
    fake = FakeOS()
    fake.healthy = False  # the process starts but never responds
    _seed_tunnel_url(fake, "https://unhealthy-test.trycloudflare.com")
    result = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)

    assert result["healthy"] is False
    assert any("did not respond" in m for m in result["messages"])
    # Startup still continues (a stuck Streamlit shouldn't block reporting)
    assert result["streamlit_pid"] is not None


def test_missing_python_environment_reported_cleanly(isolated_paths):
    fake = FakeOS()
    fake.raise_on_popen_containing = "streamlit"
    result = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)

    assert result["streamlit_pid"] is None
    assert result["tunnel_pid"] is None
    assert any("Could not start the dispatcher" in m for m in result["messages"])
    assert not launcher.STREAMLIT_PID_FILE.exists()
    # Never attempted to start the tunnel after the dispatcher itself failed.
    assert not any("cloudflared" in c for c in fake.popen_calls)


def test_missing_cloudflared_executable_reported_cleanly(isolated_paths):
    fake = FakeOS()
    fake.raise_on_popen_containing = "cloudflared"
    result = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)

    assert result["streamlit_pid"] is not None  # dispatcher itself still started
    assert result["tunnel_pid"] is None
    assert any("Could not start the tunnel" in m for m in result["messages"])
    assert not launcher.TUNNEL_PID_FILE.exists()


def test_tunnel_url_extraction_from_noisy_log(isolated_paths):
    fake = FakeOS()
    fake.logs[launcher.TUNNEL_OUT_LOG] = (
        "2026-07-31T00:00:00Z INF Requesting new quick Tunnel...\n"
        "2026-07-31T00:00:01Z INF +--------------------------------------------------------------------------------------------+\n"
        "2026-07-31T00:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable): |\n"
        "2026-07-31T00:00:01Z INF |  https://month-portsmouth-timely-relevance.trycloudflare.com                              |\n"
        "2026-07-31T00:00:01Z INF +--------------------------------------------------------------------------------------------+\n"
    )
    result = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)
    assert result["tunnel_url"] == "https://month-portsmouth-timely-relevance.trycloudflare.com"


def test_no_tunnel_url_yet_does_not_crash(isolated_paths):
    fake = FakeOS()  # no log content at all
    result = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)
    assert result["tunnel_url"] is None
    assert any("Could not detect the tunnel URL" in m for m in result["messages"])
    assert fake.opened_urls == []  # never opens a browser to nothing


# -------------------------------------------------------------- status ---
def test_status_reports_correct_states(isolated_paths):
    fake = FakeOS()
    before = launcher.status(backend=fake.backend())
    assert before == {
        "streamlit_running": False, "streamlit_pid": None,
        "tunnel_running": False, "tunnel_pid": None, "tunnel_url": None,
    }

    _seed_tunnel_url(fake, "https://status-test.trycloudflare.com")
    launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)
    after_start = launcher.status(backend=fake.backend())
    assert after_start["streamlit_running"] is True
    assert after_start["tunnel_running"] is True
    assert after_start["tunnel_url"] == "https://status-test.trycloudflare.com"

    launcher.stop(backend=fake.backend())
    after_stop = launcher.status(backend=fake.backend())
    assert after_stop["streamlit_running"] is False
    assert after_stop["tunnel_running"] is False


# ---------------------------------------------------------------- stop ---
def test_stop_terminates_only_project_owned_processes(isolated_paths):
    fake = FakeOS()
    # A stale PID file claims PID 4242, but that PID now belongs to some
    # unrelated process (simulating PID reuse after the real dispatcher
    # already exited) -- stop() must never terminate it.
    fake.alive[4242] = "C:\\Windows\\notepad.exe"
    launcher._write_pid_file(launcher.STREAMLIT_PID_FILE, {"pid": 4242, "signature": launcher.STREAMLIT_SIGNATURE})

    result = launcher.stop(backend=fake.backend())
    assert 4242 not in fake.terminated
    assert result["stopped"]["streamlit"] is False
    assert not launcher.STREAMLIT_PID_FILE.exists()  # stale file still cleaned up


def test_stop_terminates_genuinely_tracked_processes(isolated_paths):
    fake = FakeOS()
    _seed_tunnel_url(fake, "https://stop-test.trycloudflare.com")
    started = launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)

    result = launcher.stop(backend=fake.backend())
    assert started["streamlit_pid"] in fake.terminated
    assert started["tunnel_pid"] in fake.terminated
    assert result["stopped"]["streamlit"] is True
    assert result["stopped"]["tunnel"] is True
    assert not launcher.STREAMLIT_PID_FILE.exists()
    assert not launcher.TUNNEL_PID_FILE.exists()


def test_stop_when_nothing_running_is_a_no_op(isolated_paths):
    fake = FakeOS()
    result = launcher.stop(backend=fake.backend())
    assert fake.terminated == []
    assert result["stopped"] == {"streamlit": False, "tunnel": False}


# ----------------------------------------------------------------- open --
def test_open_uses_known_tunnel_url(isolated_paths):
    fake = FakeOS()
    _seed_tunnel_url(fake, "https://open-test.trycloudflare.com")
    launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)
    fake.opened_urls.clear()

    result = launcher.open_dispatcher(backend=fake.backend())
    assert result["opened"] == "https://open-test.trycloudflare.com"
    assert fake.opened_urls == ["https://open-test.trycloudflare.com"]


def test_open_falls_back_to_local_address_without_tunnel_url(isolated_paths):
    fake = FakeOS()  # no tunnel log content -- URL never detected
    launcher.start(backend=fake.backend(), sleep_fn=NO_SLEEP)
    fake.opened_urls.clear()

    result = launcher.open_dispatcher(backend=fake.backend())
    assert result["opened"] == f"http://localhost:{launcher.DEFAULT_PORT}"


def test_open_when_nothing_running_does_not_open_anything(isolated_paths):
    fake = FakeOS()
    result = launcher.open_dispatcher(backend=fake.backend())
    assert result["opened"] is None
    assert fake.opened_urls == []
    assert "not running" in result["messages"][0]


# ---------------------------------------------------- secrets / scope ---
def test_launcher_never_touches_dispatcher_or_database():
    source = Path("tools/windows_launcher/launcher.py").read_text(encoding="utf-8")
    assert "import dispatcher" not in source
    assert "from dispatcher" not in source
    assert "storage.connect" not in source
    assert "load_dotenv" not in source  # never reads .env itself -- only prose mentions it





def test_launcher_never_references_secret_values():
    for path in Path("tools/windows_launcher").glob("*"):
        if path.is_dir():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in ("QUO_API_KEY=", "LUXOR_DEMO_PASSWORD=", "1212" + "1211"):
            assert needle not in text, f"{needle!r} literal found in {path}"


def test_launcher_makes_no_real_network_call_outside_the_backend():
    """The only network-capable real function is _real_urlopen_ok, and it
    only ever targets localhost (enforced structurally, not just by
    convention) -- grep-confirmed no other network call exists."""
    source = Path("tools/windows_launcher/launcher.py").read_text(encoding="utf-8")
    assert source.count("urllib.request.urlopen") == 1
    assert "requests." not in source
    assert "quo.com" not in source.lower()


def test_bat_files_never_hardcode_a_password_or_key():
    for path in Path("tools/windows_launcher").glob("*.bat"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "QUO_API_KEY" not in text
        assert "password" not in text.lower()
