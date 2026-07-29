"""Proves the "Database upgrade required" guard actually blocks write
actions when the schema is behind, and that read-only pages keep
working regardless. Every database here is a temporary file built to a
deliberately pre-migration shape -- never the real database, and
storage.connect() is never allowed to auto-migrate it (matching
storage.connect()'s own contract, verified here from the UI's
perspective).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage
from test_schema_migration import _raw_current_minus_idempotency


@pytest.fixture()
def stale_desk_env(tmp_path, monkeypatch):
    db_path = tmp_path / "stale.db"
    _raw_current_minus_idempotency(db_path)
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def future_ts(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def app_for(page: str) -> AppTest:
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file(page, default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    return at


def test_stale_schema_never_gets_silently_migrated_by_page_render(stale_desk_env):
    conn = storage.connect(stale_desk_env)
    before = storage.check_schema_status(conn)
    conn.close()
    assert not before.up_to_date

    app_for("luxor_desk_pages/active_jobs.py")

    conn = storage.connect(stale_desk_env)
    after = storage.check_schema_status(conn)
    conn.close()
    assert not after.up_to_date  # still not migrated -- rendering never triggered it
    assert before.missing == after.missing


def test_active_job_workspace_shows_upgrade_message_and_hides_write_actions(stale_desk_env):
    conn = storage.connect(stale_desk_env)
    job_row = conn.execute("SELECT id FROM jobs WHERE ticket_number = 'HIST1'").fetchone()
    job_id = job_row[0]
    conn.close()

    at = AppTest.from_file("luxor_desk_pages/active_jobs.py", default_timeout=30)
    import luxor_desk_shared as shared_desk
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.session_state["desk_active_selected"] = job_id
    at.run()

    assert len(at.exception) == 0
    rendered_warnings = "\n".join(w.value for w in at.warning)
    assert "Database upgrade required" in rendered_warnings
    assert "manage_schema.py migrate" in rendered_warnings
    # No write-action buttons for this job -- the workspace returned
    # early after the guard fired.
    assert not any((b.key or "").startswith(f"active_save_update_{job_id}") for b in at.button)
    assert not any((b.key or "").startswith(f"active_pass_{job_id}") for b in at.button)


def test_technicians_page_unaffected_by_stale_schema(stale_desk_env):
    at = app_for("luxor_desk_pages/technicians.py")
    assert len(at.exception) == 0
    assert not any("Database upgrade required" in w.value for w in at.warning)


def test_activity_page_unaffected_by_stale_schema(stale_desk_env):
    at = app_for("luxor_desk_pages/activity.py")
    assert len(at.exception) == 0


def test_after_explicit_migration_write_actions_return(stale_desk_env):
    conn = storage.connect(stale_desk_env)
    storage.migrate_schema(conn)
    job_row = conn.execute("SELECT id FROM jobs WHERE ticket_number = 'HIST1'").fetchone()
    job_id = job_row[0]
    conn.close()

    at = AppTest.from_file("luxor_desk_pages/active_jobs.py", default_timeout=30)
    import luxor_desk_shared as shared_desk
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.session_state["desk_active_selected"] = job_id
    at.run()

    assert len(at.exception) == 0
    rendered_warnings = "\n".join(w.value for w in at.warning)
    assert "Database upgrade required" not in rendered_warnings
