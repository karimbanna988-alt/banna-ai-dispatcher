"""An outage must never look like a quiet shift.

The owner has twice read a stopped service as "no jobs today", because
an empty board is exactly what both produce. These tests pin the one
rule that prevents it: anything other than confirmed-healthy is
reported as a fault, and "we do not know" is never rendered as fine.
"""

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dispatcher import service_health  # noqa: E402

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def _write(tmp_path, *, minutes_ago=1, api=True, bridge=True, quo=True):
    path = tmp_path / "service_status.json"
    path.write_text(json.dumps({
        "checked_at": (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": api, "bridge": bridge, "quo": quo,
    }), encoding="utf-8")
    return path


def test_all_services_up_is_healthy(tmp_path):
    h = service_health.read_service_health(NOW, _write(tmp_path))
    assert h["healthy"] is True
    assert h["down"] == []
    assert h["reason"] is None


def test_a_stopped_bridge_is_reported_by_name(tmp_path):
    h = service_health.read_service_health(NOW, _write(tmp_path, bridge=False))
    assert h["healthy"] is False
    assert h["down"] == ["WhatsApp capture"]
    assert "not running" in h["reason"]


def test_several_stopped_services_are_all_named(tmp_path):
    h = service_health.read_service_health(NOW, _write(tmp_path, bridge=False, quo=False))
    assert h["down"] == ["WhatsApp capture", "Quo polling"]


def test_a_stale_report_is_a_fault_not_a_pass(tmp_path):
    """If the supervisor died, its last report is not evidence of health."""
    h = service_health.read_service_health(NOW, _write(tmp_path, minutes_ago=40))
    assert h["watchdog_alive"] is False
    assert h["healthy"] is False
    assert "has not reported" in h["reason"]


def test_a_report_just_inside_the_window_is_still_trusted(tmp_path):
    h = service_health.read_service_health(NOW, _write(tmp_path, minutes_ago=11))
    assert h["watchdog_alive"] is True
    assert h["healthy"] is True


def test_a_missing_file_is_unknown_never_healthy(tmp_path):
    h = service_health.read_service_health(NOW, tmp_path / "nope.json")
    assert h["known"] is False
    assert h.get("healthy") is not True


def test_corrupt_json_never_raises_and_never_reads_as_healthy(tmp_path):
    bad = tmp_path / "service_status.json"
    bad.write_text("{not json", encoding="utf-8")
    h = service_health.read_service_health(NOW, bad)
    assert h["known"] is False
    assert h.get("healthy") is not True


def test_a_bom_written_by_powershell_is_still_readable(tmp_path):
    """PowerShell's utf8 writes a BOM; a reader that chokes on it would
    report a false outage every single cycle."""
    path = tmp_path / "service_status.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({
        "checked_at": (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": True, "bridge": True, "quo": True}).encode("utf-8"))
    h = service_health.read_service_health(NOW, path)
    assert h["healthy"] is True


def test_the_banner_is_hidden_only_when_healthy():
    """The UI contract: hidden is reserved for confirmed health."""
    js = (ROOT / "operator_ui_v2_connected" / "js" / "console.js").read_text(encoding="utf-8")
    assert "if (!health || health.healthy) { box.hidden = true; return; }" in js
    assert "renderServiceAlarm(r.service_health)" in js
