"""Focused tests proving the sanitized demo workflow (scripts/start_demo.ps1,
scripts/seed_command_center_demo_db.py, LUXOR_DEMO_MODE) can never load or
seed the real production database, and that demo mode is surfaced to the
frontend so a dispatcher can never mistake it for the real CRM.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
import streamlit as st

import operator_api as api
from dispatcher import storage

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_demo = _load_module("seed_command_center_demo_db", "scripts/seed_command_center_demo_db.py")


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


# ------------------------------------------------- seed script safety guard
# seed() now delegates to the shared dispatcher.db_safety.resolve_db_path()
# guard (see tests/test_db_safety.py for that function's own exhaustive
# coverage) instead of a private ValueError comparison -- these two tests
# confirm the seed script is actually wired to it, not just that the
# guard function works in isolation.
def test_seed_refuses_to_write_to_the_real_database_path(monkeypatch):
    with pytest.raises(api.UnsafeDatabasePathError, match="production database"):
        seed_demo.seed(storage.DB_PATH)


def test_seed_refuses_the_real_database_path_even_via_a_relative_or_odd_spelling(tmp_path, monkeypatch):
    # storage.DB_PATH resolved through an extra ".." round-trip should
    # still be caught by the guard's use of .resolve() on both sides.
    odd_path = storage.DB_PATH.parent / ".." / storage.DB_PATH.parent.name / storage.DB_PATH.name
    with pytest.raises(api.UnsafeDatabasePathError, match="production database"):
        seed_demo.seed(odd_path)


def test_seed_succeeds_against_a_genuine_temp_path_and_includes_a_cancelled_job(tmp_path, monkeypatch):
    db_path = tmp_path / "demo.db"
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))
    st.cache_resource.clear()
    st.cache_data.clear()
    seed_demo.seed(db_path)

    conn = storage.connect(db_path)
    statuses = {row[0] for row in conn.execute("SELECT status FROM jobs").fetchall()}
    conn.close()
    assert "Cancelled" in statuses, f"expected a Cancelled demo job, got statuses: {statuses}"


def test_seed_produces_multiple_sources_multiple_technicians_and_a_closed_job(tmp_path, monkeypatch):
    db_path = tmp_path / "demo.db"
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))
    st.cache_resource.clear()
    st.cache_data.clear()
    seed_demo.seed(db_path)

    conn = storage.connect(db_path)
    sources = {row[0] for row in conn.execute("SELECT DISTINCT source FROM jobs").fetchall()}
    tech_count = conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    statuses = {row[0] for row in conn.execute("SELECT status FROM jobs").fetchall()}
    conn.close()
    assert len(sources) >= 2
    assert tech_count >= 3
    assert "Closed" in statuses
    assert "In Progress" in statuses


# -------------------------------------------------------------- demo_mode --
def test_route_me_reports_demo_mode_false_by_default(api_env, monkeypatch):
    monkeypatch.delenv("LUXOR_DEMO_MODE", raising=False)
    result = api.route_me(None, {}, {})
    assert result["demo_mode"] is False


def test_route_me_reports_demo_mode_true_when_env_var_set(api_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_MODE", "true")
    result = api.route_me(None, {}, {})
    assert result["demo_mode"] is True


def test_route_me_demo_mode_is_case_insensitive(api_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_MODE", "True")
    assert api.route_me(None, {}, {})["demo_mode"] is True


# ---------------------------------------------- unauthenticated demo status
def test_route_demo_status_reports_true_without_authentication(api_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_MODE", "true")
    result = api.route_demo_status(None, {}, {})
    assert result == {"demo_mode": True}


def test_route_demo_status_reports_false_by_default(api_env, monkeypatch):
    monkeypatch.delenv("LUXOR_DEMO_MODE", raising=False)
    result = api.route_demo_status(None, {}, {})
    assert result == {"demo_mode": False}


def test_demo_status_route_is_registered_as_public_get():
    matches = [
        (method, pattern.pattern, opts)
        for method, pattern, fn, opts in api._COMPILED_ROUTES
        if fn is api.route_demo_status
    ]
    assert len(matches) == 1
    method, pattern, opts = matches[0]
    assert method == "GET"
    assert opts.get("public") is True


def test_demo_status_route_exposes_only_a_boolean(api_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_MODE", "true")
    result = api.route_demo_status(None, {}, {})
    assert set(result.keys()) == {"demo_mode"}
    assert isinstance(result["demo_mode"], bool)


def test_route_me_demo_mode_false_for_any_other_value(api_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_MODE", "1")  # only the literal "true" counts
    assert api.route_me(None, {}, {})["demo_mode"] is False


# ---------------------------------------------------- start_demo.ps1 static
def test_start_demo_script_never_assigns_the_real_database_path_to_operator_api_db_path():
    text = (REPO_ROOT / "scripts" / "start_demo.ps1").read_text(encoding="utf-8")
    # The only mention of data\dispatcher.db in this script must be the
    # explicit "must never equal this" guard variable, never an
    # assignment target for OPERATOR_API_DB_PATH.
    assert "$env:OPERATOR_API_DB_PATH = $demoDbPath" in text
    assert "OPERATOR_API_DB_PATH = '$realDbPath'" not in text
    assert "demoDbPath" in text
    assert ".runtime\\demo" in text


def test_start_demo_script_sets_demo_mode_and_disables_bridge_adapter_and_quo_key():
    text = (REPO_ROOT / "scripts" / "start_demo.ps1").read_text(encoding="utf-8")
    assert "LUXOR_DEMO_MODE" in text
    assert "--no-bridge-adapter" in text
    assert "QUO_API_KEY" in text


def test_start_demo_script_never_invokes_the_whatsapp_bridge():
    # The script's own comments legitimately mention whatsapp_shadow_bridge
    # by name (explaining that it is NOT started) -- what must never appear
    # is an actual invocation of it (cd into it, or run its start script).
    text = (REPO_ROOT / "scripts" / "start_demo.ps1").read_text(encoding="utf-8")
    assert "start_normal.ps1" not in text
    assert "start_pairing.ps1" not in text
    assert "cd '$repoRoot\\whatsapp_shadow_bridge'" not in text
    assert "cd whatsapp_shadow_bridge" not in text.lower()


def test_stop_demo_script_never_touches_the_crm_port_or_real_database():
    # stop_demo.ps1's own header comment legitimately mentions "port 8512"
    # in prose (explaining what it does NOT touch) -- what must never
    # appear is an actual reference to that port number as a value, or to
    # the real database file.
    text = (REPO_ROOT / "scripts" / "stop_demo.ps1").read_text(encoding="utf-8")
    assert "-LocalPort 8512" not in text
    assert "dispatcher.db" not in text
    assert "$demoPort = 8517" in text
