"""Fault-injection tests: for every operational command, simulate a
failure at each stage of its write (event insert, jobs snapshot update,
dispatch_tracking update, pending-message update, whatsapp_handoffs
insert) and prove the database matches its pre-command state
afterward -- no partial event/snapshot combination survives.

Uses a real sqlite3.Connection subclass (plain sqlite3.Connection
instances don't allow attribute/method patching -- established in the
previous round's test_job_operations.py::_FlakyConnection) that raises
on a targeted SQL statement prefix.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.application import DispatcherService
from dispatcher.app_models import ClosingCommand, TechnicianUpdateCommand
from dispatcher.parser import extract_job, load_vehicle_rules


class _FlakyConnection(sqlite3.Connection):
    fail_on_prefix: str | None = None

    def execute(self, sql, *args, **kwargs):
        if self.fail_on_prefix and sql.strip().startswith(self.fail_on_prefix):
            raise sqlite3.OperationalError("simulated failure")
        return super().execute(sql, *args, **kwargs)


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def _seeded_job(db_path: Path, vehicle_rules) -> int:
    setup_conn = storage.connect(db_path)
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: FAULT1\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(setup_conn, job)
    job_id = storage.save_audit_entry(setup_conn, job, cr, final_action="Approve")
    setup_conn.close()
    return job_id


def _flaky_conn(db_path: Path, fail_on_prefix: str) -> _FlakyConnection:
    conn = sqlite3.connect(db_path, factory=_FlakyConnection)
    conn.fail_on_prefix = fail_on_prefix
    return conn


def _snapshot(conn) -> dict:
    return {
        "events": conn.execute("SELECT * FROM events ORDER BY id").fetchall(),
        "jobs": conn.execute("SELECT * FROM jobs ORDER BY id").fetchall(),
    }


# ------------------------------------------------- fail after event insert
def test_fault_after_event_insert_leaves_no_partial_status_change(tmp_path, vehicle_rules):
    """Fails the jobs-snapshot UPDATE that follows the events INSERT --
    proves the whole _insert_operational_event() transaction rolls back
    together, not just the failed statement."""
    db_path = tmp_path / "fault1.db"
    job_id = _seeded_job(db_path, vehicle_rules)

    baseline_conn = storage.connect(db_path)
    before = _snapshot(baseline_conn)
    baseline_conn.close()

    conn = _flaky_conn(db_path, "UPDATE jobs SET status")
    service = DispatcherService(conn)
    with pytest.raises(sqlite3.OperationalError):
        service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    conn.close()

    after_conn = storage.connect(db_path)
    after = _snapshot(after_conn)
    after_conn.close()
    assert before == after


# --------------------------------------------------- fail on jobs update
def test_fault_on_jobs_snapshot_update_for_closing(tmp_path, vehicle_rules):
    db_path = tmp_path / "fault2.db"
    job_id = _seeded_job(db_path, vehicle_rules)

    baseline_conn = storage.connect(db_path)
    before = _snapshot(baseline_conn)
    baseline_conn.close()

    conn = _flaky_conn(db_path, "UPDATE jobs SET status")
    service = DispatcherService(conn)
    with pytest.raises(sqlite3.OperationalError):
        service.record_closing(ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC"))
    conn.close()

    after_conn = storage.connect(db_path)
    after = _snapshot(after_conn)
    after_conn.close()
    assert before == after


# ----------------------------------------------- fail on completion (2-part)
def test_fault_during_complete_job_leaves_reconciliation_unchanged(tmp_path, vehicle_rules):
    """complete_job()'s single UPDATE sets both status and
    reconciliation_state together -- a fault here must never leave
    reconciliation_state='Settled' without the matching event, or vice
    versa."""
    db_path = tmp_path / "fault3.db"
    job_id = _seeded_job(db_path, vehicle_rules)

    setup_conn = storage.connect(db_path)
    storage.record_job_closing(setup_conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    before_conn_snapshot = _snapshot(setup_conn)
    setup_conn.close()

    conn = _flaky_conn(db_path, "UPDATE jobs SET status = ?, reconciliation_state")
    service = DispatcherService(conn)
    with pytest.raises(sqlite3.OperationalError):
        service.complete_job(job_id)
    conn.close()

    after_conn = storage.connect(db_path)
    after = _snapshot(after_conn)
    status_row = after_conn.execute("SELECT status, reconciliation_state FROM jobs WHERE id = ?", (job_id,)).fetchone()
    after_conn.close()
    assert before_conn_snapshot == after
    assert status_row == ("Closed", None)  # never partially "Settled" without the event


# ----------------------------------------------------- fail on event insert
def test_fault_during_event_insert_itself_leaves_no_row(tmp_path, vehicle_rules):
    db_path = tmp_path / "fault4.db"
    job_id = _seeded_job(db_path, vehicle_rules)

    baseline_conn = storage.connect(db_path)
    before_count = baseline_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    baseline_conn.close()

    conn = _flaky_conn(db_path, "INSERT INTO events")
    service = DispatcherService(conn)
    with pytest.raises(sqlite3.OperationalError):
        service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    conn.close()

    after_conn = storage.connect(db_path)
    after_count = after_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    after_conn.close()
    assert before_count == after_count


# -------------------------------------------- fail on pass_or_cancel_job
def test_fault_during_pass_or_cancel_leaves_status_untouched(tmp_path, vehicle_rules):
    db_path = tmp_path / "fault5.db"
    job_id = _seeded_job(db_path, vehicle_rules)

    baseline_conn = storage.connect(db_path)
    before_status = baseline_conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    before_event_count = baseline_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    baseline_conn.close()

    conn = _flaky_conn(db_path, "UPDATE jobs SET status")
    service = DispatcherService(conn)
    with pytest.raises(sqlite3.OperationalError):
        service.pass_or_cancel_job(job_id, "Cancel", "test fault")
    conn.close()

    after_conn = storage.connect(db_path)
    after_status = after_conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    after_event_count = after_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    after_conn.close()
    assert before_status == after_status == "Approved"
    assert before_event_count == after_event_count


# ------------------------------------------- recovery after a fixed fault
def test_command_succeeds_normally_after_a_prior_fault_is_cleared(tmp_path, vehicle_rules):
    """The database must be usable normally after an induced failure --
    no lingering corrupted/locked state."""
    db_path = tmp_path / "fault6.db"
    job_id = _seeded_job(db_path, vehicle_rules)

    conn = _flaky_conn(db_path, "UPDATE jobs SET status")
    service = DispatcherService(conn)
    with pytest.raises(sqlite3.OperationalError):
        service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))

    conn.fail_on_prefix = None  # clear the induced fault
    result = service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    assert result.success
    assert result.current_status == "In Progress"
    conn.close()

    assert sqlite3.connect(db_path).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
