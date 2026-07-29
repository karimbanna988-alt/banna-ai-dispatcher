"""Section 8 guard: rendering (or re-rendering) any authenticated
luxor_desk_pages/*.py page must never create an audit event, update a
lifecycle timestamp, change a status, or action a queue item. Every
page is rendered twice, with real seeded data covering every major
display branch (a live pending message, a technician-updated active
job, a closing awaiting completion, a completed job, a real
technician), and every operational table's row count/content must be
byte-for-byte identical before and after both renders.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules

PAGES = [
    "luxor_desk_pages/dispatch.py",
    "luxor_desk_pages/active_jobs.py",
    "luxor_desk_pages/closings.py",
    "luxor_desk_pages/history.py",
    "luxor_desk_pages/technicians.py",
    "luxor_desk_pages/activity.py",
]

OPERATIONAL_TABLES = (
    "events", "jobs", "quo_pending_messages", "dispatch_tracking",
    "whatsapp_handoffs", "quo_outbound_log", "console_activation_state",
)


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


def future_ts(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def seed_full_dataset(db_path, vehicle_rules) -> None:
    """One of everything a page might render: a live pending message
    (New Jobs), an active job with a technician update (Active Jobs), a
    closed-but-unsettled job (Closings), a completed job (History), and
    a real technician (Technicians/Activity)."""
    conn = storage.connect(db_path)
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()

    storage.save_pending_and_mark_processed(
        conn, message_id="PURITY1", conversation_id="c1", group_label="AGN",
        raw_text="Ref: AGN Locksmith Services\nPDL: PURITYTICKET\nPh: 1-555-010-3000\n"
                 "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n",
        from_number="+15550109900", received_at=future_ts(5),
    )

    def commit_job(raw: str) -> int:
        job = extract_job(raw, vehicle_rules)
        cr = storage.classify_incoming(conn, job)
        return storage.save_audit_entry(conn, job, cr, final_action="Approve")

    active_job_id = commit_job(
        "Ref: AGN Locksmith Services\nPDL: ACTIVEPURITY\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n"
    )
    storage.record_technician_update(conn, active_job_id, "Going", "omw now")

    closing_job_id = commit_job(
        "Ref: AGN Locksmith Services\nPDL: CLOSEPURITY\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n"
    )
    storage.record_job_closing(conn, closing_job_id, "Closed $150 CC", final_amount="150", payment_method="CC")

    completed_job_id = commit_job(
        "Ref: AGN Locksmith Services\nPDL: DONEPURITY\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n"
    )
    storage.record_job_closing(conn, completed_job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    storage.complete_job(conn, completed_job_id)

    storage.add_technician(conn, name="Purity Test Tech", areas=["Charlotte"], skills=["car lockout"], dispatch_group="Test Group")
    conn.close()


def _snapshot(db_path) -> dict:
    conn = storage.connect(db_path)
    snapshot = {}
    for table in OPERATIONAL_TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        snapshot[table] = rows
    conn.close()
    return snapshot


@pytest.mark.parametrize("page", PAGES)
def test_rendering_page_twice_never_writes_operational_state(desk_env, vehicle_rules, page):
    import luxor_desk_shared as shared_desk

    seed_full_dataset(desk_env, vehicle_rules)
    before = _snapshot(desk_env)

    at = AppTest.from_file(page, default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    assert len(at.exception) == 0, f"{page} raised on first render: {[e.message for e in at.exception]}"
    at.run()  # simulate a refresh/rerun
    assert len(at.exception) == 0, f"{page} raised on second render: {[e.message for e in at.exception]}"

    after = _snapshot(desk_env)
    assert before == after, f"{page} changed operational table state merely by rendering twice"
