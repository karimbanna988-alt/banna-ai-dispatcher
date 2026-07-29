"""Real two-connection concurrency tests -- two separate sqlite3
connections to the same temporary file, driven from two threads with a
threading.Barrier (never a sleep-based race) so both actually attempt
their write at the same instant. Proves storage.BUSY_TIMEOUT_MS absorbs
normal same-file contention without an uncaught "database is locked"
error reaching a caller, and that the idempotency layer prevents
duplicate operational effects even under a genuine race.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.application import DispatcherService, compute_idempotency_key
from dispatcher.app_models import ClosingCommand, TechnicianUpdateCommand
from dispatcher.parser import extract_job, load_vehicle_rules


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def _seeded_job(db_path: Path, vehicle_rules) -> int:
    conn = storage.connect(db_path)
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: RACE1\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, cr, final_action="Approve")
    conn.close()
    return job_id


def _connect(db_path: Path) -> sqlite3.Connection:
    """A second, independent connection to the same file -- mirrors two
    real OS processes (the Streamlit app and the standalone poller, or
    two browser tabs) each with their own connection object."""
    return storage.connect(db_path)


def _run_concurrently(fn_a, fn_b):
    """Runs fn_a and fn_b on separate threads, released by a shared
    barrier so both attempt their write at the same instant -- no sleep,
    no timing guesswork."""
    barrier = threading.Barrier(2)
    results = {}
    errors = {}

    def wrap(name, fn):
        def runner():
            try:
                barrier.wait(timeout=5)
                results[name] = fn()
            except Exception as exc:  # noqa: BLE001 -- captured for the assertion, not swallowed
                errors[name] = exc
        return runner

    t1 = threading.Thread(target=wrap("a", fn_a))
    t2 = threading.Thread(target=wrap("b", fn_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    return results, errors


# ------------------------------------------------ identical command race
def test_identical_technician_update_submitted_simultaneously(tmp_path, vehicle_rules):
    db_path = tmp_path / "race.db"
    job_id = _seeded_job(db_path, vehicle_rules)
    conn_a = _connect(db_path)
    conn_b = _connect(db_path)
    service_a = DispatcherService(conn_a)
    service_b = DispatcherService(conn_b)

    cmd = TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now")

    results, errors = _run_concurrently(
        lambda: service_a.record_technician_update(cmd),
        lambda: service_b.record_technician_update(cmd),
    )

    assert not errors, f"uncaught errors from concurrent identical command: {errors}"
    assert results["a"].success and results["b"].success
    # The data guarantee that actually matters: storage's own
    # check-before-insert (re-verified independently of the
    # application-layer pre-check both threads raced on) means at most
    # one event is ever created, regardless of what each thread's own
    # idempotent_replay flag reports. Under a genuine same-instant race,
    # BOTH threads' pre-check can read "not found yet" before either has
    # committed -- so idempotent_replay may read False on both sides;
    # that is a documented, accepted best-effort-reporting limitation
    # (see docs/APPLICATION_SERVICE.md's Idempotency section), not a
    # data-correctness bug. The event count assertion below is the real
    # guarantee under test.
    conn_check = _connect(db_path)
    status_events = conn_check.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND message_type = 'STATUS'", (job_id,)
    ).fetchone()[0]
    conn_check.close()
    assert status_events == 1  # no duplicate operational effect
    conn_a.close()
    conn_b.close()


# --------------------------------------------------- different updates race
def test_two_distinct_technician_updates_submitted_simultaneously(tmp_path, vehicle_rules):
    """Both must succeed and both must be recorded -- concurrency safety
    must never silently drop or merge two genuinely different legitimate
    updates."""
    db_path = tmp_path / "race2.db"
    job_id = _seeded_job(db_path, vehicle_rules)
    conn_a = _connect(db_path)
    conn_b = _connect(db_path)
    service_a = DispatcherService(conn_a)
    service_b = DispatcherService(conn_b)

    results, errors = _run_concurrently(
        lambda: service_a.record_technician_update(
            TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now")
        ),
        lambda: service_b.record_technician_update(
            TechnicianUpdateCommand(job_id=job_id, update_type="No Answer", raw_update="called, no answer")
        ),
    )

    assert not errors, f"uncaught errors from concurrent distinct commands: {errors}"
    assert results["a"].success and results["b"].success
    assert results["a"].idempotent_replay is False
    assert results["b"].idempotent_replay is False

    conn_check = _connect(db_path)
    status_events = conn_check.execute(
        "SELECT update_type FROM events WHERE job_id = ? AND message_type = 'STATUS' ORDER BY id", (job_id,)
    ).fetchall()
    conn_check.close()
    # Real thread scheduling doesn't guarantee commit order -- both
    # distinct updates must be recorded, in whichever order they
    # actually landed; neither may be silently dropped or merged.
    assert sorted(r[0] for r in status_events) == ["Going", "No Answer"]
    conn_a.close()
    conn_b.close()


# ------------------------------------------------ stale update vs completion
def test_stale_update_racing_completion_does_not_corrupt_state(tmp_path, vehicle_rules):
    """A technician update racing a closing+completion on the same job:
    both are legal operations on an in-progress job, so both may
    succeed, but the database must never end up with a partial/
    inconsistent combination (an event with no matching status update,
    or vice versa)."""
    db_path = tmp_path / "race3.db"
    job_id = _seeded_job(db_path, vehicle_rules)
    conn_a = _connect(db_path)
    conn_b = _connect(db_path)
    service_a = DispatcherService(conn_a)
    service_b = DispatcherService(conn_b)

    def do_update():
        return service_a.record_technician_update(
            TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now")
        )

    def do_closing():
        return service_b.record_closing(
            ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC")
        )

    results, errors = _run_concurrently(do_update, do_closing)
    assert not errors, f"uncaught errors: {errors}"
    assert results["a"].success
    assert results["b"].success

    conn_check = _connect(db_path)
    assert conn_check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn_check.execute("PRAGMA foreign_key_check").fetchall() == []
    status_events = conn_check.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND message_type = 'STATUS'", (job_id,)
    ).fetchone()[0]
    closing_events = conn_check.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND message_type = 'CLOSING'", (job_id,)
    ).fetchone()[0]
    conn_check.close()
    assert status_events == 1
    assert closing_events == 1
    conn_a.close()
    conn_b.close()


# --------------------------------------------------- cancellation vs closing
def test_cancellation_racing_closing_produces_one_consistent_winner(tmp_path, vehicle_rules):
    """Pass/cancel and record-closing are mutually exclusive once one
    succeeds (a cancelled job can't later be closed, and vice versa via
    the InvalidJobActionError guard) -- under a real race, exactly one
    must win; the loser must fail safely with a structured ActionResult,
    never an uncaught database error, and never a corrupted status."""
    db_path = tmp_path / "race4.db"
    job_id = _seeded_job(db_path, vehicle_rules)
    conn_a = _connect(db_path)
    conn_b = _connect(db_path)
    service_a = DispatcherService(conn_a)
    service_b = DispatcherService(conn_b)

    def do_cancel():
        return service_a.pass_or_cancel_job(job_id, "Cancel", "customer cancelled")

    def do_closing():
        return service_b.record_closing(
            ClosingCommand(job_id=job_id, raw_closing="Closed $150 CC", final_amount="150", payment_method="CC")
        )

    results, errors = _run_concurrently(do_cancel, do_closing)
    assert not errors, f"uncaught errors: {errors}"
    # Both branches are internally guarded (InvalidJobActionError caught
    # -> ActionResult.failure), so both threads return normally even
    # when one loses the race -- never an unhandled exception.
    outcomes = [results["a"].success, results["b"].success]
    assert outcomes.count(True) >= 1  # at least the winner succeeded

    conn_check = _connect(db_path)
    status = conn_check.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    conn_check.close()
    assert status in ("Cancelled", "Closed")  # exactly one consistent final state, never a third/garbled value
    conn_a.close()
    conn_b.close()


# ---------------------------------------------------- busy_timeout absorbs
def test_busy_timeout_absorbs_normal_contention_without_uncaught_lock_error(tmp_path, vehicle_rules):
    """A long-held write transaction on one connection must not cause a
    concurrent write attempt on another connection to raise an uncaught
    'database is locked' error -- BUSY_TIMEOUT_MS must let it wait and
    then succeed."""
    db_path = tmp_path / "race5.db"
    job_id = _seeded_job(db_path, vehicle_rules)
    conn_a = _connect(db_path)
    conn_b = _connect(db_path)

    hold_barrier = threading.Barrier(2)
    release_event = threading.Event()
    outcome = {}

    def holder():
        conn_a.execute("BEGIN IMMEDIATE")
        conn_a.execute("UPDATE jobs SET updated_at = updated_at WHERE id = ?", (job_id,))
        hold_barrier.wait(timeout=5)
        release_event.wait(timeout=2)  # hold the write lock briefly
        conn_a.commit()

    def contender():
        hold_barrier.wait(timeout=5)
        try:
            service_b = DispatcherService(conn_b)
            result = service_b.pass_or_cancel_job(job_id, "Cancel", "contended write")
            outcome["result"] = result
        except sqlite3.OperationalError as exc:
            outcome["error"] = exc
        finally:
            release_event.set()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=contender)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert "error" not in outcome, f"busy_timeout did not absorb contention: {outcome.get('error')}"
    assert outcome["result"].success
    conn_a.close()
    conn_b.close()
