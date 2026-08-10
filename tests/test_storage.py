import sqlite3
from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.companies import agn
from dispatcher.formatter import format_technician_message
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.rules import decide
from dispatcher.rules.daily import parse_daily_rules
from dispatcher.rules.service_areas import ServiceAreaRecord
from dispatcher.storage import MessageType

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def confirmed_area():
    return ServiceAreaRecord(city="Faketown", state="NC", default_technician_availability="available")


def test_first_message_for_a_ticket_is_new(conn, vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    result = storage.classify_incoming(conn, job)
    assert result.message_type == MessageType.NEW
    assert result.existing_job_id is None


def test_identical_repeat_is_duplicate(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    job_again = extract_job(raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, job_again)
    assert result2.message_type == MessageType.DUPLICATE
    assert result2.existing_job_id == storage.classify_incoming(conn, job_again).existing_job_id


def test_conflicting_field_is_a_correction(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    corrected_raw = raw.replace("100 Fake St , Faketown, NC, 27500", "999 Other Rd , Faketown, NC, 27500")
    corrected_job = extract_job(corrected_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, corrected_job)
    assert result2.message_type == MessageType.CORRECTION
    assert "address" in result2.conflicting_fields


def test_closing_message_is_classified_as_closing(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    closing_raw = raw + "\nClosed\n$500 CC\n"
    closing_job = extract_job(closing_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, closing_job)
    assert result2.message_type == MessageType.CLOSING


def test_closing_with_no_prior_dispatch_is_anomalous_not_new(conn, vehicle_rules):
    """A ticket is always dispatched before it can be closed -- a
    closing-looking message with no prior record for its ticket must never
    be treated as a normal new job."""
    raw = load("agn_clean.txt") + "\nClosed\n$500 CC\n"
    job = extract_job(raw, vehicle_rules)
    result = storage.classify_incoming(conn, job)
    assert result.message_type == MessageType.CLOSING_WITHOUT_DISPATCH
    assert result.existing_job_id is None
    assert "handle manually" in result.reason.lower()


def test_cancellation_message_is_classified(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    cancel_raw = raw + "\nCustomer called to cancel\n"
    cancel_job = extract_job(cancel_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, cancel_job)
    assert result2.message_type == MessageType.CANCELLATION


# --------------------------------- Regression: reworded cancellations -----
# Confirmed 2026-07-29: closes the documented gap in docs/architecture.md
# section 6 -- a cancellation that never says "cancel" used to fall through
# to UNRECOGNIZED. Conservative, deterministic pattern matching only (see
# storage._is_confident_cancellation) -- no LLM, no probabilistic
# classification. Anything not explicitly matched still falls through to
# UNRECOGNIZED for manual review, exactly as before.

def _classify_followup(conn, vehicle_rules, followup_text):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    followup_raw = raw + "\n" + followup_text + "\n"
    followup_job = extract_job(followup_raw, vehicle_rules)
    return storage.classify_incoming(conn, followup_job)


@pytest.mark.parametrize("text", [
    "Customer no longer needs service",
    "No longer needs the service",
    "Customer found her keys",
    "Customer found his keys",
    "They found the keys",
    "Will call back later, hung up",
    "Customer does not need us anymore",
])
def test_reworded_cancellation_is_classified(conn, vehicle_rules, text):
    result = _classify_followup(conn, vehicle_rules, text)
    assert result.message_type == MessageType.CANCELLATION


@pytest.mark.parametrize("text", [
    "CUSTOMER NO LONGER NEEDS SERVICE",
    "customer no longer needs service.",
    "Customer, no longer needs service!",
    "customer found HER keys!!",
    "They found the keys.",
    "will call back later hung up",  # no comma
    "Will call back later,   hung up",  # extra spaces
])
def test_reworded_cancellation_capitalization_and_punctuation_variations(conn, vehicle_rules, text):
    result = _classify_followup(conn, vehicle_rules, text)
    assert result.message_type == MessageType.CANCELLATION


@pytest.mark.parametrize("text", [
    "Customer called to cancel",
    "Please cancel this job",
    "Job canceled",
    "Job cancelled",
    "Cancelled by customer",
])
def test_existing_literal_cancel_behavior_is_preserved(conn, vehicle_rules, text):
    result = _classify_followup(conn, vehicle_rules, text)
    assert result.message_type == MessageType.CANCELLATION


@pytest.mark.parametrize("text", [
    "Customer may cancel",
    "Asked whether cancellation is possible",
    "Technician found the keys",
    "Customer will call back with the address",
])
def test_reworded_cancellation_false_positive_protections(conn, vehicle_rules, text):
    """These must stay UNRECOGNIZED (manual review), not be silently
    misclassified as an actual cancellation -- a hedged/hypothetical or
    interrogative mention of cancellation, a technician (not customer)
    finding keys, and a callback that isn't the specific "later ... hung
    up" shape are all deliberately excluded."""
    result = _classify_followup(conn, vehicle_rules, text)
    assert result.message_type == MessageType.UNRECOGNIZED


# ------------------------------------- Regression: hardening 2026-07-29 ---
# The original hedge guard only caught a modal immediately adjacent to
# "cancel" ("may cancel"), missing "may need to cancel" (words between).
# The "found keys" pattern also didn't account for a continuation showing
# the underlying need is still there ("found her keys but still needs the
# lock repaired"). Both were caught and fixed same-day, before any real
# technician/dispatch data existed.

@pytest.mark.parametrize("text", [
    "Customer does not need us anymore",
    "Customer does not need us any more",
])
def test_hardening_confirms_positive_cases_still_classify(conn, vehicle_rules, text):
    result = _classify_followup(conn, vehicle_rules, text)
    assert result.message_type == MessageType.CANCELLATION


@pytest.mark.parametrize("text", [
    "Customer may need to cancel",
    "Customer might have to cancel",
    "Customer could decide to cancel",
    "Customer found her keys but still needs the lock repaired",
    "Customer found his keys and now needs a duplicate made",
    "Customer found the keys, but the ignition still needs service",
])
def test_hardening_catches_wider_hedges_and_still_needs_continuations(conn, vehicle_rules, text):
    """Before the 2026-07-29 hardening, all six of these were wrongly
    classified as CANCELLATION -- a modal separated from "cancel" by more
    than one word, or a "found keys" message that goes on to say the
    underlying need is still there, must stay UNRECOGNIZED for manual
    review instead."""
    result = _classify_followup(conn, vehicle_rules, text)
    assert result.message_type == MessageType.UNRECOGNIZED


def test_status_message_is_classified(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    status_job = extract_job("Going", vehicle_rules)
    status_job.ticket_number = job.ticket_number  # same ticket, freeform status text
    # A status update tied to this ticket is understood via which
    # company's thread it arrived in -- same source as the original job,
    # matching how app.py's Status Update tab already reuses the original
    # job's source rather than a blind ticket_number-only match (ticket
    # numbers are only unique WITHIN one source, not globally).
    status_job.source = job.source
    result2 = storage.classify_incoming(conn, status_job)
    assert result2.message_type == MessageType.STATUS


def test_no_ticket_is_always_new(conn, vehicle_rules):
    job = extract_job(load("unknown_source.txt"), vehicle_rules)
    result = storage.classify_incoming(conn, job)
    assert result.message_type == MessageType.NEW
    assert result.existing_job_id is None


def test_original_message_is_never_overwritten(conn, vehicle_rules):
    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result1, final_action="Approve")

    corrected_raw = raw.replace("100 Fake St , Faketown, NC, 27500", "999 Other Rd , Faketown, NC, 27500")
    corrected_job = extract_job(corrected_raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, corrected_job)
    storage.save_audit_entry(conn, corrected_job, result2, final_action="Approve")

    timeline = storage.get_audit_timeline(conn, job.ticket_number.value)
    assert len(timeline) == 2
    assert timeline[0]["raw_message"] == raw
    assert timeline[1]["raw_message"] == corrected_raw


def test_full_pipeline_writes_a_complete_audit_entry(conn, vehicle_rules):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    raw = load("agn_clean.txt")
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    now = datetime(2026, 1, 7, 12, 0, 0)  # Wednesday
    business_now = now.replace(tzinfo=ZoneInfo("America/New_York"))
    daily = parse_daily_rules("Today:\n- Georgia open\n", now=now)
    decision = decide(job, agn.PROFILE, [confirmed_area()], daily, now=now, business_now=business_now)
    luxor_message = format_technician_message(job)

    job_id = storage.save_audit_entry(
        conn, job, classify_result, decision=decision,
        suggested_quo_reply=decision.quo_reply_suggestion,
        luxor_message=luxor_message, final_action="Approve",
    )
    timeline = storage.get_audit_timeline(conn, job.ticket_number.value)
    assert len(timeline) == 1
    entry = timeline[0]
    assert entry["raw_message"] == raw
    assert entry["decision_verdict"] == "ACCEPT"
    assert entry["luxor_message"].startswith("LUXOR LOCKSMITH")
    assert entry["final_action"] == "Approve"
    assert job_id is not None


# ------------------------------------------------- Regression: crash fix ---
# Confirmed 2026-07-27/28: jobs.ticket_number was globally UNIQUE (not
# scoped per source), and save_audit_entry() trusted a caller-supplied
# existing_job_id that could go stale (e.g. the same loaded job finalized
# twice). Both crashed with sqlite3.IntegrityError: UNIQUE constraint
# failed: jobs.ticket_number, and the failure happened mid-INSERT, so the
# events row -- the audit trail of what the dispatcher actually did -- was
# lost too. These tests lock in that none of that can happen again.

def test_same_message_processed_twice_does_not_crash(conn, vehicle_rules):
    raw = load("agn_clean.txt")

    job1 = extract_job(raw, vehicle_rules)
    result1 = storage.classify_incoming(conn, job1)
    storage.save_audit_entry(conn, job1, result1, final_action="Approve")

    # Reprocess the exact same raw text from scratch, as if the same Quo
    # message were re-reviewed or re-delivered.
    job2 = extract_job(raw, vehicle_rules)
    result2 = storage.classify_incoming(conn, job2)
    assert result2.message_type == MessageType.DUPLICATE
    storage.save_audit_entry(conn, job2, result2, final_action="Approve")  # must not raise

    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE ticket_number = ?", (job1.ticket_number.value,)).fetchone()[0] == 1
    timeline = storage.get_audit_timeline(conn, job1.ticket_number.value)
    assert len(timeline) == 2  # both attempts preserved in the append-only log


def test_same_ticket_number_from_different_sources_does_not_collide(conn, vehicle_rules):
    agn_job = extract_job(load("agn_clean.txt"), vehicle_rules)
    lucky_job = extract_job(load("lucky_clean.txt"), vehicle_rules)
    # Force an artificial collision: two different companies' own,
    # independently-generated ticket numbering happening to coincide.
    shared_ticket = "SHARED123"
    agn_job.ticket_number.value = shared_ticket
    lucky_job.ticket_number.value = shared_ticket
    assert agn_job.source != lucky_job.source

    result_a = storage.classify_incoming(conn, agn_job)
    storage.save_audit_entry(conn, agn_job, result_a, final_action="Approve")

    result_l = storage.classify_incoming(conn, lucky_job)
    assert result_l.message_type == MessageType.NEW  # not mistaken for the AGN job
    storage.save_audit_entry(conn, lucky_job, result_l, final_action="Approve")  # must not raise

    rows = conn.execute(
        "SELECT source FROM jobs WHERE ticket_number = ? ORDER BY source", (shared_ticket,)
    ).fetchall()
    assert [r[0] for r in rows] == ["AGN", "Lucky"]


def test_repeated_handle_manually_on_the_same_loaded_job_does_not_crash(conn, vehicle_rules):
    """Reproduces the actual reported crash: the review screen doesn't
    clear itself after a save, and _finalize() never recomputes
    classify_result -- so clicking the same action button again (a
    double-click, or re-clicking after already handling it) reuses the
    same, now-stale ClassifyResult object."""
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    assert classify_result.existing_job_id is None  # first time, brand new

    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")
    # Re-finalize using the exact same (now stale) classify_result object,
    # simulating a second click without ever re-running classify_incoming.
    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")  # must not raise

    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE ticket_number = ?", (job.ticket_number.value,)).fetchone()[0] == 1


def test_audit_log_preserves_every_repeated_action(conn, vehicle_rules):
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)

    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")
    storage.save_audit_entry(conn, job, classify_result, final_action="Handle Manually")
    storage.save_audit_entry(conn, job, classify_result, final_action="Approve")

    timeline = storage.get_audit_timeline(conn, job.ticket_number.value)
    assert [e["final_action"] for e in timeline] == ["Handle Manually", "Handle Manually", "Approve"]
    # jobs stays a single, current-state row throughout
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE ticket_number = ?", (job.ticket_number.value,)).fetchone()[0] == 1


# ------------------------------------------ Regression: events FK repair ---
# Confirmed 2026-07-28: a real database migrated by the earlier
# _migrate_jobs_to_source_scoped_unique (renaming jobs away and back) ended
# up with events.job_id's FOREIGN KEY clause literally pointing at
# "jobs_pre_source_scoped_unique_backup" instead of "jobs" -- SQLite
# rewrites other tables' FK clauses to follow a renamed table. These tests
# build that exact drifted schema by hand (never through storage.connect(),
# so the repair hasn't run yet), then confirm storage.connect() repairs it
# safely, preserving every row, without repeating the same rename-old-first
# mistake.

def _build_drifted_pre_repair_db(path: Path) -> None:
    """Hand-build a database in the exact drifted, pre-repair shape found
    in production: jobs uses the current (correct) schema, but events'
    persisted FOREIGN KEY clause still names the old backup table."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_number TEXT NOT NULL,
            source TEXT NOT NULL,
            latest_fields_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (source, ticket_number)
        )
        """
    )
    conn.execute(
        "CREATE TABLE jobs_pre_source_scoped_unique_backup ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_number TEXT, source TEXT, "
        "latest_fields_json TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            ticket_number TEXT,
            raw_message TEXT NOT NULL,
            message_type TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence INTEGER,
            confidence_reasons_json TEXT,
            decision_verdict TEXT,
            decision_reasons_json TEXT,
            suggested_quo_reply TEXT,
            luxor_message TEXT,
            dispatcher_edits TEXT,
            final_action TEXT,
            created_at TEXT NOT NULL, channel TEXT DEFAULT 'manual',
            FOREIGN KEY (job_id) REFERENCES "jobs_pre_source_scoped_unique_backup" (id)
        )
        """
    )
    conn.execute(
        "INSERT INTO jobs (id, ticket_number, source, latest_fields_json, created_at, updated_at) "
        "VALUES (1, 'DRIFT1', 'AGN', '{}', '2026-07-28T00:00:00+00:00', '2026-07-28T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO jobs (id, ticket_number, source, latest_fields_json, created_at, updated_at) "
        "VALUES (2, 'DRIFT2', 'Lucky', '{}', '2026-07-28T00:00:00+00:00', '2026-07-28T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, "
        "final_action, created_at, channel) VALUES "
        "(1, 1, 'DRIFT1', 'raw one', 'NEW', 'AGN', 'Approve', '2026-07-28T00:00:00+00:00', 'manual')"
    )
    conn.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, "
        "final_action, created_at, channel) VALUES "
        "(2, 2, 'DRIFT2', 'raw two', 'NEW', 'Lucky', 'Handle Manually', '2026-07-28T00:00:00+00:00', 'manual')"
    )
    conn.commit()
    conn.close()


def test_events_fk_repair_corrects_the_drifted_reference(tmp_path):
    db_path = tmp_path / "drifted.db"
    _build_drifted_pre_repair_db(db_path)

    conn = storage.connect(db_path)
    storage.migrate_schema(conn)  # connect() no longer auto-migrates an existing db -- see storage.connect()'s docstring

    schema_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0]
    assert "jobs_pre_source_scoped_unique_backup" not in schema_sql
    assert "REFERENCES jobs (id)" in schema_sql


def test_events_fk_repair_preserves_every_row_and_id(tmp_path):
    db_path = tmp_path / "drifted.db"
    _build_drifted_pre_repair_db(db_path)

    conn = storage.connect(db_path)
    storage.migrate_schema(conn)

    rows = conn.execute(
        "SELECT id, job_id, ticket_number, raw_message, final_action, channel FROM events ORDER BY id"
    ).fetchall()
    assert rows == [
        (1, 1, "DRIFT1", "raw one", "Approve", "manual"),
        (2, 2, "DRIFT2", "raw two", "Handle Manually", "manual"),
    ]


def test_events_fk_repair_is_idempotent(tmp_path):
    db_path = tmp_path / "drifted.db"
    _build_drifted_pre_repair_db(db_path)

    conn1 = storage.connect(db_path)
    storage.migrate_schema(conn1)
    first_sql = conn1.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0]

    conn2 = storage.connect(db_path)
    storage.migrate_schema(conn2)  # re-run migration against the already-repaired file
    second_sql = conn2.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0]

    assert first_sql == second_sql
    assert conn2.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    # No extra live backup table was created for this migration -- the
    # file-level backup taken before running it is the recovery path.
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "events_fk_repair_new" not in tables
    assert "events_pre_fk_repair_backup" not in tables
    # The pre-existing jobs backup table from the earlier migration is left
    # untouched, not deleted, by this unrelated repair.
    assert "jobs_pre_source_scoped_unique_backup" in tables


def test_events_fk_repair_passes_foreign_key_check_and_integrity_check(tmp_path):
    db_path = tmp_path / "drifted.db"
    _build_drifted_pre_repair_db(db_path)

    conn = storage.connect(db_path)
    storage.migrate_schema(conn)

    assert conn.execute("PRAGMA foreign_key_check(events)").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_foreign_keys_enforcement_is_enabled_after_repair_on_clean_data(tmp_path):
    db_path = tmp_path / "drifted.db"
    _build_drifted_pre_repair_db(db_path)

    conn = storage.connect(db_path)
    storage.migrate_schema(conn)

    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO events (job_id, ticket_number, raw_message, message_type, source, created_at) "
            "VALUES (9999, 'NOPE', 'raw', 'NEW', 'AGN', '2026-07-28T00:00:00+00:00')"
        )


def test_foreign_keys_enforcement_is_on_for_a_brand_new_database(tmp_path):
    conn = storage.connect(tmp_path / "fresh.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_fk_repair_refuses_when_a_view_references_events(tmp_path):
    """Safety guard: never rename/drop the table out from under a view or
    trigger that still names it -- this is exactly the class of bug being
    fixed here, applied to *this* migration's own safety."""
    db_path = tmp_path / "drifted_with_view.db"
    _build_drifted_pre_repair_db(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute("CREATE VIEW events_summary AS SELECT id, message_type FROM events")
    raw.commit()
    raw.close()

    conn = storage.connect(db_path)
    with pytest.raises(RuntimeError, match="events_summary"):
        storage.migrate_schema(conn)


def test_fk_repair_aborts_and_reports_orphans_without_enabling_enforcement(tmp_path):
    """If an orphan job_id somehow exists, the repair must abort rather
    than silently drop/rewrite data, and foreign_keys enforcement must not
    be turned on for connections against still-dirty data."""
    db_path = tmp_path / "drifted_with_orphan.db"
    _build_drifted_pre_repair_db(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute(
        "INSERT INTO events (id, job_id, ticket_number, raw_message, message_type, source, "
        "final_action, created_at, channel) VALUES "
        "(3, 9999, 'ORPHAN', 'raw orphan', 'NEW', 'AGN', 'Approve', '2026-07-28T00:00:00+00:00', 'manual')"
    )
    raw.commit()
    raw.close()

    conn = storage.connect(db_path)
    with pytest.raises(RuntimeError, match="orphan"):
        storage.migrate_schema(conn)

    # The original (unrepaired) table must be untouched -- verified via a
    # fresh raw connection, bypassing storage.connect() entirely so this
    # check can't accidentally re-trigger/hide behind the migration itself.
    raw2 = sqlite3.connect(db_path)
    assert raw2.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    schema_sql = raw2.execute("SELECT sql FROM sqlite_master WHERE name = 'events'").fetchone()[0]
    assert "jobs_pre_source_scoped_unique_backup" in schema_sql  # still drifted, repair rolled back


def test_whatsapp_registry_schema_present_true_on_fresh_database(conn):
    assert storage.whatsapp_registry_schema_present(conn) is True


def test_whatsapp_registry_schema_present_false_when_tables_missing(conn):
    conn.execute("DROP TABLE whatsapp_group_registry")
    conn.execute("DROP TABLE whatsapp_outgoing_audit")
    assert storage.whatsapp_registry_schema_present(conn) is False


def test_missing_whatsapp_registry_tables_never_fails_the_blanket_schema_check(conn):
    """Same optional-capability guarantee as Shadow Mode: a database
    missing only the WhatsApp registry tables must still report
    core-ready."""
    conn.execute("DROP TABLE whatsapp_group_registry")
    conn.execute("DROP TABLE whatsapp_outgoing_audit")
    status = storage.check_schema_status(conn)
    assert status.up_to_date
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_REVISION


# ======================================================= WHATSAPP GROUP REGISTRY

def test_registry_upsert_is_idempotent_by_group_id(conn):
    entry = {"group_id": "111@g.us", "exact_name": "Sebai Augusta Dispatch", "role": "TECH_DISPATCH",
              "company": None, "service_area": "Augusta", "technician_id": None,
              "is_active": True, "confirmed_by_owner": True, "source": "owner_manual"}
    storage.upsert_group_registry_entry(conn, entry)
    storage.upsert_group_registry_entry(conn, entry)
    rows = storage.list_group_registry(conn)
    assert len(rows) == 1
    assert rows[0]["role"] == "TECH_DISPATCH"
    assert rows[0]["confirmed_by_owner"] is True


def test_registry_rejects_an_unknown_role(conn):
    with pytest.raises(ValueError):
        storage.upsert_group_registry_entry(conn, {"group_id": "x@g.us", "exact_name": "x", "role": "NOT_A_ROLE"})


def test_registry_upsert_updates_existing_row_in_place(conn):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "Old Name", "role": "UNMAPPED_WORK",
        "confirmed_by_owner": False, "is_active": True,
    })
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "Sebai Augusta Dispatch", "role": "TECH_DISPATCH",
        "confirmed_by_owner": True, "is_active": True,
    })
    rows = storage.list_group_registry(conn)
    assert len(rows) == 1
    assert rows[0]["exact_name"] == "Sebai Augusta Dispatch"
    assert rows[0]["role"] == "TECH_DISPATCH"


def test_list_group_registry_active_only_filter(conn):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "A", "role": "TECH_DISPATCH", "is_active": True,
    })
    storage.upsert_group_registry_entry(conn, {
        "group_id": "222@g.us", "exact_name": "B", "role": "IGNORE", "is_active": False,
    })
    assert len(storage.list_group_registry(conn)) == 2
    active = storage.list_group_registry(conn, active_only=True)
    assert len(active) == 1
    assert active[0]["group_id"] == "111@g.us"


# ================================ PENDING OBSERVATIONS + REGISTRY ENRICHMENT

def _seed_pending_observation(conn, **overrides):
    obs = {
        "message_id": "wamid.1", "group_id": "111@g.us", "group_name": "Sebai Augusta Dispatch",
        "sender": "1555", "direction": "incoming", "timestamp": "1700000000",
        "text": "k on the way", "extracted_ticket": None, "quoted_message_id": None,
    }
    obs.update(overrides)
    storage.add_whatsapp_observation(conn, obs)


def test_pending_observation_from_a_confirmed_registry_group_shows_confirmed_mapping(conn):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "Sebai Augusta Dispatch", "role": "TECH_DISPATCH",
        "service_area": "Augusta", "confirmed_by_owner": True, "is_active": True, "source": "owner_manual",
    })
    _seed_pending_observation(conn)
    rows = storage.list_whatsapp_pending_observations(conn)
    assert len(rows) == 1
    assert rows[0]["group_role"] == "TECH_DISPATCH"
    assert rows[0]["group_mapping_status"] == "CONFIRMED"
    assert rows[0]["group_service_area"] == "Augusta"


def test_pending_observation_from_an_unconfirmed_registry_group_shows_human_review(conn):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "222@g.us", "exact_name": "Some Dispatch Group", "role": "UNMAPPED_WORK",
        "confirmed_by_owner": False, "is_active": True, "source": "name_heuristic",
        "notes": "offline guess: technician dispatch group",
    })
    _seed_pending_observation(conn, message_id="wamid.2", group_id="222@g.us", group_name="Some Dispatch Group")
    rows = storage.list_whatsapp_pending_observations(conn)
    assert rows[0]["group_role"] == "UNMAPPED_WORK"
    assert rows[0]["group_mapping_status"] == "HUMAN_REVIEW"
    assert rows[0]["group_offline_notes"] == "offline guess: technician dispatch group"


def test_pending_observation_from_a_group_with_no_registry_row_fails_safe_to_human_review(conn):
    # Should not happen in production (the bridge only forwards allowlisted
    # groups) but the read path must not crash or silently omit review.
    _seed_pending_observation(conn, message_id="wamid.3", group_id="333@g.us", group_name="Unregistered Group")
    rows = storage.list_whatsapp_pending_observations(conn)
    assert rows[0]["group_role"] == "UNMAPPED_WORK"
    assert rows[0]["group_mapping_status"] == "HUMAN_REVIEW"


def test_correcting_a_registry_role_later_updates_display_for_already_stored_observations(conn):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "X", "role": "UNMAPPED_WORK",
        "confirmed_by_owner": False, "is_active": True,
    })
    _seed_pending_observation(conn)
    assert storage.list_whatsapp_pending_observations(conn)[0]["group_mapping_status"] == "HUMAN_REVIEW"

    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "Sebai Augusta Dispatch", "role": "TECH_DISPATCH",
        "confirmed_by_owner": True, "is_active": True,
    })
    assert storage.list_whatsapp_pending_observations(conn)[0]["group_mapping_status"] == "CONFIRMED"


# ============================================================ OUTGOING AUDIT

def test_outgoing_audit_is_stored_in_its_own_table_never_in_pending_observations(conn):
    storage.add_whatsapp_outgoing_audit(conn, {
        "message_id": "wamid.out.1", "group_id": "111@g.us", "group_name": "Sebai Augusta Dispatch",
        "timestamp": "1700000000", "text": "on my way",
    })
    assert storage.list_whatsapp_outgoing_audit(conn) != []
    # The actionable queue must never see this -- structurally a different
    # table with no code path connecting the two.
    assert storage.list_whatsapp_pending_observations(conn, include_resolved=True) == []


def test_outgoing_audit_dedupes_by_message_id(conn):
    entry = {"message_id": "wamid.out.1", "group_id": "111@g.us", "group_name": "G", "text": "k"}
    first = storage.add_whatsapp_outgoing_audit(conn, entry)
    second = storage.add_whatsapp_outgoing_audit(conn, entry)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(storage.list_whatsapp_outgoing_audit(conn)) == 1
