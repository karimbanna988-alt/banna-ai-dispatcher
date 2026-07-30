"""Focused UI tests for the Commercial Update section added to the
Active Job workspace (luxor_desk_shared.render_active_job_workspace)
and the new Shadow Review page (luxor_desk_pages/shadow_review.py).
Every job seeded here goes through the real NEW-job pipeline -- never a
fabricated runtime record."""

from __future__ import annotations

import pathlib

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def agn_message(ticket="FAKE1", area="Charlotte, NC"):
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Fake St, {area}\nDesc: Car Lockout\n"


def commit_job(db_path, vehicle_rules, raw: str) -> int:
    conn = storage.connect(db_path)
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    conn.close()
    return job_id


def app_for(page: str, db_path) -> AppTest:
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file(page, default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    return at


# ------------------------------------------------------------- Navigation -
def test_shadow_review_page_registered_in_navigation():
    source = pathlib.Path("luxor_desk.py").read_text(encoding="utf-8")
    assert 'luxor_desk_pages/shadow_review.py' in source
    assert 'title="Shadow Review"' in source


# -------------------------------------------------- Zero writes on render
def test_rendering_active_jobs_page_never_writes_shadow_tables(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("SHDW1"))
    conn = storage.connect(desk_env)
    before_episodes = conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0]
    before_events = conn.execute("SELECT COUNT(*) FROM shadow_events").fetchone()[0]
    conn.close()

    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    after_episodes = conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0]
    after_events = conn.execute("SELECT COUNT(*) FROM shadow_events").fetchone()[0]
    conn.close()
    assert after_episodes == before_episodes == 0
    assert after_events == before_events == 0


def test_rendering_shadow_review_page_never_writes(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("SHDW2"))
    conn = storage.connect(desk_env)
    service_conn = storage.connect(desk_env)
    from dispatcher.application import DispatcherService
    DispatcherService(service_conn).record_commercial_update(job_id, "quoted 125")
    service_conn.close()
    episode_count_before = conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0]
    event_count_before = conn.execute("SELECT COUNT(*) FROM shadow_events").fetchone()[0]
    conn.close()

    at = app_for("luxor_desk_pages/shadow_review.py", desk_env)
    at.run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    episode_count_after = conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0]
    event_count_after = conn.execute("SELECT COUNT(*) FROM shadow_events").fetchone()[0]
    conn.close()
    assert episode_count_after == episode_count_before
    assert event_count_after == event_count_before


def test_shadow_review_empty_state(desk_env):
    at = app_for("luxor_desk_pages/shadow_review.py", desk_env)
    assert len(at.exception) == 0
    assert any("No shadow episodes recorded" in i.value for i in at.info)


# ------------------------------------------------- Commercial update via UI
def test_commercial_update_section_present_on_active_job(desk_env, vehicle_rules):
    commit_job(desk_env, vehicle_rules, agn_message("SHDW3"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    assert len(at.exception) == 0
    expander_labels = [e.label for e in at.expander]
    assert any("COMMERCIAL UPDATE" in label for label in expander_labels)


def test_save_commercial_update_via_ui_creates_episode(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("SHDW4"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.text_area(key=f"commercial_raw_{job_id}").set_value("quoted 125").run()
    at.button(key=f"commercial_save_{job_id}").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    conn.close()
    assert episode is not None
    assert episode["commercial_update"]["quote_amount"] == "125"


def test_commercial_update_never_changes_job_status_via_ui(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("SHDW5"))
    conn = storage.connect(desk_env)
    before_status = storage.get_job_by_id(conn, job_id)["status"]
    conn.close()

    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.text_area(key=f"commercial_raw_{job_id}").set_value("quoted 125, customer accepted").run()
    at.button(key=f"commercial_save_{job_id}").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    after_status = storage.get_job_by_id(conn, job_id)["status"]
    conn.close()
    assert before_status == after_status


# ---------------------------------------------------------- Shadow Review UI
def test_shadow_review_lists_recorded_episode(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("SHDW6"))
    conn = storage.connect(desk_env)
    from dispatcher.application import DispatcherService
    DispatcherService(conn).record_commercial_update(job_id, "quoted 125")
    conn.close()

    at = app_for("luxor_desk_pages/shadow_review.py", desk_env)
    assert len(at.exception) == 0
    expander_labels = [e.label for e in at.expander]
    assert any("AGN" in label for label in expander_labels)


def test_shadow_review_mark_verified_via_ui(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("SHDW7"))
    conn = storage.connect(desk_env)
    from dispatcher.application import DispatcherService
    DispatcherService(conn).record_commercial_update(job_id, "quoted 125")
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    conn.close()

    at = app_for("luxor_desk_pages/shadow_review.py", desk_env)
    at.selectbox(key=f"shadow_review_status_{episode['episode_id']}").set_value("VERIFIED").run()
    at.button(key=f"shadow_review_save_{episode['episode_id']}").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    updated = storage.get_shadow_episode(conn, episode["episode_id"])
    conn.close()
    assert updated["review_status"] == "VERIFIED"
    assert updated["eligible_for_export"] is True
