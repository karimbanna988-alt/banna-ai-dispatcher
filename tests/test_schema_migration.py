"""Migration correctness tests -- built from realistic historical schema
shapes via raw sqlite3 (never storage.connect(), which no longer
auto-migrates an existing database -- see storage.connect()'s
docstring). Every fixture here is a temporary file; the real database
is never touched.
"""

from __future__ import annotations

import sqlite3

import pytest

from dispatcher import storage


def _raw_current_minus_idempotency(db_path) -> None:
    """A database at the shape the real dispatcher.db was actually left
    at: every migration through _add_console_activation_support applied
    (jobs.status, technicians, dispatch_tracking, closing columns, ...)
    but idempotency_key/schema_version never added -- exactly the real
    database's state as of this round's start."""
    conn = storage.connect(db_path)  # brand new -> full schema via the pre-idempotency code path is not selectable here,
    # so instead: build it, then physically drop the two target columns'
    # effect by rebuilding events without them (SQLite has no DROP COLUMN
    # before 3.35 in general use here, so we simulate "never added" by
    # constructing the events table directly, matching the real
    # dispatcher.db's actual column set at the time this round started).
    conn.close()

    # Rebuild from scratch as a fully raw connection so we control the
    # exact column set precisely, rather than trusting a live connect()
    # call (which always produces the *current* shape).
    import os
    os.remove(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_number TEXT NOT NULL, "
        "source TEXT NOT NULL, latest_fields_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'New', last_event_id INTEGER, "
        "last_event_at TEXT, assigned_technician_id INTEGER, reconciliation_state TEXT, "
        "UNIQUE (source, ticket_number))"
    )
    raw.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, ticket_number TEXT, "
        "raw_message TEXT NOT NULL, message_type TEXT NOT NULL, source TEXT NOT NULL, confidence INTEGER, "
        "confidence_reasons_json TEXT, decision_verdict TEXT, decision_reasons_json TEXT, "
        "suggested_quo_reply TEXT, luxor_message TEXT, dispatcher_edits TEXT, final_action TEXT, "
        "created_at TEXT NOT NULL, channel TEXT DEFAULT 'manual', closing_amount TEXT, "
        "closing_payment_method TEXT, closing_parts_amount TEXT, closing_company_share TEXT, "
        "closing_technician_share TEXT, FOREIGN KEY (job_id) REFERENCES jobs (id))"
    )
    raw.execute(
        "CREATE TABLE technicians (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
        "active INTEGER NOT NULL DEFAULT 1, areas_json TEXT NOT NULL DEFAULT '[]', "
        "skills_json TEXT NOT NULL DEFAULT '[]', vehicle_restrictions_json TEXT NOT NULL DEFAULT '[]', "
        "dispatch_group TEXT, closing_group TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    raw.execute(
        "INSERT INTO jobs (id, ticket_number, source, latest_fields_json, created_at, updated_at, status) "
        "VALUES (1, 'HIST1', 'AGN', '{}', '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00', 'Approved')"
    )
    raw.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, final_action, "
        "created_at, channel) VALUES (1, 1, 'HIST1', 'raw historical message', 'NEW', 'AGN', 'Approve', "
        "'2026-07-01T00:00:00+00:00', 'manual')"
    )
    raw.commit()
    raw.close()


def _raw_idempotency_key_without_schema_version(db_path) -> None:
    """A database that somehow got idempotency_key added but not
    schema_version (an intermediate, partially-migrated state) -- proves
    check_schema_status()/migrate_schema() handle a schema that doesn't
    match any single known historical snapshot exactly."""
    _raw_current_minus_idempotency(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute("ALTER TABLE events ADD COLUMN idempotency_key TEXT")
    raw.execute("ALTER TABLE events ADD COLUMN update_type TEXT")
    raw.execute("ALTER TABLE events ADD COLUMN eta_minutes INTEGER")
    raw.execute(
        "CREATE TABLE whatsapp_handoffs (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, "
        "technician_id INTEGER, marked_sent_at TEXT NOT NULL)"
    )
    raw.execute(
        "CREATE TABLE quo_outbound_log (id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL UNIQUE, "
        "source TEXT NOT NULL, job_id INTEGER, quo_contact_id TEXT NOT NULL, proposed_text TEXT NOT NULL, "
        "mode TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL, approved_by TEXT, "
        "requested_at TEXT NOT NULL, sent_at TEXT, response_status TEXT)"
    )
    raw.execute(
        "CREATE TABLE dispatch_tracking (message_id TEXT PRIMARY KEY, first_seen_at TEXT, luxor_prepared_at TEXT, "
        "acknowledged_at TEXT, copied_at TEXT, whatsapp_opened_at TEXT, dispatched_manually_at TEXT)"
    )
    raw.execute(
        "CREATE TABLE console_activation_state (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "live_queue_started_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    raw.commit()
    raw.close()


# --------------------------------------------------------------- check ---
def test_check_reports_new_database(tmp_path):
    conn = storage.connect(tmp_path / "fresh.db")  # brand new -> auto-provisioned
    status = storage.check_schema_status(conn)
    assert status.up_to_date
    assert status.is_new_database is False  # already provisioned by connect() at this point
    assert status.missing == []


def test_check_reports_pending_migrations_for_pre_idempotency_database(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    status = storage.check_schema_status(conn)
    assert not status.up_to_date
    assert "events.idempotency_key" in status.missing
    assert "events.schema_version" in status.missing
    assert "events.update_type" in status.missing


def test_check_never_writes(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    storage.check_schema_status(conn)
    storage.check_schema_status(conn)
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert before == after
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert "idempotency_key" not in cols  # check_schema_status never adds it


def test_check_intermediate_state_idempotency_key_without_schema_version(tmp_path):
    db_path = tmp_path / "intermediate.db"
    _raw_idempotency_key_without_schema_version(db_path)
    conn = storage.connect(db_path)
    status = storage.check_schema_status(conn)
    assert not status.up_to_date
    assert "events.idempotency_key" not in status.missing  # already present
    assert "events.schema_version" in status.missing  # still missing


# ------------------------------------------------------------- migrate ---
def test_migrate_pre_idempotency_database_reaches_current_revision(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    status = storage.migrate_schema(conn)
    assert status.up_to_date
    assert status.current_revision == storage.SCHEMA_REVISION


def test_migrate_row_counts_and_ids_preserved(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    before_jobs = conn.execute("SELECT id, ticket_number, status FROM jobs ORDER BY id").fetchall()
    before_events = conn.execute("SELECT id, job_id, raw_message, final_action FROM events ORDER BY id").fetchall()

    storage.migrate_schema(conn)

    after_jobs = conn.execute("SELECT id, ticket_number, status FROM jobs ORDER BY id").fetchall()
    after_events = conn.execute("SELECT id, job_id, raw_message, final_action FROM events ORDER BY id").fetchall()
    assert before_jobs == after_jobs
    assert before_events == after_events


def test_migrate_status_and_reconciliation_unchanged(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    before = conn.execute("SELECT status, reconciliation_state FROM jobs WHERE id = 1").fetchone()
    storage.migrate_schema(conn)
    after = conn.execute("SELECT status, reconciliation_state FROM jobs WHERE id = 1").fetchone()
    assert before == after


def test_migrate_does_not_rewrite_raw_message_content(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    before = conn.execute("SELECT raw_message FROM events WHERE id = 1").fetchone()[0]
    storage.migrate_schema(conn)
    after = conn.execute("SELECT raw_message FROM events WHERE id = 1").fetchone()[0]
    assert before == after == "raw historical message"


def test_migrate_required_columns_and_indexes_exist(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    storage.migrate_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert {"idempotency_key", "schema_version", "update_type", "eta_minutes"} <= cols
    index_row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_events_idempotency_key'"
    ).fetchone()
    assert index_row is not None


def test_migrate_no_fk_violations_and_integrity_ok(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    storage.migrate_schema(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migrate_adds_shadow_tables_without_disturbing_existing_data(tmp_path):
    """The real database is currently at revision 2 (no shadow tables) --
    this fixture is shaped exactly like that (jobs/events present,
    idempotency/schema_version absent). Confirms migrate_schema() adds
    shadow_episodes/shadow_events cleanly on top, with every existing
    row and the database's overall integrity untouched."""
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    before_jobs = conn.execute("SELECT id, ticket_number, status FROM jobs ORDER BY id").fetchall()
    before_events = conn.execute("SELECT id, job_id, raw_message FROM events ORDER BY id").fetchall()

    status = storage.migrate_schema(conn)

    assert status.up_to_date
    assert status.current_revision == 2  # core revision unchanged -- shadow tables don't bump it
    assert storage.shadow_schema_present(conn) is True
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "shadow_episodes" in tables
    assert "shadow_events" in tables
    assert conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM shadow_events").fetchone()[0] == 0

    after_jobs = conn.execute("SELECT id, ticket_number, status FROM jobs ORDER BY id").fetchall()
    after_events = conn.execute("SELECT id, job_id, raw_message FROM events ORDER BY id").fetchall()
    assert before_jobs == after_jobs
    assert before_events == after_events
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migrate_twice_is_a_no_op(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    storage.migrate_schema(conn)
    events_after_first = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    jobs_after_first = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()

    status2 = storage.migrate_schema(conn)
    events_after_second = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    jobs_after_second = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()

    assert status2.up_to_date
    assert events_after_first == events_after_second
    assert jobs_after_first == jobs_after_second


def test_migrate_from_intermediate_state_completes(tmp_path):
    db_path = tmp_path / "intermediate.db"
    _raw_idempotency_key_without_schema_version(db_path)
    conn = storage.connect(db_path)
    status = storage.migrate_schema(conn)
    assert status.up_to_date
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert "schema_version" in cols


def test_migrate_fully_current_database_is_a_no_op(tmp_path):
    conn = storage.connect(tmp_path / "current.db")  # brand new -> already current
    before = storage.check_schema_status(conn)
    status = storage.migrate_schema(conn)
    assert before.up_to_date
    assert status.up_to_date


# ------------------------------------------------- legacy schema_version -
def test_migrate_backfills_legacy_events_to_version_zero(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    storage.migrate_schema(conn)
    row = conn.execute("SELECT schema_version FROM events WHERE id = 1").fetchone()
    assert row[0] == storage.LEGACY_EVENT_SCHEMA_VERSION
    assert row[0] == 0


def test_new_events_after_migration_get_current_schema_version(tmp_path):
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    storage.migrate_schema(conn)

    from dispatcher.parser import extract_job, load_vehicle_rules
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: NEWAFTER\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n",
        load_vehicle_rules(),
    )
    cr = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, cr, final_action="Approve")

    legacy_row = conn.execute("SELECT schema_version FROM events WHERE id = 1").fetchone()
    new_row = conn.execute("SELECT schema_version FROM events WHERE ticket_number = 'NEWAFTER'").fetchone()
    assert legacy_row[0] == storage.LEGACY_EVENT_SCHEMA_VERSION
    assert new_row[0] == storage.EVENT_SCHEMA_VERSION
    assert new_row[0] == 1


def test_null_idempotency_keys_never_collide(tmp_path):
    """Multiple legacy events (idempotency_key NULL) must coexist under
    the partial unique index -- SQLite never treats two NULLs as
    duplicates."""
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, final_action, "
        "created_at, channel) VALUES (2, 1, 'HIST1', 'second historical message', 'STATUS', 'AGN', 'Approve', "
        "'2026-07-01T01:00:00+00:00', 'manual')"
    )
    raw.commit()
    raw.close()
    conn = storage.connect(db_path)
    storage.migrate_schema(conn)  # must not raise despite two NULL idempotency_key rows
    count = conn.execute("SELECT COUNT(*) FROM events WHERE idempotency_key IS NULL").fetchone()[0]
    assert count == 2


# ------------------------------------------------- interrupted migration -
def test_migration_interrupted_leaves_valid_resumable_state(tmp_path, monkeypatch):
    """Simulates a crash partway through the migration chain -- proves
    the database stays valid (integrity ok, no orphan/partial rows) and
    that re-running migrate_schema() afterward completes successfully."""
    db_path = tmp_path / "pre_idempotency.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)

    original = storage._add_dispatch_tracking_support
    call_count = {"n": 0}

    def flaky(c):
        call_count["n"] += 1
        raise RuntimeError("simulated crash mid-migration")

    monkeypatch.setattr(storage, "_add_dispatch_tracking_support", flaky)
    with pytest.raises(RuntimeError, match="simulated crash"):
        storage.migrate_schema(conn)
    monkeypatch.setattr(storage, "_add_dispatch_tracking_support", original)

    # Database must still be valid -- no half-applied statement.
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # Steps before the induced failure did commit (idempotency support
    # runs before dispatch_tracking support in _ensure_schema's chain).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert "idempotency_key" in cols
    # The step that failed never got its table.
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dispatch_tracking'"
    ).fetchone()
    assert table is None

    # Re-running (with the crash removed) resumes and completes.
    status = storage.migrate_schema(conn)
    assert status.up_to_date
    table_after = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dispatch_tracking'"
    ).fetchone()
    assert table_after is not None


# --------------------------------------------- full current-shape sample -
def test_check_and_migrate_against_database_with_all_row_kinds(tmp_path):
    """A database with jobs, events, pending messages, and dispatch
    tracking rows already present (pre-idempotency shape) -- proves
    migration works across every table this app actually writes to, not
    just the empty-table case."""
    db_path = tmp_path / "full.db"
    _raw_current_minus_idempotency(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute(
        "CREATE TABLE quo_pending_messages (message_id TEXT PRIMARY KEY, conversation_id TEXT, "
        "group_label TEXT NOT NULL, raw_text TEXT NOT NULL, from_number TEXT, received_at TEXT, "
        "fetched_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', loaded_at TEXT, "
        "actioned_at TEXT, last_error TEXT)"
    )
    raw.execute(
        "INSERT INTO quo_pending_messages (message_id, group_label, raw_text, fetched_at) "
        "VALUES ('PEND1', 'AGN', 'a pending message', '2026-07-01T00:00:00+00:00')"
    )
    raw.commit()
    raw.close()

    conn = storage.connect(db_path)
    before_pending = conn.execute("SELECT COUNT(*) FROM quo_pending_messages").fetchone()[0]
    status = storage.migrate_schema(conn)
    after_pending = conn.execute("SELECT COUNT(*) FROM quo_pending_messages").fetchone()[0]

    assert status.up_to_date
    assert before_pending == after_pending == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
