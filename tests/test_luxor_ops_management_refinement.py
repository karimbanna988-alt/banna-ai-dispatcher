"""Tests for the Closings & Payments and Technicians pages added to
luxor_ops in the Management CRM refinement phase. All technician data
used here is synthetic and local to an isolated tmp_path database -- the
real roster is never touched."""

import io
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.storage import ClassifyResult, MessageType

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "")
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def make_page(name: str):
    at = AppTest.from_file(f"luxor_ops_pages/{name}.py", default_timeout=30)
    at.session_state["luxor_ops_authenticated"] = True
    at.run()
    assert len(at.exception) == 0
    return at


def seed_closed_job(db_path, amount="120.00", payment_method="CC"):
    conn = storage.connect(db_path)
    vehicle_rules = load_vehicle_rules()
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    closing_classify = ClassifyResult(MessageType.CLOSING, existing_job_id=job_id, reason="test closing")
    storage.save_audit_entry(
        conn, job, closing_classify, luxor_message="Closed -- $120 CC", final_action="Approve",
        closing_amount=amount, closing_payment_method=payment_method,
    )
    conn.close()
    return job_id


# ------------------------------------------------------- Closings & Payments
def test_unsettled_closed_job_is_flagged(app_env):
    seed_closed_job(app_env)
    at = make_page("closings_payments")
    assert any("awaiting reconciliation" in w.value.lower() for w in at.warning)


def test_no_unsettled_jobs_shows_success(app_env):
    at = make_page("closings_payments")
    assert any("no unsettled" in s.value.lower() for s in at.success)


def test_closing_amount_and_payment_method_displayed(app_env):
    seed_closed_job(app_env, amount="150.00", payment_method="Cash")
    at = make_page("closings_payments")
    at.segmented_control(key="luxor_ops_closings_view").set_value("Closed").run()
    rendered = "\n".join(m.value for m in at.markdown)
    assert "150.00" in rendered
    assert "Cash" in rendered


def test_mark_settled_updates_reconciliation_state_not_status(app_env):
    job_id = seed_closed_job(app_env)
    at = make_page("closings_payments")
    at.segmented_control(key="luxor_ops_closings_view").set_value("Closed").run()
    at.button(key=f"luxor_ops_settle_{job_id}").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    status, reconciliation_state = conn.execute(
        "SELECT status, reconciliation_state FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    conn.close()
    assert status == "Closed"
    assert reconciliation_state == "Settled"


def test_settled_job_moves_out_of_awaiting_reconciliation_view(app_env):
    job_id = seed_closed_job(app_env)
    conn = storage.connect(app_env)
    storage.mark_job_settled(conn, job_id)
    conn.close()

    at = make_page("closings_payments")
    assert any("no unsettled" in s.value.lower() for s in at.success)
    at.segmented_control(key="luxor_ops_closings_view").set_value("Settled").run()
    rendered = "\n".join(m.value for m in at.markdown)
    assert "FAKE1" in rendered


# ------------------------------------------------------------- Technicians
def test_empty_roster_shows_zero(app_env):
    at = make_page("technicians")
    assert any("Current roster: 0" in m.value for m in at.markdown)


def test_csv_upload_shows_preview_without_writing(app_env):
    csv_content = "name,areas,skills,vehicle_restrictions,dispatch_group,closing_group\nJane Tech,Charlotte,Lockout,,WA Group A,WA Closings A\n"
    at = make_page("technicians")
    uploader = at.get("file_uploader")[0]
    uploader.set_value(("roster.csv", csv_content.encode("utf-8"), "text/csv"))
    at.run()
    assert len(at.exception) == 0

    rendered = "\n".join(m.value for m in at.markdown)
    assert "Proposed records (1)" in rendered
    assert "not yet saved" in rendered

    conn = storage.connect(app_env)
    count = conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    conn.close()
    assert count == 0  # nothing written until explicit approval


def test_approve_and_import_writes_via_existing_add_technician(app_env):
    csv_content = "name,areas,skills,vehicle_restrictions,dispatch_group,closing_group\nJane Tech,Charlotte,,,WA Group A,\n"
    at = make_page("technicians")
    uploader = at.get("file_uploader")[0]
    uploader.set_value(("roster.csv", csv_content.encode("utf-8"), "text/csv"))
    at.run()

    at.button(key="luxor_ops_import_technicians").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    rows = conn.execute("SELECT name, dispatch_group FROM technicians").fetchall()
    conn.close()
    assert rows == [("Jane Tech", "WA Group A")]


def test_row_missing_required_fields_is_skipped_with_warning(app_env):
    csv_content = "name,areas\n,Charlotte\nNo Area Tech,\n"
    at = make_page("technicians")
    uploader = at.get("file_uploader")[0]
    uploader.set_value(("roster.csv", csv_content.encode("utf-8"), "text/csv"))
    at.run()
    assert len(at.exception) == 0
    assert len(at.warning) == 2  # missing name, missing areas
    assert not any(b.key == "luxor_ops_import_technicians" for b in at.button)


def test_row_with_unconfigured_area_is_rejected_with_reason(app_env):
    csv_content = "name,areas\nFabricated Tech,Nowhereville\n"
    at = make_page("technicians")
    uploader = at.get("file_uploader")[0]
    uploader.set_value(("roster.csv", csv_content.encode("utf-8"), "text/csv"))
    at.run()
    assert len(at.exception) == 0
    assert any("Nowhereville" in w.value and "could not be confirmed" in w.value for w in at.warning)
    assert not any(b.key == "luxor_ops_import_technicians" for b in at.button)

    conn = storage.connect(app_env)
    count = conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    conn.close()
    assert count == 0


def test_row_with_typo_area_is_corrected_and_importable(app_env):
    csv_content = "name,areas\nFabricated Tech,raliegh\n"
    at = make_page("technicians")
    uploader = at.get("file_uploader")[0]
    uploader.set_value(("roster.csv", csv_content.encode("utf-8"), "text/csv"))
    at.run()
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)
    assert "Raleigh" in rendered  # corrected, canonical spelling shown

    at.button(key="luxor_ops_import_technicians").click().run()
    conn = storage.connect(app_env)
    areas = conn.execute("SELECT areas_json FROM technicians WHERE name = 'Fabricated Tech'").fetchone()[0]
    conn.close()
    assert "Raleigh" in areas
