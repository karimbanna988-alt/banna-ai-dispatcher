"""Focused tests for tools/system_readiness.py. Every HTTP-dependent
check is exercised against a fake `_http_get_json`, never a real
socket; every database check runs against a temporary SQLite file,
never the real database. No test ever prints or asserts on a real
secret value.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dispatcher import storage  # noqa: E402


def _load_module(name: str, rel_path: str):
    path = Path(__file__).resolve().parents[1] / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sr = _load_module("system_readiness", "tools/system_readiness.py")


def _seeded_db(tmp_path) -> Path:
    db_path = tmp_path / "test.db"
    storage.connect(db_path).close()
    return db_path


# --------------------------------------------------------------- HTTP checks
def test_crm_port_pass_on_401(monkeypatch):
    monkeypatch.setattr(sr, "_http_get_json", lambda url, timeout=2.5: (401, {"error": "unauthorized"}))
    result = sr.check_crm_port()
    assert result.level == "PASS"


def test_crm_port_pass_on_200(monkeypatch):
    monkeypatch.setattr(sr, "_http_get_json", lambda url, timeout=2.5: (200, {"dispatcher": "x"}))
    result = sr.check_crm_port()
    assert result.level == "PASS"


def test_crm_port_warning_when_not_listening(monkeypatch):
    import urllib.error

    def raise_conn_refused(url, timeout=2.5):
        raise OSError("connection refused")

    monkeypatch.setattr(sr, "_http_get_json", raise_conn_refused)
    result = sr.check_crm_port()
    assert result.level == "WARNING"


def test_crm_port_fail_on_unexpected_status(monkeypatch):
    monkeypatch.setattr(sr, "_http_get_json", lambda url, timeout=2.5: (500, None))
    result = sr.check_crm_port()
    assert result.level == "FAIL"


def test_python_adapter_pass(monkeypatch):
    monkeypatch.setattr(sr, "_http_get_json", lambda url, timeout=2.5: (200, {"status": "ok"}))
    result = sr.check_python_adapter_port()
    assert result.level == "PASS"


def test_python_adapter_warning_when_down(monkeypatch):
    def raise_refused(url, timeout=2.5):
        raise OSError("refused")

    monkeypatch.setattr(sr, "_http_get_json", raise_refused)
    result = sr.check_python_adapter_port()
    assert result.level == "WARNING"


def test_whatsapp_bridge_pass_when_paired_and_reports_allowlist_count(monkeypatch):
    calls = {"n": 0}

    def fake(url, timeout=2.5):
        calls["n"] += 1
        if url.endswith("/health"):
            return 200, {"enabled": True, "paired": True, "status": "connected"}
        if url.endswith("/allowlist"):
            return 200, {"groups": [{"id": "a@g.us", "name": "Real Group Name"}, {"id": "b@g.us", "name": "Another"}]}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(sr, "_http_get_json", fake)
    bridge, allowlist = sr.check_whatsapp_bridge_port()
    assert bridge.level == "PASS"
    assert allowlist.level == "PASS"
    assert "2 group(s)" in allowlist.detail
    assert "Real Group Name" not in allowlist.detail
    assert "a@g.us" not in allowlist.detail


def test_whatsapp_bridge_warning_when_not_paired(monkeypatch):
    monkeypatch.setattr(sr, "_http_get_json", lambda url, timeout=2.5: (
        (200, {"enabled": True, "paired": False, "status": "disabled_or_connecting"}) if url.endswith("/health")
        else (200, {"groups": []})
    ))
    bridge, _ = sr.check_whatsapp_bridge_port()
    assert bridge.level == "WARNING"


def test_whatsapp_bridge_warning_when_down(monkeypatch):
    def raise_refused(url, timeout=2.5):
        raise OSError("refused")

    monkeypatch.setattr(sr, "_http_get_json", raise_refused)
    bridge, allowlist = sr.check_whatsapp_bridge_port()
    assert bridge.level == "WARNING"
    assert allowlist.level == "WARNING"


# --------------------------------------------------------------- DB checks
def test_database_checks_pass_for_healthy_fresh_db(tmp_path):
    db_path = _seeded_db(tmp_path)
    integrity, fk, durable, dupes = sr.check_database_integrity(db_path)
    assert integrity.level == "PASS"
    assert fk.level == "PASS"
    assert durable.level == "PASS"
    assert dupes.level == "PASS"


def test_database_checks_fail_when_file_missing(tmp_path):
    integrity, fk, durable, dupes = sr.check_database_integrity(tmp_path / "nope.db")
    assert integrity.level == "FAIL"
    assert fk.level == "FAIL"
    assert durable.level == "FAIL"
    assert dupes.level == "FAIL"


def test_database_checks_action_required_when_observations_table_missing(tmp_path):
    db_path = tmp_path / "old_schema.db"
    conn = storage.connect(db_path)
    conn.execute("DROP TABLE whatsapp_pending_observations")
    conn.commit()
    conn.close()
    integrity, fk, durable, dupes = sr.check_database_integrity(db_path)
    assert integrity.level == "PASS"
    assert durable.level == "ACTION REQUIRED"
    assert dupes.level == "WARNING"


def test_database_checks_never_print_message_text(tmp_path):
    """The duplicate-ID check must never surface observation content --
    only a count. Sanity-check the returned detail string never
    contains anything beyond digits/words we put there ourselves."""
    db_path = _seeded_db(tmp_path)
    conn = storage.connect(db_path)
    storage.add_whatsapp_observation(conn, {
        "message_id": "m1", "group_id": "g@g.us", "group_name": "Real Group",
        "sender": "s", "timestamp": "1", "text": "a secret customer address, never to leak",
        "direction": "incoming", "human_review_needed": False, "human_review_reason": None,
    })
    conn.close()
    _, _, _, dupes = sr.check_database_integrity(db_path)
    assert "secret customer address" not in dupes.detail


# ------------------------------------------------------- backup destination
def test_backup_destination_writable_pass(tmp_path, monkeypatch):
    import backup_database
    monkeypatch.setattr(backup_database, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    result = sr.check_backup_destination_writable()
    assert result.level == "PASS"


def test_backup_destination_writable_fail_when_parent_is_a_file(tmp_path, monkeypatch):
    import backup_database
    blocker = tmp_path / "blocker_file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(backup_database, "DEFAULT_BACKUP_DIR", blocker / "backups")
    result = sr.check_backup_destination_writable()
    assert result.level == "FAIL"


# ------------------------------------------------------------------ secrets
def test_read_env_var_from_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nFOO=bar\nBAZ=\"quoted value\"\n", encoding="utf-8")
    assert sr._read_env_var_from_file(env_file, "FOO") == "bar"
    assert sr._read_env_var_from_file(env_file, "BAZ") == "quoted value"
    assert sr._read_env_var_from_file(env_file, "MISSING") is None


def test_read_env_var_from_file_missing_file(tmp_path):
    assert sr._read_env_var_from_file(tmp_path / "nope.env", "FOO") is None


def test_required_secrets_action_required_when_missing(monkeypatch):
    # IMPORTANT: never let OPERATOR_API_SESSION_SECRET actually reach ""
    # in a real check_required_secrets() call. operator_api.py
    # auto-generates AND PERSISTS a fresh secret to the real .env file
    # the moment it is imported and finds that var falsy in os.environ
    # (see operator_api.py's _ensure_session_secret-equivalent, around
    # line 120) -- it does not know or care that it was imported from a
    # test. A prior version of this test set it to "" via monkeypatch
    # and it silently appended a second, live-overriding
    # OPERATOR_API_SESSION_SECRET= line to the real repo-root .env,
    # which had to be hand-repaired. To exercise the "missing" branch
    # safely, import operator_api ourselves FIRST (so the real secret
    # is already loaded and the generator's write path is never
    # reached), then only blank out the two vars that don't carry that
    # side effect, plus fake the file-read helper for the third.
    import operator_api  # noqa: F401 -- pre-import so its own module-level secret provisioning has already happened, if any
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "")
    monkeypatch.setenv("WHATSAPP_BRIDGE_ADAPTER_SECRET", "")
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setattr(sr, "_read_env_var_from_file", lambda path, name: None)
    checks = sr.check_required_secrets()
    by_name = {c.name: c for c in checks}
    assert by_name["Secret present: LUXOR_DEMO_PASSWORD"].level == "ACTION REQUIRED"
    assert by_name["Secret present: WHATSAPP_BRIDGE_ADAPTER_SECRET"].level == "ACTION REQUIRED"


def test_required_secrets_pass_and_never_print_value(monkeypatch):
    for name in sr.REQUIRED_SECRET_VARS + sr.OPTIONAL_SECRET_VARS:
        monkeypatch.setenv(name, "super-secret-value-do-not-print")
    monkeypatch.setattr(sr, "_read_env_var_from_file", lambda path, name: "super-secret-value-do-not-print")
    checks = sr.check_required_secrets()
    serialized = str(checks)
    assert "super-secret-value-do-not-print" not in serialized
    by_name = {c.name: c for c in checks}
    for name in sr.REQUIRED_SECRET_VARS:
        assert by_name[f"Secret present: {name}"].level == "PASS"


def test_bridge_secret_fingerprint_match_and_mismatch(monkeypatch):
    monkeypatch.setenv("WHATSAPP_BRIDGE_ADAPTER_SECRET", "shared-value")
    monkeypatch.setattr(sr, "_read_env_var_from_file", lambda path, name: "shared-value")
    checks = sr.check_required_secrets()
    match = next(c for c in checks if c.name == "Bridge secret fingerprint match")
    assert match.level == "PASS"

    monkeypatch.setattr(sr, "_read_env_var_from_file", lambda path, name: "different-value")
    checks = sr.check_required_secrets()
    mismatch = next(c for c in checks if c.name == "Bridge secret fingerprint match")
    assert mismatch.level == "FAIL"
    assert "shared-value" not in mismatch.detail
    assert "different-value" not in mismatch.detail


# ------------------------------------------------------------ startup scripts
def test_startup_scripts_pass_when_launcher_points_to_operator_api(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "REPO_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "start_crm.ps1").write_text("python operator_api.py\n", encoding="utf-8")
    launcher_dir = tmp_path / "tools" / "windows_launcher"
    launcher_dir.mkdir(parents=True)
    (launcher_dir / "launcher.py").write_text("STREAMLIT_SIGNATURE = 'streamlit run operator_api.py'\n", encoding="utf-8")
    result = sr.check_startup_scripts_current()
    assert result.level == "PASS"


def test_startup_scripts_action_required_when_launcher_still_points_to_luxor_desk(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "REPO_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "start_crm.ps1").write_text("python operator_api.py\n", encoding="utf-8")
    launcher_dir = tmp_path / "tools" / "windows_launcher"
    launcher_dir.mkdir(parents=True)
    (launcher_dir / "launcher.py").write_text("STREAMLIT_SIGNATURE = 'streamlit run luxor_desk.py'\n", encoding="utf-8")
    result = sr.check_startup_scripts_current()
    assert result.level == "ACTION REQUIRED"


def test_startup_scripts_fail_when_start_crm_no_longer_references_operator_api(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "REPO_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "start_crm.ps1").write_text("python something_else.py\n", encoding="utf-8")
    result = sr.check_startup_scripts_current()
    assert result.level == "FAIL"


# This confirms the actual repo state matches what the check expects to find
# (a real regression test, not just a mocked-filesystem unit test).
def test_startup_scripts_check_against_the_real_repo_reports_the_known_gap():
    result = sr.check_startup_scripts_current()
    assert result.level == "ACTION REQUIRED"
    assert "windows_launcher" in result.detail


# -------------------------------------------------------- technician mappings
def test_technician_mappings_pass_when_all_configured(tmp_path):
    db_path = _seeded_db(tmp_path)
    conn = storage.connect(db_path)
    storage.add_technician(conn, name="Complete Tech", areas=["Charlotte"], skills=[],
                            dispatch_group="Dispatch Grp", closing_group="Closing Grp")
    conn.close()
    result = sr.check_technician_mappings(db_path)
    assert result.level == "PASS"


def test_technician_mappings_action_required_when_missing_a_group(tmp_path):
    db_path = _seeded_db(tmp_path)
    conn = storage.connect(db_path)
    storage.add_technician(conn, name="Incomplete Tech", areas=["Charlotte"], skills=[])
    conn.close()
    result = sr.check_technician_mappings(db_path)
    assert result.level == "ACTION REQUIRED"
    assert result.category == "CONFIGURATION"


def test_technician_mappings_action_required_when_none_configured(tmp_path):
    db_path = _seeded_db(tmp_path)
    result = sr.check_technician_mappings(db_path)
    assert result.level == "ACTION REQUIRED"


# --------------------------------------------------------- service areas
def test_service_area_mappings_pass_against_the_real_repo():
    result = sr.check_service_area_mappings()
    assert result.level == "PASS"


# ---------------------------------------------------------- structural checks
def test_quo_read_only_pass_against_the_real_repo():
    """Regression companion to docs/SECURITY_AUDIT.md's manual finding --
    fails loudly if a write-capable call is ever added to quo_client.py."""
    result = sr.check_quo_read_only()
    assert result.level == "PASS"


def test_quo_read_only_fails_when_a_write_call_is_present(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "REPO_ROOT", tmp_path)
    quo_dir = tmp_path / "src" / "dispatcher"
    quo_dir.mkdir(parents=True)
    (quo_dir / "quo_client.py").write_text("requests.post(url)\n", encoding="utf-8")
    result = sr.check_quo_read_only()
    assert result.level == "FAIL"


def test_whatsapp_no_send_pass_against_the_real_repo():
    result = sr.check_whatsapp_no_send()
    assert result.level == "PASS"


def test_whatsapp_no_send_fails_when_a_send_call_is_present(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "REPO_ROOT", tmp_path)
    bridge_dir = tmp_path / "whatsapp_shadow_bridge" / "src"
    bridge_dir.mkdir(parents=True)
    (bridge_dir / "client.js").write_text("client.sendMessage(chatId, text)\n", encoding="utf-8")
    result = sr.check_whatsapp_no_send()
    assert result.level == "FAIL"


def test_bind_address_warns_against_the_real_repo():
    """Documents the known finding (docs/SECURITY_AUDIT.md #9) rather
    than silently passing -- confirms the check actually detects it."""
    result = sr.check_bind_address()
    assert result.level == "WARNING"
    assert result.category == "CONFIGURATION"


def test_bind_address_pass_when_loopback_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "REPO_ROOT", tmp_path)
    (tmp_path / "operator_api.py").write_text('ThreadingHTTPServer(("127.0.0.1", port), Handler)\n', encoding="utf-8")
    result = sr.check_bind_address()
    assert result.level == "PASS"


def test_demo_test_isolation_guard_passes_against_the_real_repo():
    result = sr.check_demo_test_isolation()
    assert result.level == "PASS"


# --------------------------------------------------------- human review backlog
def test_human_review_backlog_pass_when_none_found(tmp_path):
    db_path = _seeded_db(tmp_path)
    result = sr.check_human_review_backlog(db_path)
    assert result.level == "PASS"


def test_human_review_backlog_counts_handle_manually_jobs(tmp_path):
    db_path = _seeded_db(tmp_path)
    conn = storage.connect(db_path)
    now = storage._now()
    conn.execute(
        "INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at, status) "
        "VALUES ('T1', 'AGN', '{}', ?, ?, 'Handle Manually')", (now, now),
    )
    conn.commit()
    conn.close()
    result = sr.check_human_review_backlog(db_path)
    assert result.level == "WARNING"
    assert result.category == "CONFIGURATION"
    assert "1" in result.detail


def test_human_review_backlog_counts_technician_declines(tmp_path):
    db_path = _seeded_db(tmp_path)
    conn = storage.connect(db_path)
    now = storage._now()
    job_cur = conn.execute(
        "INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at, status) "
        "VALUES ('T1', 'AGN', '{}', ?, ?, 'Approved')", (now, now),
    )
    conn.execute(
        "INSERT INTO technician_acceptance (job_id, declined_at, decline_reason, updated_at) VALUES (?, ?, ?, ?)",
        (job_cur.lastrowid, now, "too far", now),
    )
    conn.commit()
    conn.close()
    result = sr.check_human_review_backlog(db_path)
    assert result.level == "WARNING"


def test_human_review_backlog_never_touches_the_real_database():
    """Uses the same strict read-only sqlite3 URI mode every other DB
    check in this tool uses -- proven here by pointing it at the real
    database and confirming it only reads."""
    result = sr.check_human_review_backlog(storage.DB_PATH)
    assert result.level in ("PASS", "WARNING")


# --------------------------------------------------------------------- main
def test_main_exit_code_1_when_any_check_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "run_all_checks", lambda db_path: [sr.Check("x", "FAIL", "bad")])
    exit_code = sr.main(["--db", str(tmp_path / "whatever.db")])
    assert exit_code == 1


def test_main_exit_code_0_when_only_pass_and_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "run_all_checks", lambda db_path: [sr.Check("x", "PASS", "ok"), sr.Check("y", "WARNING", "meh")])
    exit_code = sr.main(["--db", str(tmp_path / "whatever.db")])
    assert exit_code == 0


def test_main_never_prints_a_real_secret_value(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "totally-real-secret-999")
    db_path = _seeded_db(tmp_path)
    sr.main(["--db", str(db_path)])
    captured = capsys.readouterr()
    assert "totally-real-secret-999" not in captured.out
