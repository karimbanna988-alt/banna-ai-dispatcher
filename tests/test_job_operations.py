"""Focused tests for the purpose-built operational methods added to
storage.py for the Active Jobs / Closings / Completed-History workflows:
record_technician_update, mark_company_update_prepared/sent_manually,
pass_or_cancel_job, record_job_closing, mark_company_closing_prepared/
sent_manually, complete_job.

Every test works from job_id + already-persisted state -- never
reconstructs an ExtractedJob to call these functions (only the one-time
setup helper below uses extract_job(), to create a real committed job
via the existing, unmodified NEW-message pipeline -- exactly how a real
job would already exist before any of these methods are ever called).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.storage import InvalidJobActionError, MessageType

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def commit_job(conn: sqlite3.Connection, vehicle_rules, raw: str) -> int:
    """The one place this test file uses extract_job() -- to create a
    real committed job via the existing NEW-message pipeline, exactly
    the way a real job already exists before a dispatcher ever opens
    Active Jobs/Closings for it. Every test below operates purely on the
    resulting job_id from this point on."""
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    return storage.save_audit_entry(conn, job, classify_result, final_action="Approve")


@pytest.fixture()
def job_id(conn, vehicle_rules) -> int:
    return commit_job(conn, vehicle_rules, load("agn_clean.txt"))


# ------------------------------------------------------- record_technician_update
def test_record_technician_update_going_sets_in_progress(conn, job_id):
    job = storage.record_technician_update(conn, job_id, "Going", "omw now")
    assert job["status"] == "In Progress"


def test_record_technician_update_eta_requires_minutes(conn, job_id):
    with pytest.raises(ValueError):
        storage.record_technician_update(conn, job_id, "ETA", "eta 15")


def test_record_technician_update_eta_with_minutes(conn, job_id):
    job = storage.record_technician_update(conn, job_id, "ETA", "eta 15", eta_minutes=15)
    assert job["status"] == "In Progress"
    latest = conn.execute(
        "SELECT update_type, eta_minutes FROM events WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
    ).fetchone()
    assert latest == ("ETA", 15)


def test_record_technician_update_no_answer_and_no_show(conn, job_id):
    job = storage.record_technician_update(conn, job_id, "No Answer", "called, no answer")
    assert job["status"] == "In Progress"
    job = storage.record_technician_update(conn, job_id, "No Show", "tech waited 15 min, no show")
    assert job["status"] == "In Progress"


def test_record_technician_update_not_available(conn, job_id):
    job = storage.record_technician_update(conn, job_id, "Not Available", "customer says come back later")
    assert job["status"] == "In Progress"


def test_record_technician_update_custom_ambiguous_needs_review(conn, job_id):
    job = storage.record_technician_update(conn, job_id, "Custom", "car is a weird color, unsure what to do")
    assert job["status"] == "Needs Review"


def test_record_technician_update_requires_raw_text(conn, job_id):
    with pytest.raises(ValueError):
        storage.record_technician_update(conn, job_id, "Going", "   ")


def test_record_technician_update_rejects_unknown_type(conn, job_id):
    with pytest.raises(ValueError):
        storage.record_technician_update(conn, job_id, "Not A Real Type", "text")


def test_record_technician_update_blocked_on_cancelled_job(conn, vehicle_rules, job_id):
    storage.pass_or_cancel_job(conn, job_id, "Cancel", "customer cancelled")
    with pytest.raises(InvalidJobActionError):
        storage.record_technician_update(conn, job_id, "Going", "omw")


def test_record_technician_update_missing_job_raises(conn):
    with pytest.raises(ValueError):
        storage.record_technician_update(conn, 999999, "Going", "omw")


# --------------------------------------------- company update prepared/sent
def test_mark_company_update_requires_a_prior_technician_update(conn, job_id):
    with pytest.raises(InvalidJobActionError):
        storage.mark_company_update_prepared(conn, job_id, "Going")


def test_company_update_prepared_versus_sent_manually_distinction(conn, job_id):
    storage.record_technician_update(conn, job_id, "Going", "omw now")
    storage.mark_company_update_prepared(conn, job_id, "Going")
    prepared_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND final_action = 'Company Update Prepared'", (job_id,)
    ).fetchone()[0]
    sent_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND final_action = 'Company Update Sent Manually'", (job_id,)
    ).fetchone()[0]
    assert prepared_events == 1
    assert sent_events == 0  # preparing must never itself claim "sent"

    storage.mark_company_update_sent_manually(conn, job_id, "Going")
    sent_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND final_action = 'Company Update Sent Manually'", (job_id,)
    ).fetchone()[0]
    assert sent_events == 1


def test_company_update_prepared_never_changes_status(conn, job_id):
    job = storage.record_technician_update(conn, job_id, "Going", "omw now")
    assert job["status"] == "In Progress"
    job = storage.mark_company_update_prepared(conn, job_id, "Going")
    assert job["status"] == "In Progress"  # OPERATIONAL_NOTE must preserve status


# ---------------------------------------------------------- pass_or_cancel_job
def test_pass_or_cancel_job_requires_reason(conn, job_id):
    with pytest.raises(ValueError):
        storage.pass_or_cancel_job(conn, job_id, "Cancel", "")


def test_pass_or_cancel_job_requires_known_action(conn, job_id):
    with pytest.raises(ValueError):
        storage.pass_or_cancel_job(conn, job_id, "Delete", "reason")


def test_pass_or_cancel_job_sets_cancelled_and_creates_audit_event(conn, job_id):
    job = storage.pass_or_cancel_job(conn, job_id, "Cancel", "customer no longer needs service")
    assert job["status"] == "Cancelled"
    row = conn.execute(
        "SELECT dispatcher_edits FROM events WHERE job_id = ? AND message_type = ? ORDER BY id DESC LIMIT 1",
        (job_id, MessageType.CANCELLATION.value),
    ).fetchone()
    assert "customer no longer needs service" in row[0]


def test_pass_action_also_sets_cancelled(conn, job_id):
    job = storage.pass_or_cancel_job(conn, job_id, "Pass", "technician unavailable, passing job")
    assert job["status"] == "Cancelled"


def test_pass_or_cancel_job_blocked_when_already_cancelled(conn, job_id):
    storage.pass_or_cancel_job(conn, job_id, "Cancel", "first reason")
    with pytest.raises(InvalidJobActionError):
        storage.pass_or_cancel_job(conn, job_id, "Cancel", "second reason")


# --------------------------------------------------------- record_job_closing
def test_record_job_closing_requires_raw_closing(conn, job_id):
    with pytest.raises(ValueError):
        storage.record_job_closing(conn, job_id, "")


def test_record_job_closing_sets_closed_status(conn, job_id):
    job = storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    assert job["status"] == "Closed"


def test_missing_closing_amount_remains_missing(conn, job_id):
    """Never invents or calculates a value that wasn't actually supplied.
    get_latest_closing_details() only returns a record when a structured
    amount was recorded (its own documented contract) -- so a closing
    entered with no amount correctly yields None there; check the raw
    event row directly to confirm the missing value was never invented
    to satisfy that contract."""
    storage.record_job_closing(conn, job_id, "Closed, customer paid cash but amount not confirmed yet")
    assert storage.get_latest_closing_details(conn, job_id) is None
    row = conn.execute(
        "SELECT closing_amount, closing_payment_method FROM events WHERE job_id = ? "
        "AND message_type = ? ORDER BY id DESC LIMIT 1",
        (job_id, MessageType.CLOSING.value),
    ).fetchone()
    assert row == (None, None)


def test_record_job_closing_blocked_on_cancelled_job(conn, job_id):
    storage.pass_or_cancel_job(conn, job_id, "Cancel", "cancelled before closing")
    with pytest.raises(InvalidJobActionError):
        storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")


# ------------------------------------------- company closing prepared/sent
def test_mark_company_closing_prepared_requires_a_recorded_closing(conn, job_id):
    with pytest.raises(InvalidJobActionError):
        storage.mark_company_closing_prepared(conn, job_id, "Closed\n150 CC")


def test_company_closing_prepared_versus_sent_manually_distinction(conn, job_id):
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    storage.mark_company_closing_prepared(conn, job_id, "Closed\n150 CC")
    prepared = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND final_action = 'Company Closing Prepared'", (job_id,)
    ).fetchone()[0]
    sent = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND final_action = 'Company Closing Sent Manually'", (job_id,)
    ).fetchone()[0]
    assert prepared == 1
    assert sent == 0

    storage.mark_company_closing_sent_manually(conn, job_id, "Closed\n150 CC")
    sent = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND final_action = 'Company Closing Sent Manually'", (job_id,)
    ).fetchone()[0]
    assert sent == 1


# ------------------------------------------------------------- complete_job
def test_complete_job_requires_closed_status(conn, job_id):
    with pytest.raises(InvalidJobActionError):
        storage.complete_job(conn, job_id)


def test_complete_job_valid_transition(conn, job_id):
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    job = storage.complete_job(conn, job_id)
    assert job["status"] == "Closed"
    assert job["reconciliation_state"] == "Settled"


def test_complete_job_is_idempotent(conn, job_id):
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    storage.complete_job(conn, job_id)
    before = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    storage.complete_job(conn, job_id)  # second call must not add a duplicate event
    after = conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert before == after


def test_complete_job_invalid_from_cancelled(conn, job_id):
    storage.pass_or_cancel_job(conn, job_id, "Cancel", "cancelled")
    with pytest.raises(InvalidJobActionError):
        storage.complete_job(conn, job_id)


# ---------------------------------------------- transactional, atomic writes
class _FlakyConnection(sqlite3.Connection):
    """A real sqlite3.Connection subclass (plain instances of
    sqlite3.Connection don't allow attribute/method patching) that fails
    exactly one targeted statement on demand -- used to prove
    _insert_operational_event()'s events-insert + jobs-snapshot-update
    happen in one real SQLite transaction, not two independent writes."""

    fail_on_prefix: str | None = None

    def execute(self, sql, *args, **kwargs):
        if self.fail_on_prefix and sql.strip().startswith(self.fail_on_prefix):
            raise sqlite3.OperationalError("simulated failure")
        return super().execute(sql, *args, **kwargs)


def test_operational_event_insert_and_status_update_are_one_transaction(tmp_path, vehicle_rules):
    db_path = tmp_path / "flaky.db"
    setup_conn = storage.connect(db_path)
    jid = commit_job(setup_conn, vehicle_rules, load("agn_clean.txt"))
    setup_conn.close()

    flaky_conn = sqlite3.connect(db_path, factory=_FlakyConnection)
    events_before = flaky_conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (jid,)).fetchone()[0]
    assert events_before == 1  # the original NEW-job dispatch event from commit_job()

    flaky_conn.fail_on_prefix = "UPDATE jobs SET status"
    with pytest.raises(sqlite3.OperationalError):
        storage.record_technician_update(flaky_conn, jid, "Going", "omw now")
    flaky_conn.fail_on_prefix = None

    events_count = flaky_conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (jid,)).fetchone()[0]
    status = flaky_conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()[0]
    assert events_count == events_before  # the events insert was rolled back too, not left dangling
    assert status == "Approved"  # unchanged from before the failed call
    flaky_conn.close()


# --------------------------------------------------- full chronological history
def test_full_chronological_audit_history(conn, job_id):
    storage.record_technician_update(conn, job_id, "Going", "omw now")
    storage.mark_company_update_prepared(conn, job_id, "Going")
    storage.mark_company_update_sent_manually(conn, job_id, "Going")
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    storage.mark_company_closing_prepared(conn, job_id, "Closed\n150 CC")
    storage.mark_company_closing_sent_manually(conn, job_id, "Closed\n150 CC")
    storage.complete_job(conn, job_id)

    timeline = storage.get_audit_timeline_by_job_id(conn, job_id)
    final_actions = [e["final_action"] for e in timeline]
    assert "Approve" in final_actions  # original NEW dispatch
    assert final_actions.count("Approve") >= 2  # NEW dispatch + STATUS "Going"
    assert "Company Update Prepared" in final_actions
    assert "Company Update Sent Manually" in final_actions
    assert "Company Closing Prepared" in final_actions
    assert "Company Closing Sent Manually" in final_actions
    assert "Job Completed" in final_actions
    # chronological -- ids strictly increasing, never reordered
    ids = [e["id"] for e in timeline]
    assert ids == sorted(ids)


# ----------------------------------------------------- derive_job_status unit
def test_derive_job_status_operational_note_preserves_status():
    assert storage.derive_job_status("In Progress", "OPERATIONAL_NOTE", "Company Update Prepared") == "In Progress"
    assert storage.derive_job_status("Closed", "OPERATIONAL_NOTE", "Job Completed") == "Closed"


def test_derive_job_status_pass_and_cancel_both_cancel():
    assert storage.derive_job_status("In Progress", "CANCELLATION", "Pass") == "Cancelled"
    assert storage.derive_job_status("In Progress", "CANCELLATION", "Cancel") == "Cancelled"
