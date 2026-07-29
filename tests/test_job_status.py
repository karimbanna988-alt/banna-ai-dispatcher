"""Unit tests for the job-status state machine (storage.derive_job_status)
and the chronological-replay backfill that uses it. Kept separate from
test_storage.py since this is a large, self-contained transition table
worth reading as its own unit."""

import sqlite3
from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------- derive_job_status() -----
def test_handle_manually_always_wins_regardless_of_message_type_or_previous():
    for message_type in ("NEW", "STATUS", "CLOSING", "CANCELLATION", "CORRECTION", "TOTALLY_UNKNOWN"):
        for prev in ("New", "Approved", "Closed", None, "garbage"):
            assert storage.derive_job_status(prev, message_type, "Handle Manually") == "Handle Manually"


@pytest.mark.parametrize("action", ["Approve", "Edit"])
def test_new_message_approve_or_edit_is_approved(action):
    assert storage.derive_job_status("New", "NEW", action) == "Approved"
    assert storage.derive_job_status(None, "NEW", action) == "Approved"  # prev irrelevant for NEW


def test_new_message_reject_is_rejected():
    assert storage.derive_job_status("New", "NEW", "Reject") == "Rejected"


def test_new_message_unknown_action_falls_back_to_needs_review():
    assert storage.derive_job_status("New", "NEW", None) == "Needs Review"
    assert storage.derive_job_status("New", "NEW", "Something Else") == "Needs Review"


@pytest.mark.parametrize("action", ["Approve", "Edit"])
def test_status_message_approve_or_edit_is_in_progress(action):
    assert storage.derive_job_status("Approved", "STATUS", action) == "In Progress"


def test_status_message_reject_preserves_previous_status():
    assert storage.derive_job_status("Approved", "STATUS", "Reject") == "Approved"
    assert storage.derive_job_status("In Progress", "STATUS", "Reject") == "In Progress"


def test_status_message_reject_with_no_valid_previous_falls_back_to_needs_review():
    assert storage.derive_job_status(None, "STATUS", "Reject") == "Needs Review"
    assert storage.derive_job_status("not-a-real-status", "STATUS", "Reject") == "Needs Review"


@pytest.mark.parametrize("action", ["Approve", "Edit"])
def test_closing_message_approve_or_edit_is_closed(action):
    assert storage.derive_job_status("In Progress", "CLOSING", action) == "Closed"


def test_closing_message_reject_preserves_previous_status():
    assert storage.derive_job_status("In Progress", "CLOSING", "Reject") == "In Progress"


@pytest.mark.parametrize("action", ["Approve", "Edit"])
def test_cancellation_message_approve_or_edit_is_cancelled(action):
    assert storage.derive_job_status("Approved", "CANCELLATION", action) == "Cancelled"


def test_cancellation_message_reject_preserves_previous_status():
    assert storage.derive_job_status("Approved", "CANCELLATION", "Reject") == "Approved"


@pytest.mark.parametrize("action", ["Approve", "Edit", "Reject", "Handle Manually", None])
def test_closing_without_dispatch_is_always_needs_review_except_handle_manually(action):
    result = storage.derive_job_status("Approved", "CLOSING_WITHOUT_DISPATCH", action)
    if action == "Handle Manually":
        assert result == "Handle Manually"  # rule 1 takes priority over the anomaly rule
    else:
        assert result == "Needs Review"


@pytest.mark.parametrize("message_type", ["CORRECTION", "UPDATE_REQUEST", "DUPLICATE", "UNRECOGNIZED"])
@pytest.mark.parametrize("action", ["Approve", "Edit", "Reject"])
def test_non_lifecycle_message_types_preserve_previous_status(message_type, action):
    assert storage.derive_job_status("Closed", message_type, action) == "Closed"
    assert storage.derive_job_status("In Progress", message_type, action) == "In Progress"
    assert storage.derive_job_status("Cancelled", message_type, action) == "Cancelled"


def test_non_lifecycle_message_type_with_no_valid_previous_falls_back_to_needs_review():
    assert storage.derive_job_status(None, "CORRECTION", "Approve") == "Needs Review"


def test_non_lifecycle_message_type_unknown_action_falls_back_to_needs_review():
    assert storage.derive_job_status("Approved", "UNRECOGNIZED", "Some Weird Action") == "Needs Review"


def test_unknown_message_type_falls_back_to_needs_review():
    assert storage.derive_job_status("Approved", "SOMETHING_NEW_AND_UNKNOWN", "Approve") == "Needs Review"


def test_new_is_a_reachable_previous_status_for_a_malformed_first_event():
    """A job's very first event should always be NEW in normal operation,
    but if history is malformed (e.g. a correction with no prior NEW), the
    default seed status "New" is itself a valid previous status and gets
    preserved rather than crashing or silently defaulting elsewhere."""
    assert storage.derive_job_status("New", "CORRECTION", "Approve") == "New"


# ------------------------------------------- Live save path integration ----
def test_save_audit_entry_sets_status_last_event_id_and_last_event_at(conn, vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)

    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")

    row = conn.execute(
        "SELECT status, last_event_id, last_event_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row[0] == "Approved"
    assert row[1] is not None
    event_row = conn.execute("SELECT id, created_at FROM events WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    assert row[1] == event_row[0]
    assert row[2] == event_row[1]


def test_save_audit_entry_status_progresses_through_a_realistic_sequence(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "Approved"

    status_job = extract_job("Going", vehicle_rules)
    status_job.ticket_number = job.ticket_number
    status_job.source = job.source
    status_result = storage.classify_incoming(conn, status_job)
    assert status_result.message_type.value == "STATUS"
    storage.save_audit_entry(conn, status_job, status_result, final_action="Approve")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "In Progress"

    closing_raw = raw + "\nClosed\n$500 CC\n"
    closing_job = extract_job(closing_raw, vehicle_rules)
    closing_result = storage.classify_incoming(conn, closing_job)
    assert closing_result.message_type.value == "CLOSING"
    storage.save_audit_entry(conn, closing_job, closing_result, final_action="Approve")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "Closed"


def test_rejecting_a_later_status_report_preserves_the_jobs_operational_status(conn, vehicle_rules):
    """The only way to reach 'Rejected' is rejecting the original NEW
    message -- rejecting a later status report must not blow away an
    otherwise fine, already-approved job."""
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")

    status_job = extract_job("Going", vehicle_rules)
    status_job.ticket_number = job.ticket_number
    status_job.source = job.source
    status_result = storage.classify_incoming(conn, status_job)
    storage.save_audit_entry(conn, status_job, status_result, final_action="Reject")

    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "Approved"


# ------------------------------------------------------------- Backfill ----
def _insert_raw_event(conn, *, job_id, ticket_number, message_type, final_action, created_at, source="AGN"):
    conn.execute(
        "INSERT INTO events (job_id, ticket_number, raw_message, message_type, source, "
        "final_action, created_at, channel) VALUES (?, ?, 'raw', ?, ?, ?, ?, 'manual')",
        (job_id, ticket_number, message_type, source, final_action, created_at),
    )
    conn.commit()


def test_backfill_replays_events_chronologically_not_just_the_latest(tmp_path):
    """Proves replay, not "read the latest row": a CORRECTION approval's
    correct status depends entirely on what came before it (preserve
    previous), which single-latest-event derivation cannot determine on
    its own without already knowing the prior state."""
    db_path = tmp_path / "backfill.db"
    raw = sqlite3.connect(db_path)
    raw.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_number TEXT NOT NULL, "
        "source TEXT NOT NULL, latest_fields_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, UNIQUE (source, ticket_number))"
    )
    raw.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, "
        "ticket_number TEXT, raw_message TEXT NOT NULL, message_type TEXT NOT NULL, "
        "source TEXT NOT NULL, confidence INTEGER, confidence_reasons_json TEXT, "
        "decision_verdict TEXT, decision_reasons_json TEXT, suggested_quo_reply TEXT, "
        "luxor_message TEXT, dispatcher_edits TEXT, final_action TEXT, created_at TEXT NOT NULL, "
        "channel TEXT DEFAULT 'manual', FOREIGN KEY (job_id) REFERENCES jobs (id))"
    )
    raw.execute(
        "INSERT INTO jobs (id, ticket_number, source, latest_fields_json, created_at, updated_at) "
        "VALUES (1, 'REPLAY1', 'AGN', '{}', '2026-07-28T00:00:00+00:00', '2026-07-28T00:00:00+00:00')"
    )
    # NEW+Approve -> Approved, then STATUS+Approve -> In Progress, then
    # CORRECTION+Approve -> preserves "In Progress" (not "Approved", which
    # is what a naive "just look at the one CORRECTION event" derivation
    # would wrongly produce if it ignored history and picked some other
    # default).
    raw.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, "
        "final_action, created_at, channel) VALUES "
        "(1, 1, 'REPLAY1', 'raw', 'NEW', 'AGN', 'Approve', '2026-07-28T00:00:00+00:00', 'manual')"
    )
    raw.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, "
        "final_action, created_at, channel) VALUES "
        "(2, 1, 'REPLAY1', 'raw', 'STATUS', 'AGN', 'Approve', '2026-07-28T01:00:00+00:00', 'manual')"
    )
    raw.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, "
        "final_action, created_at, channel) VALUES "
        "(3, 1, 'REPLAY1', 'raw', 'CORRECTION', 'AGN', 'Approve', '2026-07-28T02:00:00+00:00', 'manual')"
    )
    raw.commit()
    raw.close()

    conn = storage.connect(db_path)
    storage.migrate_schema(conn)  # explicit now -- triggers _add_job_status_columns_and_backfill; connect() no longer auto-migrates an existing db

    row = conn.execute("SELECT status, last_event_id, last_event_at FROM jobs WHERE id = 1").fetchone()
    assert row[0] == "In Progress"
    assert row[1] == 3
    assert row[2] == "2026-07-28T02:00:00+00:00"


def test_backfill_is_idempotent_and_resumable(tmp_path):
    db_path = tmp_path / "backfill2.db"
    conn1 = storage.connect(db_path)
    job = extract_job(load("agn_clean.txt"), load_vehicle_rules())
    classify_result = storage.classify_incoming(conn1, job)
    job_id = storage.save_audit_entry(conn1, job, classify_result, final_action="Approve")
    first_status = conn1.execute("SELECT status, last_event_id FROM jobs WHERE id = ?", (job_id,)).fetchone()

    # Simulate an interrupted backfill: manually clear last_event_id on the
    # already-correct row, as if an earlier backfill pass never finished.
    conn1.execute("UPDATE jobs SET last_event_id = NULL WHERE id = ?", (job_id,))
    conn1.commit()

    conn2 = storage.connect(db_path)
    storage.migrate_schema(conn2)  # explicit re-run of _ensure_schema -- must resume, not skip
    second_status = conn2.execute("SELECT status, last_event_id FROM jobs WHERE id = ?", (job_id,)).fetchone()

    assert second_status[0] == first_status[0]
    assert second_status[1] is not None  # backfill resumed and re-populated it


def test_jobs_status_index_exists(conn):
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(jobs)")}
    assert "idx_jobs_status_last_event_at" in indexes
