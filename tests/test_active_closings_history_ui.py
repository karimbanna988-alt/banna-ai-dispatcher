"""Focused UI tests for the real Active Jobs / Closings / Completed-
History pages (luxor_desk_pages/active_jobs.py, closings.py, history.py)
and their navigation wiring in luxor_desk.py. Complements
tests/test_job_operations.py (storage-level) and
tests/test_luxor_desk_real.py (New Jobs workflow, architectural
defects). Every job seeded here goes through the real NEW-job pipeline
(extract_job/classify_incoming/save_audit_entry) before any of the new
operational actions are exercised against it -- never a fabricated
runtime record.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, "src")

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules

DESK_FILES = [
    "luxor_desk.py", "luxor_desk_shared.py", "luxor_desk_read_model.py",
    "luxor_desk_pages/dispatch.py", "luxor_desk_pages/active_jobs.py",
    "luxor_desk_pages/closings.py", "luxor_desk_pages/history.py",
    "luxor_desk_pages/technicians.py", "luxor_desk_pages/activity.py",
]


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
    """Real NEW-job commit via the existing, unmodified pipeline -- the
    only way any job used by these tests comes into existence."""
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
def test_completed_history_page_registered_in_navigation():
    source = pathlib.Path("luxor_desk.py").read_text(encoding="utf-8")
    assert 'luxor_desk_pages/history.py' in source
    assert 'title="Completed / History"' in source


def test_navigation_order_matches_canonical_workflow():
    source = pathlib.Path("luxor_desk.py").read_text(encoding="utf-8")
    nav_line = next(line for line in source.splitlines() if "st.navigation([" in line)
    order = [name.strip() for name in nav_line.split("[", 1)[1].split("]")[0].split(",")]
    assert order == ["dispatch", "active_jobs", "closings", "history", "technicians", "activity", "shadow_review"]


# --------------------------------------------------------------- Empty states
def test_active_jobs_page_empty_state_no_fabricated_data(desk_env):
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    assert len(at.exception) == 0
    assert any("No active jobs" in i.value for i in at.info)


def test_history_page_empty_state(desk_env):
    at = app_for("luxor_desk_pages/history.py", desk_env)
    assert len(at.exception) == 0
    assert any("No completed jobs yet" in i.value for i in at.info)


# --------------------------------------------------------- Active Jobs flow
def test_active_job_shows_up_after_approve(desk_env, vehicle_rules):
    commit_job(desk_env, vehicle_rules, agn_message("ACT1"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "ACT1" in rendered
    assert any("No technician update recorded yet" in c.value for c in at.caption)


def test_record_technician_update_going_via_ui(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("ACT2"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.selectbox(key=f"active_update_type_{job_id}").set_value("Going").run()
    at.text_area(key=f"active_raw_update_{job_id}").set_value("omw now").run()
    at.button(key=f"active_save_update_{job_id}").click().run()
    assert len(at.exception) == 0
    conn = storage.connect(desk_env)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    conn.close()
    assert status == "In Progress"


def test_custom_update_shows_human_review_needed(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("ACT3"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Custom", "car had a weird sound, unsure")
    conn.close()
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(w.value for w in at.warning)
    assert "HUMAN REVIEW NEEDED" in rendered


def test_pass_cancel_button_disabled_without_reason(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("ACT4"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    button = next(b for b in at.button if b.key == f"active_pass_{job_id}")
    assert button.disabled is True


def test_pass_cancel_via_ui_requires_typed_reason(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("ACT5"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.text_input(key=f"active_pass_reason_{job_id}").set_value("customer cancelled service").run()
    at.button(key=f"active_pass_{job_id}").click().run()
    assert len(at.exception) == 0
    conn = storage.connect(desk_env)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    conn.close()
    assert status == "Cancelled"


def test_move_to_closing_hint_shown_only_after_completed_update(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("ACT6"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done, customer happy")
    conn.close()
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    assert len(at.exception) == 0
    assert any("MOVE TO CLOSING" in i.value for i in at.info)


# --------------------------------------------------------------- Closings flow
def test_closing_workspace_shows_enter_form_before_any_closing(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("CLW1"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    conn.close()
    at = app_for("luxor_desk_pages/closings.py", desk_env)
    assert len(at.exception) == 0
    assert any("No closing recorded yet" in c.value for c in at.caption)
    assert at.button(key=f"closing_record_{job_id}") is not None


def test_record_closing_via_ui(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("CLW2"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    conn.close()

    at = app_for("luxor_desk_pages/closings.py", desk_env)
    at.text_area(key=f"closing_raw_{job_id}").set_value("Closed $150 CC").run()
    at.text_input(key=f"closing_amount_{job_id}").set_value("150").run()
    at.text_input(key=f"closing_payment_{job_id}").set_value("CC").run()
    at.button(key=f"closing_record_{job_id}").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    conn.close()
    assert status == "Closed"


def test_prepare_send_and_complete_closing_via_ui(desk_env, vehicle_rules):
    """Seeds the closing directly (already covered by test_record_closing_via_ui
    above) so this test's AppTest session renders a stable set of widgets
    from the very first run -- the enter-closing form never appears at
    all, avoiding driving a widget-disappearing-mid-session interaction
    the AppTest harness doesn't handle well for text inputs."""
    job_id = commit_job(desk_env, vehicle_rules, agn_message("CLW2B"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    conn.close()

    at = app_for("luxor_desk_pages/closings.py", desk_env)
    prepare_btn = next((b for b in at.button if b.key == f"closing_prepare_{job_id}"), None)
    assert prepare_btn is not None, "PREPARE COMPANY CLOSING must appear once amount+payment method are on record"
    prepare_btn.click().run()
    assert len(at.exception) == 0

    sent_btn = next(b for b in at.button if b.key == f"closing_sent_{job_id}")
    sent_btn.click().run()
    assert len(at.exception) == 0

    complete_btn = next(b for b in at.button if b.key == f"closing_complete_{job_id}")
    complete_btn.click().run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    row = conn.execute("SELECT status, reconciliation_state FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row == ("Closed", "Settled")


def test_closing_missing_amount_shows_human_review_needed(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("CLW3"))
    conn = storage.connect(desk_env)
    storage.record_job_closing(conn, job_id, "Closed, amount not given yet")
    conn.close()
    at = app_for("luxor_desk_pages/closings.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(w.value for w in at.warning)
    assert "HUMAN REVIEW NEEDED" in rendered
    assert not any(b.key == f"closing_prepare_{job_id}" for b in at.button)


def test_complete_job_button_hidden_until_sent_manually(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("CLW4"))
    conn = storage.connect(desk_env)
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    conn.close()
    at = app_for("luxor_desk_pages/closings.py", desk_env)
    assert len(at.exception) == 0
    assert not any(b.key == f"closing_complete_{job_id}" for b in at.button)
    assert any("COMPLETE JOB is available once" in c.value for c in at.caption)


# --------------------------------------------------------------- History page
def test_completed_job_appears_in_history_with_audit_trail(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("HIST1"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Going", "omw")
    storage.mark_company_update_prepared(conn, job_id, "Going")
    storage.mark_company_update_sent_manually(conn, job_id, "Going")
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    storage.mark_company_closing_prepared(conn, job_id, "Closed\n150 CC")
    storage.mark_company_closing_sent_manually(conn, job_id, "Closed\n150 CC")
    storage.complete_job(conn, job_id)
    conn.close()

    at = app_for("luxor_desk_pages/history.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "HIST1" in rendered
    expander_labels = [e.label for e in at.expander]
    assert any("View details" in label for label in expander_labels)
    at.expander[0].expanded = True
    at.run()
    assert len(at.exception) == 0


def test_cancelled_job_appears_in_history(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("HIST2"))
    conn = storage.connect(desk_env)
    storage.pass_or_cancel_job(conn, job_id, "Cancel", "customer no longer needs service")
    conn.close()
    at = app_for("luxor_desk_pages/history.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "HIST2" in rendered


def test_in_progress_job_not_shown_in_history(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("HIST3"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Going", "omw")
    conn.close()
    at = app_for("luxor_desk_pages/history.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "HIST3" not in rendered


# ---------------------------------------------- No operational writes on render
def test_rendering_active_jobs_page_never_writes_events(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("NOWRITE1"))
    conn = storage.connect(desk_env)
    before = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    conn.close()

    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.run()  # simulate a refresh
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    after = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    conn.close()
    assert after == before


def test_rendering_closings_page_never_writes_events(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("NOWRITE2"))
    conn = storage.connect(desk_env)
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    before = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    conn.close()

    at = app_for("luxor_desk_pages/closings.py", desk_env)
    at.run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    after = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    conn.close()
    assert after == before


def test_rendering_history_page_never_writes_events(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("NOWRITE3"))
    conn = storage.connect(desk_env)
    storage.pass_or_cancel_job(conn, job_id, "Cancel", "cancelled for test")
    before = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    conn.close()

    at = app_for("luxor_desk_pages/history.py", desk_env)
    at.run()
    assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    after = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    conn.close()
    assert after == before


# ------------------------------------------------------- Safety/guard checks
def test_no_write_http_verbs_in_new_pages():
    for f in ("luxor_desk_pages/active_jobs.py", "luxor_desk_pages/closings.py", "luxor_desk_pages/history.py"):
        source = pathlib.Path(f).read_text(encoding="utf-8")
        for forbidden in (".post(", ".put(", ".delete(", ".patch("):
            assert forbidden not in source


def test_no_raw_script_or_onclick_in_new_pages():
    forbidden = ("<script", "onclick=", "onclick =", "javascript:", "navigator.clipboard")
    for f in ("luxor_desk_pages/active_jobs.py", "luxor_desk_pages/closings.py", "luxor_desk_pages/history.py"):
        source = pathlib.Path(f).read_text(encoding="utf-8")
        for bad in forbidden:
            assert bad not in source


def test_no_second_persistence_layer_in_new_pages():
    for f in ("luxor_desk_pages/active_jobs.py", "luxor_desk_pages/closings.py", "luxor_desk_pages/history.py"):
        source = pathlib.Path(f).read_text(encoding="utf-8")
        assert "CREATE TABLE" not in source
        assert "sqlite3.connect(" not in source


def test_fabricated_identifiers_cannot_appear_in_new_production_files():
    forbidden = ("Alex Rivera", "Taylor Nguyen", "$95.00", "ML-77QX")
    for f in DESK_FILES:
        source = pathlib.Path(f).read_text(encoding="utf-8")
        for bad in forbidden:
            assert bad not in source, f"{f} must not contain fabricated identifier {bad!r}"
