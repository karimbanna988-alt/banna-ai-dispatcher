"""SQLite job history and append-only audit log.

Two tables:
- `jobs`: one row per ticket/PDL, holding only the latest known field
  snapshot (a convenience cache for comparison -- not the source of truth).
- `events`: append-only. Every processed message becomes a new row here and
  existing rows are never updated or deleted, so the original raw message
  and every past decision/edit stay intact forever, even when a later
  message corrects or updates the same ticket.

See docs/architecture.md section 3 for the data contracts this implements.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

from .action_names import TECHNICIAN_UPDATE_TYPES, ActionName
from .clock import Clock, SystemClock, now_iso
from .models import ExtractedJob
from .parser import REQUIRED_FIELDS
from .rules import Decision
from .rules.service_areas import ServiceAreaRecord, find_service_area
from .shadow import EXPORTABLE_REVIEW_STATES, SHADOW_EPISODE_SCHEMA_VERSION, ReviewStatus, ShadowChannel, ShadowEventType
from .rules.service_categories import KNOWN_SERVICE_CATEGORIES, classify_service_categories, normalize_skill_label
from .updates import map_status_to_reply

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "dispatcher.db"

# Bumped only when the *shape* of an events row changes in a way future
# code needs to distinguish from older rows (new required interpretation
# of an existing column, a column repurposed, etc.) -- not for every
# additive, backward-compatible column (those need no version bump,
# since old readers already tolerate an extra NULL column). See
# docs/EVENT_CONTRACT.md.
#
# EVENT_SCHEMA_VERSION 0 is reserved for events written before this
# column existed (backfilled explicitly by migrate_schema(), never by
# DEFAULT -- see _add_idempotency_and_schema_version_support()'s
# docstring): those rows never had a chance to be deduplicated via
# idempotency_key, so calling them "1" would overstate what the system
# actually guaranteed when they were written. Only rows inserted by
# save_audit_entry()/the operational job actions AFTER this column
# existed are stamped 1.
EVENT_SCHEMA_VERSION = 1
LEGACY_EVENT_SCHEMA_VERSION = 0

# The current CORE target database schema revision, tracked via
# SQLite's own PRAGMA user_version (never a second migration-tracking
# table -- see docs/SCHEMA_MIGRATION_RUNBOOK.md for why one isn't needed
# here). Bumped whenever a new migration function is added to
# _ensure_schema() that the EXISTING dispatcher workflow depends on.
#
# Shadow Mode's tables (shadow_episodes/shadow_events, see
# _add_shadow_mode_support() and docs/SHADOW_MODE.md) are deliberately
# NOT counted here and do NOT bump this number -- they are an optional
# capability layered on top of core revision 2, checked independently
# via shadow_schema_present(). A database missing only the shadow
# tables must still report core-ready (current_revision >= 2, nothing
# in _SCHEMA_MARKERS missing) -- see
# tests/test_shadow_persistence.py::test_missing_shadow_tables_never_fails_the_blanket_schema_check.
# _ensure_schema() still creates the shadow tables as a harmless,
# purely-additive side effect of any core migration (or a brand-new
# database), so no second migration script is needed -- running the
# existing `python scripts/manage_schema.py migrate` picks up both.
SCHEMA_REVISION = 2

# SQLite's own busy-wait, in milliseconds, before a writer blocked by
# another writer's transaction gives up and raises "database is
# locked" -- see docs/SQLITE_CONNECTION_POLICY.md. Zero (SQLite's
# default) means an immediate failure on any contention at all; this
# app has at most two local processes (the Streamlit UI and the
# optional standalone poller) ever writing to the same file, so a
# short bounded wait is enough to absorb that, without masking a
# genuinely stuck writer forever.
BUSY_TIMEOUT_MS = 5000


class MessageType(str, Enum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"
    CORRECTION = "CORRECTION"
    UPDATE_REQUEST = "UPDATE_REQUEST"
    STATUS = "STATUS"
    CLOSING = "CLOSING"
    CANCELLATION = "CANCELLATION"
    CLOSING_WITHOUT_DISPATCH = "CLOSING_WITHOUT_DISPATCH"
    UNRECOGNIZED = "UNRECOGNIZED"
    # Dispatcher-initiated (not derived from an inbound message classification):
    # a technician was assigned, reassigned, or removed for this job. See
    # assign_technician() and derive_job_status().
    ASSIGNMENT = "ASSIGNMENT"
    # Dispatcher-confirmed administrative fact about an already-established
    # job -- a company update/closing was prepared or sent manually, or the
    # job was completed. Always preserves the job's existing status (see
    # derive_job_status()) -- exactly like ASSIGNMENT, these never move the
    # operational lifecycle on their own. See record_technician_update()
    # and friends below.
    OPERATIONAL_NOTE = "OPERATIONAL_NOTE"


@dataclass
class ClassifyResult:
    message_type: MessageType
    existing_job_id: Optional[int]
    conflicting_fields: List[str] = field(default_factory=list)
    reason: str = ""


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Opens a connection for normal application reads/writes.

    Does NOT apply schema migrations to a pre-existing database -- see
    migrate_schema() / scripts/manage_schema.py. Rendering a page,
    logging in, refreshing, or any other read must never be the trigger
    for a production schema change.

    The one deliberate exception: a database file with no `jobs` table
    at all is unambiguously brand new (every test fixture's
    tmp_path/"test.db" hits this, and so does a genuine first-ever run)
    -- there is no existing data a migration could put at risk, so it is
    provisioned with the full current schema immediately. An existing
    database that merely lacks newer columns/tables is left exactly as
    it is; callers must run the explicit migration command first (see
    docs/SCHEMA_MIGRATION_RUNBOOK.md). Use check_schema_status(conn) to
    detect this before attempting any write that depends on a newer
    column."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the Streamlit UI reruns the script on a fresh
    # thread each time while reusing one cached connection (st.cache_resource
    # in app.py) -- this is a single local dispatcher session, not concurrent
    # multi-writer access, so relaxing SQLite's default thread affinity here
    # is safe.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if _is_brand_new_database(conn):
        _ensure_schema(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_REVISION}")
        conn.commit()
    _configure_foreign_key_enforcement(conn)
    return conn


def _is_brand_new_database(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    return row is None


# (table, column) pairs that must all be present for the database to be
# considered fully migrated to SCHEMA_REVISION. `column=None` means "the
# table itself must exist". This list is intentionally the full current
# target shape, not a step-by-step replay of every historical migration
# -- in practice a real database that has ever been opened by an older
# version of this app already has everything except the most recent
# addition(s), and a from-scratch database goes through the same
# idempotent _ensure_schema() chain regardless, so there is no need for
# a second, independently-maintained migration-tracking table (a
# genuine _new_ requirement would add one more line here, not a new
# subsystem).
_SCHEMA_MARKERS: tuple = (
    ("jobs", "status"),
    ("jobs", "assigned_technician_id"),
    ("jobs", "reconciliation_state"),
    ("events", "channel"),
    ("events", "closing_amount"),
    ("events", "update_type"),
    ("events", "eta_minutes"),
    ("events", "idempotency_key"),
    ("events", "schema_version"),
    ("technicians", "dispatch_group"),
    ("technicians", "closing_group"),
    ("whatsapp_handoffs", None),
    ("quo_outbound_log", None),
    ("dispatch_tracking", None),
    ("console_activation_state", None),
    ("quo_pending_messages", None),
    ("whatsapp_pending_observations", None),
)

# Deliberately NOT part of _SCHEMA_MARKERS/check_schema_status(): those
# gate every existing write-capable page (dispatch/active_jobs/closings)
# via require_current_schema(), and none of that existing functionality
# depends on shadow_episodes/shadow_events. Folding the shadow tables
# into that same blanket check would make the entire, already-working
# dispatch workflow report "Database upgrade required" the moment this
# code ships, even before anyone touches Shadow Mode -- a regression to
# functionality that has nothing to do with this feature. Shadow Mode's
# own UI sections check shadow_schema_present() independently instead,
# so only Shadow Mode itself degrades gracefully until migrated.


@dataclass(frozen=True)
class SchemaStatus:
    """Read-only snapshot of a database's migration state -- never
    produced by a function that also writes. `missing` lists
    human-readable marker names (table.column, or table for a whole
    missing table) for any operator/log message; never customer data."""

    current_revision: int
    required_revision: int
    is_new_database: bool
    missing: List[str]

    @property
    def up_to_date(self) -> bool:
        return not self.missing and (self.is_new_database or self.current_revision >= self.required_revision)


def check_schema_status(conn: sqlite3.Connection) -> SchemaStatus:
    """Read-only: never calls _ensure_schema(), never writes anything,
    safe to call on a connection opened strictly read-only (SQLite
    `mode=ro`). Callers use this to decide whether to permit
    state-changing actions -- see luxor_desk_shared.require_current_schema()."""
    if _is_brand_new_database(conn):
        return SchemaStatus(current_revision=0, required_revision=SCHEMA_REVISION, is_new_database=True, missing=[])

    missing: List[str] = []
    for table, column in _SCHEMA_MARKERS:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if table_exists is None:
            missing.append(f"{table} (table)")
            continue
        if column is not None:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                missing.append(f"{table}.{column}")
    index_row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_events_idempotency_key'"
    ).fetchone()
    if index_row is None:
        missing.append("idx_events_idempotency_key (unique index)")

    current_revision = conn.execute("PRAGMA user_version").fetchone()[0]
    return SchemaStatus(
        current_revision=current_revision, required_revision=SCHEMA_REVISION,
        is_new_database=False, missing=missing,
    )


def migrate_schema(conn: sqlite3.Connection) -> SchemaStatus:
    """The ONLY function in this module permitted to run _ensure_schema()
    against a database that might already contain real rows. Never
    called by connect(); only by scripts/manage_schema.py (which is
    responsible for taking a backup first) or a test that explicitly
    wants a migrated temporary database.

    Idempotent: safe to run any number of times, including immediately
    after a prior run that failed partway through. Every step inside
    _ensure_schema() is independently idempotent (each checks "is this
    already applied?" before acting) and each step's own DDL is atomic
    at the SQLite level, so a failure on step N never leaves step N
    half-applied -- it leaves steps 1..N-1 committed and N..end simply
    not yet attempted, a safe, resumable, always-idempotent-to-retry
    state. This is NOT the same as one all-or-nothing transaction
    spanning the entire chain (see docs/SCHEMA_MIGRATION_RUNBOOK.md for
    why that stronger guarantee isn't offered, and isn't needed given
    per-step idempotency).

    Also backfills LEGACY_EVENT_SCHEMA_VERSION (0) onto every events row
    that existed before this migration added schema_version -- see
    EVENT_SCHEMA_VERSION's own docstring for why legacy rows are never
    silently relabeled as version 1."""
    was_new = _is_brand_new_database(conn)
    pre_migration_event_ids: List[int] = []
    if not was_new:
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "schema_version" not in existing_columns:
            pre_migration_event_ids = [row[0] for row in conn.execute("SELECT id FROM events").fetchall()]

    _ensure_schema(conn)

    if pre_migration_event_ids:
        conn.executemany(
            "UPDATE events SET schema_version = ? WHERE id = ?",
            [(LEGACY_EVENT_SCHEMA_VERSION, event_id) for event_id in pre_migration_event_ids],
        )
        conn.commit()

    conn.execute(f"PRAGMA user_version = {SCHEMA_REVISION}")
    conn.commit()
    # Re-evaluate now that the schema is current -- connect() may have
    # left foreign_keys off because tables this check depends on
    # (e.g. `technicians`) didn't exist yet before this migration ran.
    _configure_foreign_key_enforcement(conn)
    return check_schema_status(conn)


JOBS_PRE_SOURCE_SCOPED_BACKUP_TABLE = "jobs_pre_source_scoped_unique_backup"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
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
    _migrate_jobs_to_source_scoped_unique(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
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
            created_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
        """
    )
    # Added 2026-07-27 for shadow mode: which pipeline entry point produced
    # this record -- "manual" (pasted/typed), "simulator" (fake inbox), or
    # "shadow_mode_quo_live" (real Quo message polled read-only, never
    # sent). CREATE TABLE IF NOT EXISTS above is a no-op on an
    # already-existing events table, so this column needs an explicit,
    # idempotent migration for databases created before this column existed.
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "channel" not in existing_columns:
        conn.execute("ALTER TABLE events ADD COLUMN channel TEXT DEFAULT 'manual'")
    _repair_events_job_id_foreign_key(conn)
    _add_job_status_columns_and_backfill(conn)
    _add_technician_assignment_support(conn)
    _add_whatsapp_handoff_support(conn)
    _add_outbound_log_support(conn)
    _add_closing_and_reconciliation_support(conn)
    _add_technician_update_support(conn)
    _add_idempotency_and_schema_version_support(conn)
    _add_dispatch_tracking_support(conn)
    _add_console_activation_support(conn)
    _add_whatsapp_pending_observations_support(conn)
    _add_technician_acceptance_support(conn)
    _add_whatsapp_group_registry_support(conn)
    _add_whatsapp_outgoing_audit_support(conn)
    # Single-row table (id=1 enforced) tracking the last successful Quo poll
    # checkpoint -- one dispatcher polling one org.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quo_poll_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_checkpoint TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Additive: durable last-poll-failure message for the connection-state
    # UI (Operator Console/Management CRM) -- idempotent for databases
    # created before this column existed, same pattern as every other
    # migration in this module.
    existing_poll_state_columns = {row[1] for row in conn.execute("PRAGMA table_info(quo_poll_state)")}
    if "last_error" not in existing_poll_state_columns:
        conn.execute("ALTER TABLE quo_poll_state ADD COLUMN last_error TEXT")
    # Every Quo message id that has ever been fetched, recorded at fetch
    # time (not at Approve/Reject time) -- makes overlapping poll windows
    # or an app restart structurally unable to re-queue the same message.
    # This id-level dedup ledger is intentionally kept separate from
    # quo_pending_messages below: a message id is only ever inserted here
    # in the same transaction as its full content being durably stored
    # (see save_pending_and_mark_processed), so "processed" always implies
    # "content is safe," never the reverse.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quo_processed_messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT,
            fetched_at TEXT NOT NULL
        )
        """
    )
    # Durable Quo review queue -- the source of truth for what's waiting to
    # be reviewed, superseding the old design where this only lived in
    # st.session_state (lost on every app/laptop restart). Lifecycle:
    # pending (fetched, not yet opened) -> loaded (open in the review
    # screen; also the state a restart-interrupted review is found in, so
    # "load next" still picks it up) -> actioned (Approve/Edit/Reject/
    # Handle Manually successfully saved an audit event for it). Rows are
    # never deleted in this phase.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quo_pending_messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT,
            group_label TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            from_number TEXT,
            received_at TEXT,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            loaded_at TEXT,
            actioned_at TEXT,
            last_error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quo_pending_status_received "
        "ON quo_pending_messages (status, received_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_job_id_id ON events (job_id, id)"
    )
    conn.commit()
    _add_shadow_mode_support(conn)


def _add_shadow_mode_support(conn: sqlite3.Connection) -> None:
    """Shadow Mode's persistence: two additive tables, no existing table
    touched. Mirrors this file's own established jobs/events split --
    `shadow_episodes` is the one-row-per-episode enrichable snapshot
    (mirrors `jobs`), `shadow_events` is the append-only chronological
    log of every SHADOW_OBSERVATION/SHADOW_SUGGESTION/DISPATCHER_DECISION/
    COMMERCIAL_UPDATE/CUSTOMER_RESPONSE/NEGOTIATION_OUTCOME/SHADOW_REVIEWED/
    SHADOW_EXCLUDED action ever recorded against an episode (mirrors
    `events`) -- so a corrected quote is never lost, only ever
    superseded in the snapshot while the original stays visible in the
    log. See docs/SHADOW_MODE.md.

    `episode_id` is an opaque random identifier (never derived from
    customer data), stable across every enrichment call for the same
    episode -- see find_or_create_shadow_episode(). `job_id` starts NULL
    for episodes observed before CONFIRM DISPATCHED (correlated instead
    via pending_message_id or (source, ticket_or_pdl)) and is backfilled,
    never re-created, once the job is committed."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_episodes (
            episode_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            channel TEXT NOT NULL,
            source_message_id TEXT,
            pending_message_id TEXT,
            job_id INTEGER,
            ticket_or_pdl TEXT,
            dispatcher_actor TEXT,
            technician_id INTEGER,
            previous_job_status TEXT,
            incoming_message_type TEXT,
            normalized_intent TEXT,
            shadow_suggested_action TEXT,
            shadow_suggested_reply TEXT,
            shadow_confidence REAL,
            shadow_reasons_json TEXT,
            validation_issues_json TEXT,
            human_review_required INTEGER NOT NULL DEFAULT 0,
            dispatcher_actual_action TEXT,
            dispatcher_actual_reply TEXT,
            actual_send_channel TEXT,
            actual_send_confirmed_at TEXT,
            commercial_update_json TEXT,
            customer_response TEXT,
            negotiation_outcome TEXT,
            final_job_outcome TEXT,
            review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',
            eligible_for_export INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reviewed_at TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_episodes_job_id ON shadow_episodes (job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_episodes_source_ticket ON shadow_episodes (source, ticket_or_pdl)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_episodes_pending_message_id ON shadow_episodes (pending_message_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            channel TEXT NOT NULL,
            actor TEXT,
            payload_json TEXT NOT NULL,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (episode_id) REFERENCES shadow_episodes (episode_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_events_episode_id ON shadow_events (episode_id, id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_shadow_events_idempotency_key "
        "ON shadow_events (idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    conn.commit()


def _migrate_jobs_to_source_scoped_unique(conn: sqlite3.Connection) -> None:
    """2026-07-28: jobs.ticket_number was globally UNIQUE, but a ticket
    number is only unique WITHIN one source company -- AGN, Lucky, and Main
    Leads each generate their own independently, so two companies can
    legitimately share a ticket number. That crashed save_audit_entry with
    a UNIQUE constraint violation (and lost the audit record for whichever
    save failed, since the whole INSERT rolled back).

    Migrates the unique key to (source, ticket_number). Idempotent (skips
    once already applied) and never destructive: the pre-migration table is
    renamed to JOBS_PRE_SOURCE_SCOPED_BACKUP_TABLE, never dropped, and every
    row is copied across with its exact original `id` preserved so
    `events.job_id` foreign keys keep pointing at the right row."""
    current_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if current_sql_row is None or "ticket_number TEXT UNIQUE NOT NULL" not in current_sql_row[0]:
        return  # brand-new table already has the new schema, or already migrated

    conn.execute(f"ALTER TABLE jobs RENAME TO {JOBS_PRE_SOURCE_SCOPED_BACKUP_TABLE}")
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
        f"""
        INSERT INTO jobs (id, ticket_number, source, latest_fields_json, created_at, updated_at)
        SELECT id, ticket_number, source, latest_fields_json, created_at, updated_at
        FROM {JOBS_PRE_SOURCE_SCOPED_BACKUP_TABLE}
        """
    )
    conn.commit()


def _repair_events_job_id_foreign_key(conn: sqlite3.Connection) -> None:
    """2026-07-28: events.job_id's FOREIGN KEY clause was left pointing at
    "jobs_pre_source_scoped_unique_backup" instead of "jobs". SQLite's
    ALTER TABLE ... RENAME automatically rewrites FOREIGN KEY clauses in
    OTHER tables to keep following a renamed table -- the earlier jobs
    migration (_migrate_jobs_to_source_scoped_unique) renamed jobs itself
    away and back, and events.job_id's declaration silently followed the
    away-rename. PRAGMA foreign_keys defaults OFF per connection, so this
    was never enforced, but it was still wrong.

    Deliberately does NOT repeat that rename-old-table-first sequence.
    Instead it follows SQLite's own recommended pattern for schema changes
    ALTER TABLE can't express directly: build the corrected table fresh
    under a temporary name, copy every row across with id/timestamps
    preserved, verify row counts and zero orphan job_id values, only then
    drop the (already-copied) old table and rename the temporary one into
    place -- the rename here targets a table nothing else references yet,
    so it cannot trigger the same class of bug. Idempotent: a no-op once
    already repaired. Whole operation is one transaction: any failure
    rolls back everything, leaving the original events table untouched.
    """
    current_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    if current_sql_row is None or "jobs_pre_source_scoped_unique_backup" not in current_sql_row[0]:
        return  # no events table yet, or already repaired -- idempotent no-op

    # Prove nothing else references "events" by name before any rename --
    # a view, trigger, or another table's foreign key pointing at "events"
    # could otherwise be silently broken/rewritten by SQLite's
    # rename-follows behavior, the exact bug class being fixed here.
    referencing_objects = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name != 'events' AND sql LIKE '%events%'"
    ).fetchall()
    for obj_type, obj_name, obj_sql in referencing_objects:
        normalized = " ".join((obj_sql or "").split())
        if obj_type in ("view", "trigger"):
            raise RuntimeError(
                f"Refusing automatic events table repair: {obj_type} {obj_name!r} references "
                "'events' -- resolve manually before migrating."
            )
        if obj_type == "table" and "REFERENCES events" in normalized:
            raise RuntimeError(
                f"Refusing automatic events table repair: table {obj_name!r} has a foreign key "
                "referencing 'events' -- resolve manually before migrating."
            )
        # An index on events (e.g. idx_events_job_id_id) is expected and
        # harmless -- it's recreated below after the swap, not a blocker.

    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE events_fk_repair_new (
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
                created_at TEXT NOT NULL,
                channel TEXT DEFAULT 'manual',
                FOREIGN KEY (job_id) REFERENCES jobs (id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO events_fk_repair_new (
                id, job_id, ticket_number, raw_message, message_type, source,
                confidence, confidence_reasons_json, decision_verdict,
                decision_reasons_json, suggested_quo_reply, luxor_message,
                dispatcher_edits, final_action, created_at, channel
            )
            SELECT
                id, job_id, ticket_number, raw_message, message_type, source,
                confidence, confidence_reasons_json, decision_verdict,
                decision_reasons_json, suggested_quo_reply, luxor_message,
                dispatcher_edits, final_action, created_at, channel
            FROM events
            """
        )

        src_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        dst_count = conn.execute("SELECT COUNT(*) FROM events_fk_repair_new").fetchone()[0]
        if src_count != dst_count:
            raise RuntimeError(
                f"events FK repair row-count mismatch: source={src_count} destination={dst_count}"
            )

        orphans = conn.execute(
            """
            SELECT COUNT(*) FROM events_fk_repair_new
            WHERE job_id IS NOT NULL AND job_id NOT IN (SELECT id FROM jobs)
            """
        ).fetchone()[0]
        if orphans:
            raise RuntimeError(
                f"events FK repair found {orphans} orphan job_id value(s) -- aborting migration; "
                "resolve manually (see report of orphan rows before this migration is retried)"
            )

        # Old table is fully copied and verified -- safe to drop now. Not
        # kept as a live backup table (per instruction: prefer the
        # file-level database backup taken before this migration ran, over
        # cluttering the live schema with another backup table). The
        # pre-existing jobs_pre_source_scoped_unique_backup table from the
        # earlier migration is left untouched.
        conn.execute("DROP TABLE events")
        conn.execute("ALTER TABLE events_fk_repair_new RENAME TO events")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_job_id_id ON events (job_id, id)")

        fk_violations = conn.execute("PRAGMA foreign_key_check(events)").fetchall()
        if fk_violations:
            raise RuntimeError(f"events FK repair failed PRAGMA foreign_key_check: {fk_violations}")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"events FK repair failed PRAGMA integrity_check: {integrity}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _configure_foreign_key_enforcement(conn: sqlite3.Connection) -> None:
    """PRAGMA foreign_keys is a per-connection setting, never persisted in
    the database file -- SQLite defaults it OFF, so it must be set
    explicitly on every connection, not just once during migration. Only
    turned on when every known foreign key relationship is currently
    clean (cheap indexed checks), so enforcement can never itself break on
    data it hasn't verified.

    Tolerates a not-yet-migrated database (e.g. connect() deliberately
    skipping migration for a pre-existing file -- see connect()'s own
    docstring): a missing `technicians` table means this database
    predates that concept entirely, which is not the same thing as
    "orphaned data" -- there's nothing to enforce yet, so this simply
    leaves foreign_keys at SQLite's safe default (off) rather than
    raising out of connect()."""
    try:
        event_orphans = conn.execute(
            "SELECT COUNT(*) FROM events WHERE job_id IS NOT NULL AND job_id NOT IN (SELECT id FROM jobs)"
        ).fetchone()[0]
        technician_orphans = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE assigned_technician_id IS NOT NULL "
            "AND assigned_technician_id NOT IN (SELECT id FROM technicians)"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return
    if event_orphans == 0 and technician_orphans == 0:
        conn.execute("PRAGMA foreign_keys = ON")


def _add_job_status_columns_and_backfill(conn: sqlite3.Connection) -> None:
    """Adds jobs.status/last_event_id/last_event_at (idempotent ALTER TABLE,
    same PRAGMA table_info pattern as the events.channel migration) and
    backfills every existing job by REPLAYING its events chronologically
    through derive_job_status() -- not by reading only its latest event --
    so a job's backfilled status matches exactly what the live save path
    would have produced had status tracking existed from the start.

    Re-runnable/idempotent via last_event_id IS NULL as the "not yet
    backfilled" marker (rather than gating on column-existence alone),
    so an interrupted backfill safely resumes on the next connect() instead
    of silently leaving some jobs stuck at the default."""
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "status" not in existing_columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'New'")
    if "last_event_id" not in existing_columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN last_event_id INTEGER")
    if "last_event_at" not in existing_columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN last_event_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_status_last_event_at ON jobs (status, last_event_at)"
    )

    unbackfilled_job_ids = [
        row[0] for row in conn.execute(
            """
            SELECT id FROM jobs
            WHERE last_event_id IS NULL
              AND EXISTS (SELECT 1 FROM events WHERE events.job_id = jobs.id)
            """
        ).fetchall()
    ]
    for job_id in unbackfilled_job_ids:
        events_for_job = conn.execute(
            "SELECT id, message_type, final_action, created_at FROM events "
            "WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
        status = "New"
        last_event_id = None
        last_event_at = None
        for event_id, message_type, final_action, created_at in events_for_job:
            status = derive_job_status(status, message_type, final_action)
            last_event_id = event_id
            last_event_at = created_at
        conn.execute(
            "UPDATE jobs SET status = ?, last_event_id = ?, last_event_at = ? WHERE id = ?",
            (status, last_event_id, last_event_at, job_id),
        )
    if unbackfilled_job_ids:
        conn.commit()


def _add_technician_assignment_support(conn: sqlite3.Connection) -> None:
    """Additive only: a technicians roster table (nothing like it existed
    anywhere in the codebase or rules/ config before this) plus a nullable
    jobs.assigned_technician_id column recording the *current* assignment --
    mirroring how jobs.status/last_event_id already track current state
    while events holds the full history. Every assignment/reassignment/
    removal is also recorded as an events row (see assign_technician());
    this column is only ever a fast-lookup mirror of the latest one, never
    an alternate source of truth.

    Idempotent via the same PRAGMA table_info / CREATE TABLE IF NOT EXISTS
    pattern used by every other migration in this module.

    2026-07-28: dispatch_group/closing_group are opaque, technician-owned
    destination strings (the AI never parses or reasons about their
    wording) -- added here as plain nullable columns via idempotent
    ALTER TABLE for databases that already have a technicians table from
    before this concept existed."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS technicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            areas_json TEXT NOT NULL DEFAULT '[]',
            skills_json TEXT NOT NULL DEFAULT '[]',
            vehicle_restrictions_json TEXT NOT NULL DEFAULT '[]',
            dispatch_group TEXT,
            closing_group TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    existing_technician_columns = {row[1] for row in conn.execute("PRAGMA table_info(technicians)")}
    if "dispatch_group" not in existing_technician_columns:
        conn.execute("ALTER TABLE technicians ADD COLUMN dispatch_group TEXT")
    if "closing_group" not in existing_technician_columns:
        conn.execute("ALTER TABLE technicians ADD COLUMN closing_group TEXT")
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "assigned_technician_id" not in existing_columns:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN assigned_technician_id INTEGER REFERENCES technicians (id)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_assigned_technician_id ON jobs (assigned_technician_id)"
    )
    conn.commit()


def _add_whatsapp_handoff_support(conn: sqlite3.Connection) -> None:
    """Additive only, same pattern as every other migration in this module.

    Deliberately a separate table rather than an `events` row: a WhatsApp
    handoff confirmation is a dispatcher noting "I sent this manually" --
    it must never flow through derive_job_status()'s final_action
    matching (which has no branch for this action and would fall through
    to its safest-fallback "Needs Review", incorrectly resetting an
    already-approved/in-progress job's status). Keeping it fully separate
    makes that impossible by construction, not by convention."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            technician_id INTEGER,
            marked_sent_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs (id),
            FOREIGN KEY (technician_id) REFERENCES technicians (id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_handoffs_job_id ON whatsapp_handoffs (job_id)"
    )
    conn.commit()


def _add_outbound_log_support(conn: sqlite3.Connection) -> None:
    """Additive only, same pattern as every other migration in this
    module. Every outbound attempt -- previewed, blocked, duplicate, or
    (in a future phase) actually sent -- is recorded here before any
    network call could happen, so the audit trail covers every attempt,
    not just successful sends. See dispatcher/outbound.py, which is the
    only module permitted to write to this table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quo_outbound_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            job_id INTEGER,
            quo_contact_id TEXT NOT NULL,
            proposed_text TEXT NOT NULL,
            mode TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reason TEXT NOT NULL,
            approved_by TEXT,
            requested_at TEXT NOT NULL,
            sent_at TEXT,
            response_status TEXT,
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quo_outbound_log_job_id ON quo_outbound_log (job_id)"
    )
    conn.commit()


def _add_closing_and_reconciliation_support(conn: sqlite3.Connection) -> None:
    """Additive only, same idempotent PRAGMA table_info pattern as every
    other migration in this module. The minimum structured closing record
    requested: five nullable amount/share columns on `events` (only ever
    populated on a CLOSING-type event a dispatcher actually recorded --
    never invented, matching closing.py's existing "verbatim only"
    philosophy), plus one nullable `reconciliation_state` column on `jobs`
    that is deliberately separate from `jobs.status`/derive_job_status():
    a job's operational lifecycle (New -> Approved -> ... -> Closed) and
    whether it's been financially settled are orthogonal facts, and
    keeping them in separate columns means adding "Settled" can never
    require touching the existing, already-tested status state machine.
    NULL reconciliation_state on a Closed job means "awaiting
    reconciliation" -- not stored as its own literal value since that
    would just be restating "not yet settled" a second way."""
    existing_event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    for column in (
        "closing_amount", "closing_payment_method", "closing_parts_amount",
        "closing_company_share", "closing_technician_share",
    ):
        if column not in existing_event_columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
    existing_job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "reconciliation_state" not in existing_job_columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN reconciliation_state TEXT")
    conn.commit()


def _add_technician_update_support(conn: sqlite3.Connection) -> None:
    """Additive only, same idempotent PRAGMA table_info pattern as every
    other migration in this module. Two nullable columns on `events`:
    `update_type` is one of the fixed, dispatcher-selected technician-
    update-type labels (see record_technician_update()) -- never inferred
    from free text; `eta_minutes` is only ever populated for
    update_type == "ETA", straight from what the dispatcher entered,
    never estimated."""
    existing_event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "update_type" not in existing_event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN update_type TEXT")
    if "eta_minutes" not in existing_event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN eta_minutes INTEGER")
    conn.commit()


def _add_idempotency_and_schema_version_support(conn: sqlite3.Connection) -> None:
    """Additive only, same idempotent PRAGMA table_info pattern as every
    other migration in this module. `idempotency_key` is an opaque,
    caller-computed string (see application.py) that, when supplied,
    makes save_audit_entry()/the operational job actions above safe to
    call any number of times with the same key -- a Streamlit rerun,
    double-click, or poller retry that resubmits the exact same logical
    action finds its already-recorded event instead of inserting a
    duplicate. NULL keys (every event before this migration, and any
    caller that doesn't pass one) are never treated as colliding with
    each other -- SQLite's UNIQUE index intentionally does not enforce
    uniqueness across NULLs, and the partial index below only indexes
    non-NULL keys anyway, so old/unkeyed call sites are completely
    unaffected. `schema_version` records which shape of this table a row
    was written under -- see EVENT_SCHEMA_VERSION and
    docs/EVENT_CONTRACT.md."""
    existing_event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "idempotency_key" not in existing_event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN idempotency_key TEXT")
    if "schema_version" not in existing_event_columns:
        conn.execute(
            f"ALTER TABLE events ADD COLUMN schema_version INTEGER NOT NULL DEFAULT {EVENT_SCHEMA_VERSION}"
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency_key "
        "ON events (idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    conn.commit()


def _add_dispatch_tracking_support(conn: sqlite3.Connection) -> None:
    """Additive only, same pattern as every other migration in this
    module. Speed Dispatch Console lifecycle timestamps -- keyed by
    quo_pending_messages.message_id (not job_id), since these milestones
    (first seen, Luxor prepared, acknowledged, copied, WhatsApp opened)
    all happen *before* a message is necessarily approved into a job.
    "dispatched_manually_at" is the one milestone that coincides with the
    real save_audit_entry()/mark_quo_pending_message_actioned() call --
    recorded here too, purely for age/SLA display, never as an alternate
    source of truth for whether a job was actually created (jobs/events
    remain that source of truth, unchanged). Every column is nullable and
    only ever set once, moving forward -- never backfilled for messages
    that predate this table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dispatch_tracking (
            message_id TEXT PRIMARY KEY,
            first_seen_at TEXT,
            luxor_prepared_at TEXT,
            acknowledged_at TEXT,
            copied_at TEXT,
            whatsapp_opened_at TEXT,
            dispatched_manually_at TEXT
        )
        """
    )
    conn.commit()


def get_dispatch_tracking(conn: sqlite3.Connection, message_id: str) -> dict:
    """Always returns a dict with every known column, defaulting every
    unset milestone to None -- never raises for a message with no
    tracking row yet (the common case for a just-arrived message)."""
    row = conn.execute("SELECT * FROM dispatch_tracking WHERE message_id = ?", (message_id,)).fetchone()
    columns = ("message_id", "first_seen_at", "luxor_prepared_at", "acknowledged_at", "copied_at", "whatsapp_opened_at", "dispatched_manually_at")
    if row is None:
        return {c: (message_id if c == "message_id" else None) for c in columns}
    return dict(zip(columns, row))


def _touch_dispatch_tracking(conn: sqlite3.Connection, message_id: str, column: str) -> None:
    """Sets `column` to now() the first time only -- calling this again
    for the same message/column is a no-op (COALESCE keeps the original
    timestamp), so re-clicking an action never overwrites a genuinely
    earlier recorded milestone."""
    assert column in (
        "first_seen_at", "luxor_prepared_at", "acknowledged_at",
        "copied_at", "whatsapp_opened_at", "dispatched_manually_at",
    ), f"unknown dispatch_tracking column: {column}"
    now = _now()
    conn.execute(
        f"""
        INSERT INTO dispatch_tracking (message_id, {column}) VALUES (?, ?)
        ON CONFLICT(message_id) DO UPDATE SET {column} = COALESCE(dispatch_tracking.{column}, excluded.{column})
        """,
        (message_id, now),
    )
    conn.commit()


def mark_first_seen(conn: sqlite3.Connection, message_id: str) -> None:
    _touch_dispatch_tracking(conn, message_id, "first_seen_at")


def mark_luxor_prepared(conn: sqlite3.Connection, message_id: str) -> None:
    _touch_dispatch_tracking(conn, message_id, "luxor_prepared_at")


def mark_acknowledged(conn: sqlite3.Connection, message_id: str) -> None:
    _touch_dispatch_tracking(conn, message_id, "acknowledged_at")


def mark_copied(conn: sqlite3.Connection, message_id: str) -> None:
    _touch_dispatch_tracking(conn, message_id, "copied_at")


def mark_whatsapp_opened(conn: sqlite3.Connection, message_id: str) -> None:
    _touch_dispatch_tracking(conn, message_id, "whatsapp_opened_at")


def mark_dispatched_manually(conn: sqlite3.Connection, message_id: str) -> None:
    _touch_dispatch_tracking(conn, message_id, "dispatched_manually_at")


def _add_console_activation_support(conn: sqlite3.Connection) -> None:
    """Additive only, same pattern as quo_poll_state. Draws the line
    between "historical development/shadow-mode backlog" and "genuinely
    live queue" -- a message received before live_queue_started_at is
    Previous Backlog (audit-only, never urgent, never counted); at or
    after it, the message is eligible for the live actionable queue.
    console_activation_state is a single row (id=1), same convention as
    quo_poll_state. console_activation_audit keeps a permanent record of
    every boundary change (initial activation + any manual reset) --
    never overwritten, never deleted."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS console_activation_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            live_queue_started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS console_activation_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_value TEXT,
            new_value TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            note TEXT
        )
        """
    )
    conn.commit()


def get_live_queue_started_at(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT live_queue_started_at FROM console_activation_state WHERE id = 1").fetchone()
    return row[0] if row else None


def ensure_live_queue_activated(conn: sqlite3.Connection) -> str:
    """Idempotent: seeds the live-queue boundary to now() the first time
    this is ever called for this database, and never again -- a later
    app/process restart must NOT silently move the boundary forward
    (that would quietly reclassify genuinely-live jobs as backlog). The
    boundary only ever moves via the explicit, audited
    reset_live_queue_boundary() action below."""
    existing = get_live_queue_started_at(conn)
    if existing:
        return existing
    now = _now()
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO console_activation_audit (old_value, new_value, changed_at, note) VALUES (?, ?, ?, ?)",
        (None, now, now, "Initial activation on first corrected-console launch"),
    )
    conn.commit()
    return now


def reset_live_queue_boundary(conn: sqlite3.Connection, note: str = "Manual reset") -> str:
    """Explicit administrative action only -- never called automatically.
    Everything before the new boundary becomes Previous Backlog; nothing
    is deleted or rewritten. Always recorded in console_activation_audit,
    including the prior boundary value, so the change itself is
    auditable."""
    old = get_live_queue_started_at(conn)
    now = _now()
    conn.execute(
        """
        INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET live_queue_started_at = excluded.live_queue_started_at, updated_at = excluded.updated_at
        """,
        (now, now),
    )
    conn.execute(
        "INSERT INTO console_activation_audit (old_value, new_value, changed_at, note) VALUES (?, ?, ?, ?)",
        (old, now, now, note),
    )
    conn.commit()
    return now


def _add_whatsapp_pending_observations_support(conn: sqlite3.Connection) -> None:
    """Durable replacement for whatsapp_bridge_adapter.py's previous
    in-memory-only pending queue. An observation the adapter has
    accepted must survive a CRM process restart; a Python module-level
    list cannot do that. Deduplication by message_id is enforced here
    (UNIQUE + INSERT OR IGNORE) instead of a separate in-memory seen-set
    -- one source of truth, not two that could drift. Still never
    written to by the bridge itself (only via the adapter's HTTP layer,
    unchanged), and still never auto-attached to a job -- job_id stays
    NULL until a human explicitly attaches it via the existing connected
    UI flow."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_pending_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            sender TEXT,
            direction TEXT NOT NULL DEFAULT 'incoming',
            whatsapp_timestamp TEXT,
            text TEXT NOT NULL,
            extracted_ticket TEXT,
            quoted_message_id TEXT,
            correlated_job_id INTEGER,
            job_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            human_review_needed INTEGER NOT NULL DEFAULT 0,
            human_review_reason TEXT,
            received_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_pending_observations_quoted "
        "ON whatsapp_pending_observations(quoted_message_id)"
    )
    conn.commit()


_WHATSAPP_OBSERVATION_COLUMNS = (
    "message_id", "group_id", "group_name", "sender", "direction", "timestamp",
    "text", "extracted_ticket", "quoted_message_id", "correlated_job_id", "job_id",
    "status", "human_review_needed", "human_review_reason", "received_at", "created_at",
)


def add_whatsapp_observation(conn: sqlite3.Connection, observation: dict) -> dict:
    """Durably inserts one captured WhatsApp observation. message_id is
    UNIQUE, so a retry of the same message_id is a safe no-op (reports
    duplicate=True) rather than a second row -- exactly-once storage,
    enforced by the database itself, not by a caller-side check-then-act
    that could race. If quoted_message_id matches an earlier
    observation that already has a job_id, that job_id is copied here as
    correlated_job_id -- a hint surfaced to the operator, never an
    automatic attach or status change."""
    now = _now()
    correlated_job_id = None
    if observation.get("quoted_message_id"):
        row = conn.execute(
            "SELECT job_id FROM whatsapp_pending_observations WHERE message_id = ? AND job_id IS NOT NULL",
            (observation["quoted_message_id"],),
        ).fetchone()
        if row:
            correlated_job_id = row[0]
    cur = conn.execute(
        "INSERT OR IGNORE INTO whatsapp_pending_observations "
        "(message_id, group_id, group_name, sender, direction, whatsapp_timestamp, text, extracted_ticket, "
        "quoted_message_id, correlated_job_id, status, human_review_needed, human_review_reason, received_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            observation["message_id"], observation["group_id"], observation["group_name"],
            observation.get("sender"), observation.get("direction", "incoming"),
            observation.get("timestamp"), observation["text"], observation.get("extracted_ticket"),
            observation.get("quoted_message_id"), correlated_job_id, "pending",
            1 if observation.get("human_review_needed") else 0, observation.get("human_review_reason"),
            now, now,
        ),
    )
    conn.commit()
    return {"accepted": True, "duplicate": cur.rowcount == 0}


def _whatsapp_observation_row_to_dict(row) -> dict:
    d = dict(zip(_WHATSAPP_OBSERVATION_COLUMNS, row))
    d["human_review_needed"] = bool(d["human_review_needed"])
    return d


def list_whatsapp_pending_observations(conn: sqlite3.Connection, include_resolved: bool = False) -> list:
    where = "" if include_resolved else "WHERE o.status = 'pending'"
    # LEFT JOIN, not denormalized at write time: a group's registry role
    # can be corrected by the owner later without needing to backfill
    # every historical observation -- the display always reflects the
    # CURRENT registry state, not a snapshot from when the message
    # arrived. A group with no registry row at all (should not happen --
    # the bridge only ever forwards allowlisted groups -- but handled
    # defensively) reads as UNMAPPED_WORK / HUMAN_REVIEW, never silently
    # blank.
    rows = conn.execute(
        f"""
        SELECT o.message_id, o.group_id, o.group_name, o.sender, o.direction, o.whatsapp_timestamp,
               o.text, o.extracted_ticket, o.quoted_message_id, o.correlated_job_id, o.job_id, o.status,
               o.human_review_needed, o.human_review_reason, o.received_at, o.created_at,
               r.role, r.company, r.service_area, r.technician_id, r.technician_name_unconfirmed,
               r.confirmed_by_owner, r.notes
        FROM whatsapp_pending_observations o
        LEFT JOIN whatsapp_group_registry r ON r.group_id = o.group_id
        {where}
        ORDER BY o.id ASC
        """
    ).fetchall()
    return [_whatsapp_observation_row_to_enriched_dict(r) for r in rows]


def _whatsapp_observation_row_to_enriched_dict(row) -> dict:
    base_cols = _WHATSAPP_OBSERVATION_COLUMNS
    d = dict(zip(base_cols, row[: len(base_cols)]))
    d["human_review_needed"] = bool(d["human_review_needed"])
    (role, company, service_area, technician_id, technician_name_unconfirmed,
     confirmed_by_owner, notes) = row[len(base_cols):]
    d["group_role"] = role or "UNMAPPED_WORK"
    d["group_company"] = company
    d["group_service_area"] = service_area
    d["group_technician_id"] = technician_id
    d["group_technician_name"] = technician_name_unconfirmed
    d["group_mapping_confirmed"] = bool(confirmed_by_owner)
    d["group_mapping_status"] = "CONFIRMED" if confirmed_by_owner else "HUMAN_REVIEW"
    d["group_offline_notes"] = notes
    return d


def get_whatsapp_observation(conn: sqlite3.Connection, message_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT message_id, group_id, group_name, sender, direction, whatsapp_timestamp, "
        "text, extracted_ticket, quoted_message_id, correlated_job_id, job_id, status, "
        "human_review_needed, human_review_reason, received_at, created_at "
        "FROM whatsapp_pending_observations WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    return _whatsapp_observation_row_to_dict(row) if row else None


def resolve_whatsapp_observation(conn: sqlite3.Connection, message_id: str, status: str, job_id: Optional[int] = None) -> Optional[dict]:
    """Marks an observation resolved (e.g. status='attached') and, only
    when the caller explicitly supplies one (the human-driven attach
    flow), records the real job_id it was attached to -- this is what
    lets a later quoted-reply correlate back to the right job."""
    if job_id is not None:
        conn.execute(
            "UPDATE whatsapp_pending_observations SET status = ?, job_id = ? WHERE message_id = ?",
            (status, job_id, message_id),
        )
    else:
        conn.execute(
            "UPDATE whatsapp_pending_observations SET status = ? WHERE message_id = ?",
            (status, message_id),
        )
    conn.commit()
    return get_whatsapp_observation(conn, message_id)


_REGISTRY_ROLES = (
    "COMPANY_JOB_INTAKE", "COMPANY_CLOSINGS", "TECH_DISPATCH", "TECH_CLOSINGS",
    "UNMAPPED_WORK", "IGNORE",
)


def _add_whatsapp_group_registry_support(conn: sqlite3.Connection) -> None:
    """Multi-group shadow mode's group identity table. Runtime code must
    look a group up here by its exact WhatsApp group_id -- never infer a
    group's role from its display name (names change, get typo'd, and
    are not a safe identity key). Name-based analysis stays an offline,
    human-reviewed proposal (see scripts/seed_whatsapp_group_registry.py)
    that becomes a row here only once explicitly seeded; is_active and
    confirmed_by_owner both default to inert so importing a row is never
    itself an activation."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_group_registry (
            group_id TEXT PRIMARY KEY,
            exact_name TEXT NOT NULL,
            role TEXT NOT NULL,
            company TEXT,
            service_area TEXT,
            technician_id INTEGER REFERENCES technicians(id),
            technician_name_unconfirmed TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            confirmed_by_owner INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'name_heuristic',
            notes TEXT,
            confirmed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def upsert_group_registry_entry(conn: sqlite3.Connection, entry: dict) -> dict:
    """Idempotent by group_id (the real WhatsApp id, never a name).
    Re-running the seed script is always safe -- it never creates a
    duplicate row and never silently downgrades an owner-confirmed row
    back to unconfirmed unless the caller explicitly passes that."""
    if entry.get("role") not in _REGISTRY_ROLES:
        raise ValueError(f"role must be one of {_REGISTRY_ROLES}, got {entry.get('role')!r}")
    now = _now()
    existing = conn.execute(
        "SELECT created_at FROM whatsapp_group_registry WHERE group_id = ?", (entry["group_id"],)
    ).fetchone()
    created_at = existing[0] if existing else now
    conn.execute(
        """
        INSERT INTO whatsapp_group_registry
            (group_id, exact_name, role, company, service_area, technician_id,
             technician_name_unconfirmed, is_active, confirmed_by_owner, source, notes,
             confirmed_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(group_id) DO UPDATE SET
            exact_name = excluded.exact_name,
            role = excluded.role,
            company = excluded.company,
            service_area = excluded.service_area,
            technician_id = excluded.technician_id,
            technician_name_unconfirmed = excluded.technician_name_unconfirmed,
            is_active = excluded.is_active,
            confirmed_by_owner = excluded.confirmed_by_owner,
            source = excluded.source,
            notes = excluded.notes,
            confirmed_at = excluded.confirmed_at,
            updated_at = excluded.updated_at
        """,
        (
            entry["group_id"], entry["exact_name"], entry["role"], entry.get("company"),
            entry.get("service_area"), entry.get("technician_id"),
            entry.get("technician_name_unconfirmed"),
            1 if entry.get("is_active") else 0, 1 if entry.get("confirmed_by_owner") else 0,
            entry.get("source", "name_heuristic"), entry.get("notes"),
            now if entry.get("confirmed_by_owner") else None, created_at, now,
        ),
    )
    conn.commit()
    return get_group_registry_entry(conn, entry["group_id"])


_REGISTRY_COLUMNS = (
    "group_id", "exact_name", "role", "company", "service_area", "technician_id",
    "technician_name_unconfirmed", "is_active", "confirmed_by_owner", "source", "notes",
    "confirmed_at", "created_at", "updated_at",
)


def _registry_row_to_dict(row) -> dict:
    d = dict(zip(_REGISTRY_COLUMNS, row))
    d["is_active"] = bool(d["is_active"])
    d["confirmed_by_owner"] = bool(d["confirmed_by_owner"])
    return d


def get_group_registry_entry(conn: sqlite3.Connection, group_id: str) -> Optional[dict]:
    row = conn.execute(
        f"SELECT {', '.join(_REGISTRY_COLUMNS)} FROM whatsapp_group_registry WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    return _registry_row_to_dict(row) if row else None


def list_group_registry(conn: sqlite3.Connection, active_only: bool = False) -> list:
    where = "WHERE is_active = 1" if active_only else ""
    rows = conn.execute(
        f"SELECT {', '.join(_REGISTRY_COLUMNS)} FROM whatsapp_group_registry {where} ORDER BY group_id"
    ).fetchall()
    return [_registry_row_to_dict(r) for r in rows]


def list_group_activity(conn: sqlite3.Connection) -> list:
    """Every captured group with how busy it actually is.

    The owner cannot classify 85 groups from memory, and the ones worth
    classifying first are the ones carrying traffic. This joins the
    registry to observed message volume so the review screen can lead
    with "this group sent 40 messages this week and Banna does not know
    what it is".

    Read-only. Counting messages never changes how any of them are
    interpreted.
    """
    rows = conn.execute(
        """
        SELECT r.group_id,
               r.exact_name,
               r.role,
               r.company,
               r.service_area,
               r.technician_id,
               r.confirmed_by_owner,
               r.is_active,
               COUNT(o.id)            AS message_count,
               MAX(o.received_at)     AS last_message_at,
               SUM(CASE WHEN o.received_at >= datetime('now', '-7 days')
                        THEN 1 ELSE 0 END) AS recent_count
        FROM whatsapp_group_registry r
        LEFT JOIN whatsapp_pending_observations o ON o.group_id = r.group_id
        GROUP BY r.group_id
        ORDER BY recent_count DESC, message_count DESC, r.group_id
        """
    ).fetchall()
    return [
        {
            "group_id": r[0], "exact_name": r[1], "role": r[2], "company": r[3],
            "service_area": r[4], "technician_id": r[5],
            "confirmed_by_owner": bool(r[6]), "is_active": bool(r[7]),
            "message_count": r[8] or 0, "last_message_at": r[9],
            "recent_count": r[10] or 0,
        }
        for r in rows
    ]


def known_companies(conn: sqlite3.Connection) -> list:
    """Companies the owner has already confirmed somewhere in the
    registry. Offered as choices so a typo cannot silently create a
    fourth competitor that correlates against nothing."""
    rows = conn.execute(
        "SELECT DISTINCT company FROM whatsapp_group_registry "
        "WHERE company IS NOT NULL AND TRIM(company) <> '' ORDER BY company"
    ).fetchall()
    return [r[0] for r in rows]


def _add_whatsapp_outgoing_audit_support(conn: sqlite3.Connection) -> None:
    """Deliberately a SEPARATE table from whatsapp_pending_observations,
    with no shared accessor function and no column linking it into the
    attach-to-job flow. This is what makes 'an outgoing message can
    never become inbound work' a structural fact rather than a runtime
    check that could be bypassed: nothing in this file ever reads this
    table when computing what belongs in the actionable pending queue."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_outgoing_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            whatsapp_timestamp TEXT,
            text TEXT NOT NULL,
            received_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def add_whatsapp_outgoing_audit(conn: sqlite3.Connection, entry: dict) -> dict:
    now = _now()
    cur = conn.execute(
        "INSERT OR IGNORE INTO whatsapp_outgoing_audit "
        "(message_id, group_id, group_name, whatsapp_timestamp, text, received_at, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (entry["message_id"], entry["group_id"], entry["group_name"], entry.get("timestamp"),
         entry["text"], now, now),
    )
    conn.commit()
    return {"accepted": True, "duplicate": cur.rowcount == 0}


def list_whatsapp_outgoing_audit(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT message_id, group_id, group_name, whatsapp_timestamp, text, received_at, created_at "
        "FROM whatsapp_outgoing_audit ORDER BY id ASC"
    ).fetchall()
    cols = ("message_id", "group_id", "group_name", "whatsapp_timestamp", "text", "received_at", "created_at")
    return [dict(zip(cols, r)) for r in rows]


def _add_technician_acceptance_support(conn: sqlite3.Connection) -> None:
    """Additive only, same pattern as every other migration in this
    module. Job-keyed (not message_id-keyed like dispatch_tracking)
    because a job outlives the pending message that created it, and
    technician acceptance is a fact about the job, not about one Quo
    message. Deliberately does NOT touch jobs.status -- "did the
    technician accept" is orthogonal to the deterministic job-status
    state machine (derive_job_status()), exactly like reconciliation_state
    is orthogonal to status. See docs/END_TO_END_WORKFLOW_AUDIT.md step 6
    for why this table didn't exist before this sprint."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS technician_acceptance (
            job_id INTEGER PRIMARY KEY,
            accepted_at TEXT,
            accepted_via_message_id TEXT,
            declined_at TEXT,
            declined_via_message_id TEXT,
            decline_reason TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def technician_acceptance_schema_present(conn: sqlite3.Connection) -> bool:
    """Read-only, independent of check_schema_status()/require_current_schema(),
    same reasoning as shadow_schema_present(): this table is additive
    and optional, not part of the blanket schema-upgrade gate every
    existing write-capable page already enforces, so a real database
    that hasn't yet run this migration doesn't suddenly show "Database
    upgrade required" for functionality that has nothing to do with
    technician acceptance. Callers degrade gracefully (treat as "not
    tracked yet") instead."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='technician_acceptance'"
    ).fetchone()
    return row is not None


def get_technician_acceptance(conn: sqlite3.Connection, job_id: int, schema_present: Optional[bool] = None) -> dict:
    """Always returns a dict -- never raises for a job with no row yet
    (the common case before a technician has replied at all), and never
    raises on a database that hasn't run this migration (returns the
    same all-None shape, per technician_acceptance_schema_present()).

    `schema_present` lets a caller that already checked
    technician_acceptance_schema_present(conn) itself this request (e.g.
    operator_api.py's _technician_k_dto, looping over many jobs) pass
    that answer straight through instead of this function re-querying
    sqlite_master a second time for the same fact -- profiling
    route_action_center at 500 synthetic jobs found this exact double
    check was ~36% of the route's total cost. Pass nothing (the
    default) to check it here, unchanged for any other caller."""
    if schema_present is None:
        schema_present = technician_acceptance_schema_present(conn)
    if not schema_present:
        return {"job_id": job_id, "accepted_at": None, "accepted_via_message_id": None,
                "declined_at": None, "declined_via_message_id": None, "decline_reason": None}
    row = conn.execute(
        "SELECT accepted_at, accepted_via_message_id, declined_at, declined_via_message_id, decline_reason "
        "FROM technician_acceptance WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    columns = ("accepted_at", "accepted_via_message_id", "declined_at", "declined_via_message_id", "decline_reason")
    if row is None:
        return {"job_id": job_id, **{c: None for c in columns}}
    return {"job_id": job_id, **dict(zip(columns, row))}


def mark_technician_accepted(
    conn: sqlite3.Connection, job_id: int, message_id: str, idempotency_key: Optional[str] = None,
) -> dict:
    """Set-once, same COALESCE 'first write wins' contract as
    _touch_dispatch_tracking -- a second confirm (double-click, replay)
    is a safe no-op that preserves the original timestamp/message_id.
    Also records a full audit-trail event (unlike dispatch_tracking's
    silent milestones) -- technician acceptance is a fact worth showing
    in the job's history, not just an internal UI-progress marker."""
    if not technician_acceptance_schema_present(conn):
        raise InvalidJobActionError(
            "This database has not run the technician-acceptance migration yet -- "
            "see docs/SCHEMA_MIGRATION_RUNBOOK.md."
        )
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    now = _now()
    conn.execute(
        """
        INSERT INTO technician_acceptance (job_id, accepted_at, accepted_via_message_id, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            accepted_at = COALESCE(technician_acceptance.accepted_at, excluded.accepted_at),
            accepted_via_message_id = COALESCE(technician_acceptance.accepted_via_message_id, excluded.accepted_via_message_id),
            updated_at = excluded.updated_at
        """,
        (job_id, now, message_id, now),
    )
    conn.commit()
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.OPERATIONAL_NOTE.value, final_action=ActionName.TECHNICIAN_ACCEPTED.value,
        raw_message=f"Confirmed accepted via WhatsApp message {message_id}", idempotency_key=idempotency_key,
    )
    return get_technician_acceptance(conn, job_id)


def mark_technician_declined(
    conn: sqlite3.Connection, job_id: int, message_id: str, reason: str, idempotency_key: Optional[str] = None,
) -> dict:
    if not technician_acceptance_schema_present(conn):
        raise InvalidJobActionError(
            "This database has not run the technician-acceptance migration yet -- "
            "see docs/SCHEMA_MIGRATION_RUNBOOK.md."
        )
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    now = _now()
    conn.execute(
        """
        INSERT INTO technician_acceptance (job_id, declined_at, declined_via_message_id, decline_reason, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            declined_at = COALESCE(technician_acceptance.declined_at, excluded.declined_at),
            declined_via_message_id = COALESCE(technician_acceptance.declined_via_message_id, excluded.declined_via_message_id),
            decline_reason = COALESCE(technician_acceptance.decline_reason, excluded.decline_reason),
            updated_at = excluded.updated_at
        """,
        (job_id, now, message_id, reason, now),
    )
    conn.commit()
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.OPERATIONAL_NOTE.value, final_action=ActionName.TECHNICIAN_DECLINED.value,
        raw_message=f"Confirmed declined via WhatsApp message {message_id}: {reason}", idempotency_key=idempotency_key,
    )
    return get_technician_acceptance(conn, job_id)


# Module-level clock, swappable only by tests (production always uses
# the default SystemClock) -- see clock.py. Every _now() call in this
# file goes through it, so a test can make "now" deterministic without
# threading a clock parameter through dozens of existing call sites.
_CLOCK: Clock = SystemClock()


def set_clock(clock: Clock) -> None:
    """Test-only seam. Never called by production code -- SystemClock
    is the permanent default. Always paired with reset_clock() in a
    fixture teardown so one test's fixed time can't leak into another."""
    global _CLOCK
    _CLOCK = clock


def reset_clock() -> None:
    global _CLOCK
    _CLOCK = SystemClock()


def _now() -> str:
    return now_iso(_CLOCK)


def _current_fields(job: ExtractedJob) -> dict:
    return {name: fv.value for name, fv in job.all_fields().items()}


def _looks_like_closing(raw_text: str) -> bool:
    return bool(re.search(r"\bclosed\b", raw_text.lower()) and re.search(r"\$\s*\d", raw_text))


# 2026-07-29: known, accepted gap (see docs/architecture.md section 6) --
# a cancellation that never says "cancel" fell through to UNRECOGNIZED.
# Closes it with a small, explicit set of conservative, deterministic
# patterns for the exact reworded phrasings observed in real messages,
# plus narrow guards so a hedged/interrogative mention of cancellation
# ("customer may cancel", "asked whether cancellation is possible") or a
# "found keys" message that goes on to say the underlying need is still
# there ("found her keys but still needs the lock repaired") is NOT
# treated as a confident cancellation -- deliberately no attempt at broad
# natural-language understanding; anything not matched here still falls
# through to UNRECOGNIZED for manual review, exactly like before.
#
# Hardened 2026-07-29 (same day): the original hedge guard only matched a
# modal immediately adjacent to "cancel" ("may cancel", "may just
# cancel"), so "may need to cancel" / "might have to cancel" / "could
# decide to cancel" (two words between the modal and "cancel") slipped
# through and were wrongly classified as CANCELLATION -- widened the gap
# to up to 4 words. Also added the "found keys" continuation guard below,
# for the same reason: "customer found her keys" alone is a confident
# signal, but "found her keys but still needs the lock repaired" is not.
_CANCELLATION_HEDGE_RE = re.compile(
    r"\b(?:may|might|could|possibly)\b(?:\s+\w+){0,4}\s+cancel"  # "may cancel", "might have to cancel"
    r"|\bwhether\s+\w*\s*cancel",  # "whether cancellation is possible"
    re.IGNORECASE,
)

# A "found keys" message that goes on to say the underlying need is still
# there is not a cancellation -- narrowly scoped to this one shape (found
# keys + a later still/now/but ... needs), not a general "needs" guard.
_CANCELLATION_FOUND_KEYS_STILL_NEEDS_RE = re.compile(
    r"\bfound\s+(?:her|his|their|the)\s+keys?\b.*\b(?:still|now|but)\b.*\bneeds?\b",
    re.IGNORECASE,
)

_REWORDED_CANCELLATION_PATTERNS = (
    re.compile(r"\bno longer needs?\b", re.IGNORECASE),
    re.compile(r"\b(?:does not|doesn't|does n't) need\b.*\bany\s*more\b", re.IGNORECASE),
    # Subject must be the customer ("customer"/"they"), never the
    # technician -- "technician found the keys" is not a cancellation
    # signal, so it's deliberately excluded by only allowlisting these
    # two subjects rather than matching "found ... keys" on its own.
    re.compile(r"\b(?:customer|they)\s+found\s+(?:her|his|their|the)\s+keys?\b", re.IGNORECASE),
    re.compile(r"\bwill call back later,?\s*hung up\b", re.IGNORECASE),
)


def _is_confident_cancellation(raw_text: str) -> bool:
    if _CANCELLATION_HEDGE_RE.search(raw_text):
        return False
    if _CANCELLATION_FOUND_KEYS_STILL_NEEDS_RE.search(raw_text):
        return False
    if re.search(r"\bcancel(?:led|ed)?\b", raw_text, re.IGNORECASE) or re.search(r"\bcancellation\b", raw_text, re.IGNORECASE):
        return True
    return any(pattern.search(raw_text) for pattern in _REWORDED_CANCELLATION_PATTERNS)


def classify_incoming(conn: sqlite3.Connection, job: ExtractedJob) -> ClassifyResult:
    ticket = job.ticket_number.value
    if not ticket:
        return ClassifyResult(
            MessageType.NEW, None, [],
            "no ticket/PDL number -- cannot correlate with any existing job",
        )

    # Scoped by source too: ticket numbers are only unique WITHIN one
    # source company (AGN, Lucky, Main Leads each generate their own
    # independently) -- a bare ticket_number lookup could match the wrong
    # company's job if two happened to share the same ticket string.
    row = conn.execute(
        "SELECT id, latest_fields_json FROM jobs WHERE ticket_number = ? AND source = ?",
        (ticket, job.source.value),
    ).fetchone()
    if row is None:
        # In real dispatching, every ticket is dispatched before it can be
        # closed -- a closing-looking message with no prior record for this
        # ticket is anomalous (e.g. out-of-order delivery, a typo'd ticket
        # number) and must never be silently treated as a normal new job.
        if _looks_like_closing(job.raw_text):
            return ClassifyResult(
                MessageType.CLOSING_WITHOUT_DISPATCH, None, [],
                "message looks like a closing, but no prior dispatch exists for this ticket "
                "-- handle manually, do not process as a normal new job",
            )
        return ClassifyResult(MessageType.NEW, None, [], "no existing job found for this ticket/PDL")

    job_id, latest_fields_json = row
    latest_fields = json.loads(latest_fields_json)

    last_raw_row = conn.execute(
        "SELECT raw_message FROM events WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
    ).fetchone()
    if last_raw_row and last_raw_row[0].strip() == job.raw_text.strip():
        return ClassifyResult(MessageType.DUPLICATE, job_id, [], "identical to the most recent message for this ticket")

    text_lower = job.raw_text.lower()

    if _is_confident_cancellation(job.raw_text):
        return ClassifyResult(MessageType.CANCELLATION, job_id, [], "message mentions cancellation")

    if _looks_like_closing(job.raw_text):
        return ClassifyResult(MessageType.CLOSING, job_id, [], "message contains 'Closed' plus a dollar amount")

    if "update" in text_lower and "?" in job.raw_text:
        return ClassifyResult(MessageType.UPDATE_REQUEST, job_id, [], "message asks for an update")

    if map_status_to_reply(job.raw_text.strip()).confirmed:
        return ClassifyResult(MessageType.STATUS, job_id, [], "message matches a known technician status phrase")

    current_fields = _current_fields(job)
    conflicting = [
        name for name in REQUIRED_FIELDS
        if latest_fields.get(name) and current_fields.get(name) and latest_fields.get(name) != current_fields.get(name)
    ]
    if conflicting:
        return ClassifyResult(MessageType.CORRECTION, job_id, conflicting, "required field(s) changed from the stored job")

    return ClassifyResult(
        MessageType.UNRECOGNIZED, job_id, [],
        "repeat message for this ticket did not match a known pattern -- review manually",
    )


VALID_JOB_STATUSES = frozenset({
    "New", "Needs Review", "Approved", "Handle Manually",
    "Rejected", "In Progress", "Closed", "Cancelled",
})

_APPROVE_LIKE_ACTIONS = frozenset({ActionName.APPROVE.value, ActionName.EDIT.value})

# message_type values that don't advance a job's operational lifecycle on
# their own -- approving or rejecting one of these just confirms/dismisses
# that particular report without changing what stage the job is at.
_NON_LIFECYCLE_MESSAGE_TYPES = frozenset({
    MessageType.CORRECTION.value, MessageType.UPDATE_REQUEST.value,
    MessageType.DUPLICATE.value, MessageType.UNRECOGNIZED.value,
})


def derive_job_status(previous_status: Optional[str], message_type: str, final_action: Optional[str]) -> str:
    """The single, deterministic job-status state machine -- used identically
    for a live save_audit_entry() call, the one-time chronological-replay
    backfill, and every test. Never derive status from only the latest
    event elsewhere; always go through this function so behavior can't
    silently drift between the two call sites.

    See docs/architecture.md for the full transition table and the
    ambiguous cases this resolves (e.g. rejecting a later STATUS/CLOSING/
    CANCELLATION/correction report preserves the job's existing status
    rather than rejecting the whole job -- only rejecting the original NEW
    message does that).
    """
    prev = previous_status if previous_status in VALID_JOB_STATUSES else "Needs Review"

    if final_action == ActionName.HANDLE_MANUALLY.value:
        return "Handle Manually"

    if message_type == MessageType.NEW.value:
        if final_action in _APPROVE_LIKE_ACTIONS:
            return "Approved"
        if final_action == "Reject":
            return "Rejected"
        return "Needs Review"  # unknown/missing final_action -- safest fallback

    if message_type == MessageType.STATUS.value:
        if final_action in _APPROVE_LIKE_ACTIONS:
            return "In Progress"
        if final_action == "Reject":
            return prev
        return "Needs Review"

    if message_type == MessageType.CLOSING.value:
        if final_action in _APPROVE_LIKE_ACTIONS:
            return "Closed"
        if final_action == "Reject":
            return prev
        return "Needs Review"

    if message_type == MessageType.CANCELLATION.value:
        # "Pass"/"Cancel" are the dispatcher-initiated PASS / CANCEL action
        # on an already-active job (see pass_or_cancel_job()) -- both are
        # confirmatory, same as Approve/Edit is for an inbound cancellation
        # report; there is deliberately no behavioral difference between
        # "Pass" and "Cancel" here, matching the combined PASS / CANCEL
        # button in the Active Jobs workspace.
        if final_action in _APPROVE_LIKE_ACTIONS or final_action in (ActionName.PASS.value, ActionName.CANCEL.value):
            return "Cancelled"
        if final_action == "Reject":
            return prev
        return "Needs Review"

    if message_type == MessageType.CLOSING_WITHOUT_DISPATCH.value:
        # Anomalous by construction (classify_incoming already flags this
        # as "no prior dispatch exists for this ticket") -- always flagged
        # for review, regardless of what action was taken on it.
        return "Needs Review"

    if message_type in _NON_LIFECYCLE_MESSAGE_TYPES:
        if final_action in _APPROVE_LIKE_ACTIONS or final_action == "Reject":
            return prev
        return "Needs Review"

    if message_type == MessageType.ASSIGNMENT.value:
        # Who's assigned is orthogonal to what lifecycle stage the job is
        # in -- assigning/reassigning/removing a technician never changes
        # status on its own (Handle Manually, from rule 1 above, is the
        # only way an assignment event can move status at all).
        if final_action in (ActionName.ASSIGN.value, ActionName.REASSIGN.value, ActionName.REMOVE.value):
            return prev
        return "Needs Review"

    if message_type == MessageType.OPERATIONAL_NOTE.value:
        # A prepared/sent-manually/completion confirmation is an
        # administrative fact about a job that is already in some real
        # lifecycle stage -- it must never move that stage on its own,
        # exactly like ASSIGNMENT above. complete_job() separately sets
        # jobs.reconciliation_state, which is intentionally orthogonal to
        # status (see _add_closing_and_reconciliation_support's docstring).
        return prev

    return "Needs Review"  # unknown/malformed message_type -- safest fallback


def save_audit_entry(
    conn: sqlite3.Connection,
    job: ExtractedJob,
    classify_result: ClassifyResult,
    decision: Optional[Decision] = None,
    suggested_quo_reply: Optional[str] = None,
    luxor_message: Optional[str] = None,
    dispatcher_edits: Optional[str] = None,
    final_action: Optional[str] = None,
    channel: str = "manual",
    closing_amount: Optional[str] = None,
    closing_payment_method: Optional[str] = None,
    closing_parts_amount: Optional[str] = None,
    closing_company_share: Optional[str] = None,
    closing_technician_share: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> int:
    if idempotency_key:
        existing_event = conn.execute(
            "SELECT job_id FROM events WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing_event is not None:
            # Idempotent replay: the exact same logical action (a
            # Streamlit rerun, double-click, or poller retry resubmitting
            # this key) was already recorded -- return the job_id it
            # produced without writing a second event or re-deriving
            # status again.
            return existing_event[0]

    now = _now()
    ticket = job.ticket_number.value
    fields_snapshot = json.dumps(_current_fields(job))
    message_type_value = classify_result.message_type.value

    if ticket:
        # Deliberately does NOT trust classify_result.existing_job_id for
        # the insert-vs-update decision -- that value is computed once when
        # a message is first loaded into the review screen and can go
        # stale (e.g. the dispatcher finalizes the same loaded job twice,
        # a double-click, or re-approves after already handling it). An
        # upsert keyed on (source, ticket_number) is idempotent regardless:
        # re-saving the same job any number of times updates the same row
        # instead of attempting a second INSERT and crashing on the
        # UNIQUE constraint. job_id is always resolved fresh afterward.
        existing = conn.execute(
            "SELECT id, status FROM jobs WHERE source = ? AND ticket_number = ?",
            (job.source.value, ticket),
        ).fetchone()
        previous_status = existing[1] if existing else "New"
        new_status = derive_job_status(previous_status, message_type_value, final_action)

        conn.execute(
            """
            INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, ticket_number) DO UPDATE SET
                latest_fields_json = excluded.latest_fields_json,
                updated_at = excluded.updated_at,
                status = excluded.status
            """,
            (ticket, job.source.value, fields_snapshot, now, now, new_status),
        )
        job_id = conn.execute(
            "SELECT id FROM jobs WHERE source = ? AND ticket_number = ?",
            (job.source.value, ticket),
        ).fetchone()[0]
    else:
        # No ticket at all to key an upsert on -- fall back to whatever the
        # caller already resolved (e.g. a status/closing entry attached to
        # an already-known job_id).
        job_id = classify_result.existing_job_id
        new_status = None
        if job_id is not None:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            previous_status = row[0] if row else "New"
            new_status = derive_job_status(previous_status, message_type_value, final_action)

    try:
        cur = conn.execute(
            """
            INSERT INTO events (
                job_id, ticket_number, raw_message, message_type, source,
                confidence, confidence_reasons_json, decision_verdict,
                decision_reasons_json, suggested_quo_reply, luxor_message,
                dispatcher_edits, final_action, created_at, channel,
                closing_amount, closing_payment_method, closing_parts_amount,
                closing_company_share, closing_technician_share,
                idempotency_key, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, ticket, job.raw_text, message_type_value, job.source.value,
                job.overall_confidence, json.dumps(job.status_reasons),
                decision.verdict.value if decision else None,
                json.dumps(decision.reasons) if decision else None,
                suggested_quo_reply, luxor_message, dispatcher_edits, final_action, now, channel,
                closing_amount, closing_payment_method, closing_parts_amount,
                closing_company_share, closing_technician_share,
                idempotency_key, EVENT_SCHEMA_VERSION,
            ),
        )
    except sqlite3.IntegrityError:
        # Race: another call inserted the same idempotency_key between our
        # check above and this INSERT (two near-simultaneous reruns) --
        # recover by returning the row that already exists instead of
        # raising, since the write it represents already happened.
        if idempotency_key:
            existing_event = conn.execute(
                "SELECT job_id FROM events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing_event is not None:
                return existing_event[0]
        raise
    event_id = cur.lastrowid

    if job_id is not None and new_status is not None:
        conn.execute(
            "UPDATE jobs SET status = ?, last_event_id = ?, last_event_at = ? WHERE id = ?",
            (new_status, event_id, now, job_id),
        )

    conn.commit()
    return job_id


def mark_job_settled(conn: sqlite3.Connection, job_id: int) -> None:
    """The only writer of jobs.reconciliation_state -- deliberately never
    touches jobs.status, so marking a job financially settled can never
    alter its operational lifecycle. Only meaningful for a job that is
    already Closed, but not restricted to that here: an out-of-order
    settlement is a business-process question, not something this
    function should silently refuse to record."""
    conn.execute(
        "UPDATE jobs SET reconciliation_state = 'Settled', updated_at = ? WHERE id = ?",
        (_now(), job_id),
    )
    conn.commit()


def get_latest_closing_details(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    """Most recent event for this job that actually recorded a closing
    amount -- None if no closing has ever been recorded with structured
    details. Never invents a value for a job whose closing predates this
    schema, or whose closing was recorded without amount details."""
    row = conn.execute(
        """
        SELECT closing_amount, closing_payment_method, closing_parts_amount,
               closing_company_share, closing_technician_share, created_at
        FROM events
        WHERE job_id = ? AND closing_amount IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    columns = ("amount", "payment_method", "parts_amount", "company_share", "technician_share", "recorded_at")
    return dict(zip(columns, row))


def get_audit_timeline(conn: sqlite3.Connection, ticket_number: str) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM events WHERE ticket_number = ? ORDER BY id ASC", (ticket_number,)
    ).fetchall()
    columns = [d[0] for d in conn.execute("SELECT * FROM events LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


def get_audit_timeline_by_job_id(conn: sqlite3.Connection, job_id: int) -> List[dict]:
    """Same events table, same append-only source of truth as
    get_audit_timeline() -- keyed by job_id instead of ticket_number so the
    Jobs tab can look up a specific job unambiguously (ticket numbers are
    only unique within one source, not globally)."""
    rows = conn.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY id ASC", (job_id,)
    ).fetchall()
    columns = [d[0] for d in conn.execute("SELECT * FROM events LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


def get_event_by_idempotency_key(conn: sqlite3.Connection, idempotency_key: str) -> Optional[dict]:
    """The read-side counterpart to save_audit_entry()'s/the operational
    job actions' idempotency check -- lets a caller (application.py)
    determine *before* issuing a command whether it would be a replay,
    for reporting purposes (ActionResult.idempotent_replay). The
    storage-layer dedup itself does not depend on this being called
    first; it re-checks independently at write time."""
    row = conn.execute("SELECT * FROM events WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in conn.execute("SELECT * FROM events LIMIT 0").description]
    return dict(zip(columns, row))


def list_jobs(
    conn: sqlite3.Connection,
    *,
    ticket_search: Optional[str] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[str] = None,
) -> List[dict]:
    """One row per (source, ticket_number) job, for the Jobs tab list view.
    Deliberately selects only id/source/ticket_number/status/timestamps/
    latest_action -- never raw_message, latest_fields_json, or any other
    column that could carry a phone number, address, or full message text,
    so the list view can't leak that data by construction, without needing
    a separate masking step."""
    clauses = ["1 = 1"]
    params: List[object] = []
    if ticket_search:
        clauses.append("jobs.ticket_number LIKE ?")
        params.append(f"%{ticket_search}%")
    if source:
        clauses.append("jobs.source = ?")
        params.append(source)
    if status:
        clauses.append("jobs.status = ?")
        params.append(status)
    if since:
        clauses.append("jobs.last_event_at IS NOT NULL AND jobs.last_event_at >= ?")
        params.append(since)

    sql = (
        "SELECT jobs.id, jobs.source, jobs.ticket_number, jobs.status, "
        "jobs.created_at, jobs.updated_at, jobs.last_event_at, events.final_action AS latest_action, "
        "technicians.name AS assigned_technician_name "
        "FROM jobs "
        "LEFT JOIN events ON events.id = jobs.last_event_id "
        "LEFT JOIN technicians ON technicians.id = jobs.assigned_technician_id "
        "WHERE " + " AND ".join(clauses) + " "
        "ORDER BY COALESCE(jobs.last_event_at, jobs.updated_at) DESC"
    )
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in rows]


# ------------------------------------------------- Operational job actions -
# Purpose-built methods for acting on an ALREADY-COMMITTED job (job_id +
# whatever's actually persisted for it), for the Active Jobs / Closings /
# Completed workflows. These deliberately do NOT go through
# save_audit_entry(), which requires a full ExtractedJob -- a committed
# jobs row only has a flattened latest_fields_json snapshot, and
# reconstructing a fake ExtractedJob (fabricating raw_text/confidence/
# status_reasons that were never persisted) just to satisfy that older
# function's signature was the explicitly rejected approach. Every method
# below instead: (1) fetches the current persisted job via get_job_by_id(),
# (2) validates the action is legal for that job's current state (raising
# InvalidJobActionError otherwise), (3) derives the new status through the
# one existing deterministic authority, derive_job_status() -- never a
# second state machine, (4) inserts one append-only events row and updates
# the jobs snapshot in a single transaction via _insert_operational_event(),
# and (5) returns the freshly-read updated job state. None of these ever
# sends anything externally -- they only record that a human confirmed a
# fact.

class InvalidJobActionError(ValueError):
    """Raised when an action is not legal for a job's current persisted
    state (e.g. recording a closing for a job that was never dispatched,
    completing a job with no closing on record). Distinct from a plain
    ValueError, which this module still uses for missing/malformed input
    (bad job_id, empty required text) -- this is specifically an illegal
    state-transition, so callers can tell the two apart if they want to."""


def get_job_by_id(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    """The current persisted state of one job, as a plain dict -- the
    single read path every operational method below (and the Active
    Jobs/Closings/Completed pages) uses to look up a committed job.
    Never returns or requires an ExtractedJob."""
    row = conn.execute(
        """
        SELECT jobs.id, jobs.source, jobs.ticket_number, jobs.status,
               jobs.reconciliation_state, jobs.assigned_technician_id,
               jobs.latest_fields_json, jobs.created_at, jobs.updated_at,
               jobs.last_event_at, technicians.name AS assigned_technician_name,
               technicians.dispatch_group AS assigned_technician_dispatch_group
        FROM jobs
        LEFT JOIN technicians ON technicians.id = jobs.assigned_technician_id
        WHERE jobs.id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    columns = (
        "id", "source", "ticket_number", "status", "reconciliation_state",
        "assigned_technician_id", "latest_fields_json", "created_at", "updated_at",
        "last_event_at", "assigned_technician_name", "assigned_technician_dispatch_group",
    )
    d = dict(zip(columns, row))
    d["fields"] = json.loads(d.pop("latest_fields_json") or "{}")
    return d


def _insert_operational_event(
    conn: sqlite3.Connection, *, job_id: int, source: str, ticket_number: Optional[str],
    message_type: str, final_action: Optional[str], raw_message: str = "",
    update_type: Optional[str] = None, eta_minutes: Optional[int] = None,
    luxor_message: Optional[str] = None, dispatcher_edits: Optional[str] = None,
    closing_amount: Optional[str] = None, closing_payment_method: Optional[str] = None,
    closing_parts_amount: Optional[str] = None, closing_company_share: Optional[str] = None,
    closing_technician_share: Optional[str] = None, set_reconciliation_state: Optional[str] = None,
    channel: str = "manual", idempotency_key: Optional[str] = None,
) -> int:
    """The one transactional write path every operational method below
    uses: insert one append-only events row, derive the new job status
    through derive_job_status() (the same single authority
    save_audit_entry() already uses), and update the jobs snapshot --
    event insert + status/reconciliation update all in one SQLite
    transaction, so a job can never end up with an audit event recorded
    but its snapshot left stale, or vice versa.

    When `idempotency_key` is supplied and an event with that key
    already exists, this is a no-op: returns the existing event_id
    without inserting a second event or re-deriving status/
    reconciliation again -- safe to call any number of times with the
    same key (Streamlit reruns, double-clicks, poller retries)."""
    if idempotency_key:
        existing = conn.execute(
            "SELECT id FROM events WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            return existing[0]

    now = _now()
    previous_status_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    previous_status = previous_status_row[0] if previous_status_row else "New"
    new_status = derive_job_status(previous_status, message_type, final_action)
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            """
            INSERT INTO events (
                job_id, ticket_number, raw_message, message_type, source,
                luxor_message, dispatcher_edits, final_action, created_at, channel,
                update_type, eta_minutes, idempotency_key, schema_version,
                closing_amount, closing_payment_method, closing_parts_amount,
                closing_company_share, closing_technician_share
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, ticket_number, raw_message, message_type, source,
                luxor_message, dispatcher_edits, final_action, now, channel,
                update_type, eta_minutes, idempotency_key, EVENT_SCHEMA_VERSION,
                closing_amount, closing_payment_method, closing_parts_amount,
                closing_company_share, closing_technician_share,
            ),
        )
        event_id = cur.lastrowid
        if set_reconciliation_state is not None:
            conn.execute(
                "UPDATE jobs SET status = ?, reconciliation_state = ?, last_event_id = ?, last_event_at = ?, updated_at = ? WHERE id = ?",
                (new_status, set_reconciliation_state, event_id, now, now, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status = ?, last_event_id = ?, last_event_at = ?, updated_at = ? WHERE id = ?",
                (new_status, event_id, now, now, job_id),
            )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        if idempotency_key:
            existing = conn.execute(
                "SELECT id FROM events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return existing[0]
        raise
    except Exception:
        conn.rollback()
        raise
    return event_id


_TECHNICIAN_UPDATE_TYPES = frozenset(TECHNICIAN_UPDATE_TYPES)
_TERMINAL_NO_FURTHER_ACTION_STATUSES = frozenset({"Cancelled", "Rejected"})


def record_technician_update(
    conn: sqlite3.Connection, job_id: int, update_type: str, raw_update: str,
    eta_minutes: Optional[int] = None, idempotency_key: Optional[str] = None,
) -> dict:
    """Records a technician's real WhatsApp reply against an already-
    committed job. `update_type` is always dispatcher-selected from the
    fixed set below -- never inferred from the pasted text, matching
    updates.py's "never invent a status" rule. Every recognized type
    (everything except "Custom") derives to "In Progress" -- a confirmed
    report, not a claim that the job is finished (that only ever happens
    through the closing workflow). "Custom" -- an update that doesn't fit
    any of the deterministic types -- derives to "Needs Review" instead,
    since this module cannot safely interpret it on its own."""
    if update_type not in _TECHNICIAN_UPDATE_TYPES:
        raise ValueError(
            f"Unknown technician update type: {update_type!r} -- must be one of "
            f"{sorted(_TECHNICIAN_UPDATE_TYPES)}"
        )
    if update_type == "ETA" and not eta_minutes:
        raise ValueError("ETA updates require eta_minutes -- never invented")
    if not raw_update or not raw_update.strip():
        raise ValueError("raw_update (the technician's actual reply) is required")

    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    if job["status"] in _TERMINAL_NO_FURTHER_ACTION_STATUSES:
        raise InvalidJobActionError(
            f"Cannot record a technician update for job {job_id} -- status is {job['status']!r}"
        )

    final_action = ActionName.APPROVE.value if update_type != "Custom" else None
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.STATUS.value, final_action=final_action,
        raw_message=raw_update.strip(), update_type=update_type, eta_minutes=eta_minutes,
        idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


def mark_company_update_prepared(
    conn: sqlite3.Connection, job_id: int, prepared_text: str, idempotency_key: Optional[str] = None,
) -> dict:
    """Records that a company-facing status update was prepared -- never
    claims it was sent (see mark_company_update_sent_manually for that
    separate, explicit confirmation)."""
    if not prepared_text or not prepared_text.strip():
        raise ValueError("prepared_text is required")
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    has_update = conn.execute(
        "SELECT 1 FROM events WHERE job_id = ? AND message_type = ? AND update_type IS NOT NULL LIMIT 1",
        (job_id, MessageType.STATUS.value),
    ).fetchone()
    if not has_update:
        raise InvalidJobActionError(
            f"Cannot prepare a company update for job {job_id} -- no technician update has been recorded yet"
        )
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.OPERATIONAL_NOTE.value, final_action=ActionName.COMPANY_UPDATE_PREPARED.value,
        luxor_message=prepared_text.strip(), idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


def mark_company_update_sent_manually(
    conn: sqlite3.Connection, job_id: int, prepared_text: str, idempotency_key: Optional[str] = None,
) -> dict:
    """Only explicit confirmation -- this call -- ever records that a
    company update was actually sent. Preparing or copying it never does."""
    if not prepared_text or not prepared_text.strip():
        raise ValueError("prepared_text is required")
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.OPERATIONAL_NOTE.value, final_action=ActionName.COMPANY_UPDATE_SENT_MANUALLY.value,
        luxor_message=prepared_text.strip(), idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


_PASS_CANCEL_ACTIONS = frozenset({ActionName.PASS.value, ActionName.CANCEL.value})


def pass_or_cancel_job(
    conn: sqlite3.Connection, job_id: int, final_action: str, reason: str,
    idempotency_key: Optional[str] = None,
) -> dict:
    """The single dispatcher-initiated pass/cancel action for an already-
    active job -- always requires a reason, always creates an audit
    event. "Pass" and "Cancel" are both accepted (matching the combined
    PASS / CANCEL button) and behave identically: the job's status
    becomes "Cancelled"."""
    if final_action not in _PASS_CANCEL_ACTIONS:
        raise ValueError(f"final_action must be one of {sorted(_PASS_CANCEL_ACTIONS)}, got {final_action!r}")
    if not reason or not reason.strip():
        raise ValueError("reason is required to pass/cancel a job")
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    if job["status"] in _TERMINAL_NO_FURTHER_ACTION_STATUSES or job["status"] == "Closed":
        raise InvalidJobActionError(f"Cannot pass/cancel job {job_id} -- status is already {job['status']!r}")
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.CANCELLATION.value, final_action=final_action,
        raw_message="", dispatcher_edits=f"Reason: {reason.strip()}", idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


def record_job_closing(
    conn: sqlite3.Connection, job_id: int, raw_closing: str,
    final_amount: Optional[str] = None, payment_method: Optional[str] = None,
    parts: Optional[str] = None, company_share: Optional[str] = None,
    technician_share: Optional[str] = None, idempotency_key: Optional[str] = None,
) -> dict:
    """Records a real closing -- from an incoming Quo closing/reply, a
    technician closing pasted from WhatsApp, or a dispatcher-entered
    record -- against an already-committed job. Only ever stores values
    actually supplied; a missing amount/payment method stays missing,
    never invented or calculated. `raw_closing` (the technician's actual
    closing text) is the one required field -- structured amount/payment/
    parts/shares are all optional, exactly as much as was really
    reported."""
    if not raw_closing or not raw_closing.strip():
        raise ValueError("raw_closing (the technician's actual closing text) is required")
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    if job["status"] in _TERMINAL_NO_FURTHER_ACTION_STATUSES:
        raise InvalidJobActionError(f"Cannot record a closing for job {job_id} -- status is {job['status']!r}")
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.CLOSING.value, final_action=ActionName.APPROVE.value,
        raw_message=raw_closing.strip(),
        closing_amount=final_amount or None, closing_payment_method=payment_method or None,
        closing_parts_amount=parts or None, closing_company_share=company_share or None,
        closing_technician_share=technician_share or None, idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


def mark_company_closing_prepared(
    conn: sqlite3.Connection, job_id: int, prepared_text: str, idempotency_key: Optional[str] = None,
) -> dict:
    """Requires a closing to already be on record (job.status == "Closed",
    set by record_job_closing()) -- never claims a closing was prepared
    for a job that was never actually closed."""
    if not prepared_text or not prepared_text.strip():
        raise ValueError("prepared_text is required")
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    if job["status"] != "Closed":
        raise InvalidJobActionError(
            f"Cannot prepare a company closing for job {job_id} -- no closing has been recorded "
            f"yet (status is {job['status']!r})"
        )
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.OPERATIONAL_NOTE.value, final_action=ActionName.COMPANY_CLOSING_PREPARED.value,
        luxor_message=prepared_text.strip(), idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


def mark_company_closing_sent_manually(
    conn: sqlite3.Connection, job_id: int, prepared_text: str, idempotency_key: Optional[str] = None,
) -> dict:
    """Only explicit confirmation -- this call -- ever records that a
    company closing was actually sent. Preparing or copying it never does."""
    if not prepared_text or not prepared_text.strip():
        raise ValueError("prepared_text is required")
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    if job["status"] != "Closed":
        raise InvalidJobActionError(f"Cannot mark a company closing sent for job {job_id} -- status is {job['status']!r}")
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.OPERATIONAL_NOTE.value, final_action=ActionName.COMPANY_CLOSING_SENT_MANUALLY.value,
        luxor_message=prepared_text.strip(), idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


def complete_job(conn: sqlite3.Connection, job_id: int, idempotency_key: Optional[str] = None) -> dict:
    """The final terminal action -- requires the job to already be
    "Closed" (i.e. a real closing was recorded via record_job_closing())
    per the existing deterministic transition authority; raises
    InvalidJobActionError otherwise. Sets jobs.reconciliation_state to
    "Settled" (the same financial-settlement concept mark_job_settled()
    already writes) and records one final OPERATIONAL_NOTE event, both in
    the same transaction. Idempotent: completing an already-settled job
    is a safe no-op rather than a duplicate audit event."""
    job = get_job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    if job["status"] != "Closed":
        raise InvalidJobActionError(
            f"Cannot complete job {job_id} from status {job['status']!r} -- a closing must be "
            "recorded first (record_job_closing)."
        )
    if job["reconciliation_state"] == "Settled":
        return job
    _insert_operational_event(
        conn, job_id=job_id, source=job["source"], ticket_number=job["ticket_number"],
        message_type=MessageType.OPERATIONAL_NOTE.value, final_action=ActionName.JOB_COMPLETED.value,
        set_reconciliation_state="Settled", idempotency_key=idempotency_key,
    )
    return get_job_by_id(conn, job_id)


# ------------------------------------------------ Technician assignment ----
# No technician-identity concept existed anywhere in this codebase or in
# rules/ config before this section (confirmed by inspection 2026-07-28) --
# the only prior notion was an area-level default_technician_availability
# flag in service_areas.yaml plus a freeform per-shift override in
# rules/daily.py, neither of which names an individual technician. This is
# a minimal, additive roster + per-job assignment layer on top of the
# existing jobs/events model -- not a parallel storage system.

def _parse_json_string_list(raw: Optional[str]) -> List[str]:
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _technician_row_to_dict(columns: List[str], row: tuple) -> dict:
    d = dict(zip(columns, row))
    d["areas"] = _parse_json_string_list(d.pop("areas_json"))
    d["skills"] = _parse_json_string_list(d.pop("skills_json"))
    d["vehicle_restrictions"] = _parse_json_string_list(d.pop("vehicle_restrictions_json"))
    d["active"] = bool(d["active"])
    return d


def add_technician(
    conn: sqlite3.Connection,
    name: str,
    areas: List[str],
    skills: Optional[List[str]] = None,
    vehicle_restrictions: Optional[List[str]] = None,
    dispatch_group: Optional[str] = None,
    closing_group: Optional[str] = None,
) -> int:
    """Adds a technician to the roster. `areas` must be service-area names
    -- the same geographic identity rules/service_areas.yaml and
    rules.decide() already resolve to (e.g. "Charlotte") -- not a WhatsApp
    group name, so eligibility checks stay anchored to the one existing
    area concept. `skills`/`vehicle_restrictions` are optional and
    explicit: empty means "no additional restriction beyond the global
    vehicle-restriction rules rules.decide() already enforces before a job
    ever reaches assignment" -- never inferred or assumed.

    `dispatch_group`/`closing_group` are opaque destination strings (e.g.
    a WhatsApp group name) -- purely informational, attached to this one
    technician. Nothing in this codebase parses or reasons about their
    wording; they're only ever displayed to the dispatcher once this
    technician is selected for a job."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO technicians (
            name, active, areas_json, skills_json, vehicle_restrictions_json,
            dispatch_group, closing_group, created_at, updated_at
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name, json.dumps(areas or []), json.dumps(skills or []), json.dumps(vehicle_restrictions or []),
            dispatch_group or None, closing_group or None, now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def set_technician_active(conn: sqlite3.Connection, technician_id: int, active: bool) -> None:
    conn.execute(
        "UPDATE technicians SET active = ?, updated_at = ? WHERE id = ?",
        (1 if active else 0, _now(), technician_id),
    )
    conn.commit()


def update_technician_destinations(
    conn: sqlite3.Connection, technician_id: int, dispatch_group: str, closing_group: str,
) -> dict:
    """Explicit, transactional, audited update of one existing
    technician's two opaque WhatsApp destination strings.
    `add_technician()` only ever sets these at creation time -- this is
    the one path for changing them afterward.

    Validates the technician exists (raises ValueError if not), captures
    the previous values, then updates both fields plus
    technicians.updated_at and inserts one append-only, non-customer
    `events` audit row (job_id=NULL -- this change belongs to no job,
    message_type=OPERATIONAL_NOTE, final_action=
    ActionName.TECHNICIAN_CONFIGURATION_UPDATED) in a single transaction.
    Nothing here parses or validates the destination strings' contents
    (same "opaque" contract as add_technician()) -- non-empty is the
    caller's (application.py's) job.

    Returns previous and new values plus the audit event id. There is no
    separate "rollback" command: rolling back is calling this function
    again with the previous values, which is itself a new, equally
    audited transaction -- never a destructive undo."""
    existing = get_technician(conn, technician_id)
    if existing is None:
        raise ValueError(f"No technician with id {technician_id}")

    previous_dispatch_group = existing["dispatch_group"]
    previous_closing_group = existing["closing_group"]
    now = _now()
    audit_payload = json.dumps({
        "technician_id": technician_id,
        "technician_name": existing["name"],
        "previous": {"dispatch_group": previous_dispatch_group, "closing_group": previous_closing_group},
        "new": {"dispatch_group": dispatch_group, "closing_group": closing_group},
    })

    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE technicians SET dispatch_group = ?, closing_group = ?, updated_at = ? WHERE id = ?",
            (dispatch_group, closing_group, now, technician_id),
        )
        cur = conn.execute(
            """
            INSERT INTO events (
                job_id, ticket_number, raw_message, message_type, source,
                final_action, created_at, channel, schema_version
            ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_payload, MessageType.OPERATIONAL_NOTE.value, "internal",
                ActionName.TECHNICIAN_CONFIGURATION_UPDATED.value, now, "manual", EVENT_SCHEMA_VERSION,
            ),
        )
        audit_event_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "technician_id": technician_id,
        "name": existing["name"],
        "previous_dispatch_group": previous_dispatch_group,
        "previous_closing_group": previous_closing_group,
        "dispatch_group": dispatch_group,
        "closing_group": closing_group,
        "audit_event_id": audit_event_id,
    }


def update_technician_areas(conn: sqlite3.Connection, technician_id: int, areas: List[str]) -> dict:
    """Same audited pattern as update_technician_destinations() above --
    changes a technician's CURRENT service-area assignment (e.g. an
    owner-confirmed reassignment like Kimo moving from Greenville to
    Alabama) without touching any historical jobs/events row. Those
    tables record what was true when each job happened; this only
    changes what's true going forward. The previous areas are captured
    in the audit event, not silently discarded."""
    existing = get_technician(conn, technician_id)
    if existing is None:
        raise ValueError(f"No technician with id {technician_id}")

    previous_areas = existing["areas"]
    now = _now()
    audit_payload = json.dumps({
        "technician_id": technician_id,
        "technician_name": existing["name"],
        "previous": {"areas": previous_areas},
        "new": {"areas": areas},
    })

    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE technicians SET areas_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(areas or []), now, technician_id),
        )
        cur = conn.execute(
            """
            INSERT INTO events (
                job_id, ticket_number, raw_message, message_type, source,
                final_action, created_at, channel, schema_version
            ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_payload, MessageType.OPERATIONAL_NOTE.value, "internal",
                ActionName.TECHNICIAN_CONFIGURATION_UPDATED.value, now, "manual", EVENT_SCHEMA_VERSION,
            ),
        )
        audit_event_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "technician_id": technician_id,
        "name": existing["name"],
        "previous_areas": previous_areas,
        "areas": areas,
        "audit_event_id": audit_event_id,
    }


def list_technicians(conn: sqlite3.Connection, active_only: bool = False) -> List[dict]:
    sql = "SELECT * FROM technicians" + (" WHERE active = 1" if active_only else "") + " ORDER BY name ASC"
    cur = conn.execute(sql)
    rows = cur.fetchall()
    columns = [d[0] for d in cur.description]
    return [_technician_row_to_dict(columns, row) for row in rows]


def get_technician(conn: sqlite3.Connection, technician_id: int) -> Optional[dict]:
    cur = conn.execute("SELECT * FROM technicians WHERE id = ?", (technician_id,))
    row = cur.fetchone()
    if row is None:
        return None
    columns = [d[0] for d in cur.description]
    return _technician_row_to_dict(columns, row)


def record_whatsapp_handoff(conn: sqlite3.Connection, job_id: int, technician_id: Optional[int]) -> None:
    """Records that a dispatcher manually sent the Luxor message via
    WhatsApp -- see _add_whatsapp_handoff_support's docstring for why this
    is a separate table rather than an events row. Never sends anything
    itself; this only records that a human did."""
    conn.execute(
        "INSERT INTO whatsapp_handoffs (job_id, technician_id, marked_sent_at) VALUES (?, ?, ?)",
        (job_id, technician_id, _now()),
    )
    conn.commit()


def get_whatsapp_handoffs(conn: sqlite3.Connection, job_id: int) -> List[dict]:
    cur = conn.execute(
        "SELECT * FROM whatsapp_handoffs WHERE job_id = ? ORDER BY id DESC", (job_id,)
    )
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def is_outbound_duplicate(conn: sqlite3.Connection, idempotency_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM quo_outbound_log WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return row is not None


def record_outbound_attempt(
    conn: sqlite3.Connection, *, idempotency_key: str, source: str, job_id: Optional[int],
    quo_contact_id: str, proposed_text: str, mode: str, outcome: str, reason: str,
    approved_by: Optional[str] = None,
) -> None:
    """Records exactly one outbound attempt -- called for every outcome
    (previewed/blocked/duplicate), not only a real send, and always
    before any network call could occur. `INSERT OR IGNORE` on the unique
    idempotency_key means a second attempt with the same key is silently
    absorbed here too, in addition to the explicit is_outbound_duplicate()
    check callers are expected to make first."""
    conn.execute(
        """
        INSERT OR IGNORE INTO quo_outbound_log (
            idempotency_key, source, job_id, quo_contact_id, proposed_text,
            mode, outcome, reason, approved_by, requested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (idempotency_key, source, job_id, quo_contact_id, proposed_text, mode, outcome, reason, approved_by, _now()),
    )
    conn.commit()


@dataclass
class AssignmentResult:
    outcome: str  # "assigned" | "reassigned" | "removed" | "handle_manually"
    reason: str
    technician_id: Optional[int] = None
    technician_name: Optional[str] = None
    # Opaque destination strings from the selected technician's own record
    # (None for "removed"/"handle_manually") -- for the dispatcher to use,
    # never reasoned about by this module.
    dispatch_group: Optional[str] = None
    closing_group: Optional[str] = None


def _skill_matches(skill: str, service_type_lower: str) -> bool:
    """Tries the deterministic service-category classifier first (for the
    five known car/house lockout/keys/lock-installation categories --
    matched regardless of whether the stored skill uses spaces,
    underscores, or mixed case); falls back to the original plain
    substring test for any other, free-text skill string so
    existing/future non-category skills keep matching exactly as before."""
    normalized = normalize_skill_label(skill)
    if normalized in KNOWN_SERVICE_CATEGORIES:
        return normalized in classify_service_categories(service_type_lower)
    return skill.lower() in service_type_lower


def _technician_area_problem(tech: dict, area_label: Optional[str]) -> Optional[str]:
    """Only meaningful once an area_label has already been confirmed --
    by find_service_area() in assign_technician(), or supplied directly
    by list_eligible_technicians_for(). Returns a single problem
    string, or None if the technician covers this area."""
    if not area_label:
        return "no confirmed service area to check against"
    tech_areas_lower = {a.lower() for a in tech["areas"]}
    if not tech["areas"]:
        return f"technician {tech['name']!r} has no configured coverage areas"
    if area_label.lower() not in tech_areas_lower:
        return (
            f"technician {tech['name']!r} does not cover service area "
            f"{area_label!r} (covers: {', '.join(tech['areas'])})"
        )
    return None


def _technician_skill_problem(tech: dict, service_type: Optional[str]) -> Optional[str]:
    service_type_lower = (service_type or "").lower()
    if not tech["skills"]:
        return None
    if not service_type_lower:
        return f"job has no service type on record -- cannot verify technician {tech['name']!r}'s configured skills"
    if not any(_skill_matches(skill, service_type_lower) for skill in tech["skills"]):
        return f"technician {tech['name']!r}'s configured skills don't cover this job's service type"
    return None


def _technician_vehicle_problem(tech: dict, vehicle: Optional[str]) -> Optional[str]:
    vehicle_lower = (vehicle or "").lower()
    if not tech["vehicle_restrictions"]:
        return None
    if not vehicle_lower:
        return (
            f"job has no vehicle info on record -- cannot verify technician "
            f"{tech['name']!r}'s configured vehicle restrictions"
        )
    if any(r.lower() in vehicle_lower for r in tech["vehicle_restrictions"]):
        return f"technician {tech['name']!r} is explicitly restricted from this job's vehicle"
    return None


def list_eligible_technicians_for(
    conn: sqlite3.Connection,
    area_label: str,
    service_type: Optional[str] = None,
    vehicle: Optional[str] = None,
    active_only: bool = True,
) -> List[dict]:
    """Read-only: every technician with zero eligibility problems against
    an already-confirmed area_label (e.g. "Greenville", "NJ") and
    optional service_type/vehicle. Unlike assign_technician(), never
    resolves area_label itself from a job's raw city/state -- the caller
    already knows which canonical area it's asking about (used for the
    routing-matrix preview and any future "show all eligible candidates"
    UI). Returns technicians in list_technicians()'s stable, name-sorted
    order -- multiple eligible technicians are never reordered or reduced
    to one by database/iteration order; presenting the full list for a
    human to choose from is the caller's responsibility, not this
    function's."""
    eligible = []
    for tech in list_technicians(conn, active_only=active_only):
        if _technician_area_problem(tech, area_label):
            continue
        if _technician_skill_problem(tech, service_type):
            continue
        if _technician_vehicle_problem(tech, vehicle):
            continue
        eligible.append(tech)
    return eligible


def assign_technician(
    conn: sqlite3.Connection,
    job_id: int,
    technician_id: Optional[int],
    service_areas: List[ServiceAreaRecord],
) -> AssignmentResult:
    """The sole entry point for changing a job's technician assignment --
    always writes a permanent events row (message_type=ASSIGNMENT), exactly
    like every other action in this system. `technician_id=None` removes
    the current assignment (always allowed -- removing is the safe
    direction and needs no eligibility checks).

    Never invents technician availability, skill match, acceptance, ETA, or
    completion -- only checks facts already on record: technician.active,
    technician.areas against the job's own freshly re-derived service area
    (via the same find_service_area() the rest of the pipeline already uses
    -- never a stale or duplicated area concept), and technician.skills/
    vehicle_restrictions if explicitly configured. Falls back to "Handle
    Manually" the moment any of those facts don't unambiguously allow the
    assignment -- including when the job's own data is too incomplete to
    verify a configured restriction -- exactly like rules.decide() already
    does for the rest of the pipeline.

    Eligibility is reasoned about purely in terms of service-area identity
    (e.g. "Charlotte") -- never a dispatch/closing group's wording. Once a
    technician is selected, their own opaque dispatch_group/closing_group
    are surfaced on the result for the dispatcher to use, not reasoned
    about by this function."""
    job_row = conn.execute(
        "SELECT id, source, ticket_number, latest_fields_json, assigned_technician_id "
        "FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job_row is None:
        raise ValueError(f"No job with id {job_id}")
    _, source, ticket_number, fields_json, current_technician_id = job_row
    fields = json.loads(fields_json)

    if technician_id is None:
        return _apply_assignment_decision(
            conn, job_id=job_id, source=source, ticket_number=ticket_number,
            technician_id=None, final_action=ActionName.REMOVE.value,
            reason="Assignment removed by dispatcher.",
        )

    tech = get_technician(conn, technician_id)
    if tech is None:
        raise ValueError(f"No technician with id {technician_id}")

    problems: List[str] = []
    if not tech["active"]:
        problems.append(f"technician {tech['name']!r} is marked inactive")

    city, state = fields.get("city"), fields.get("state")
    if not city and not state:
        problems.append("job has no city/state on record -- cannot verify area eligibility")
    else:
        area_lookup = find_service_area(city, state, service_areas)
        if area_lookup.status != "confirmed":
            problems.append(f"job's service area is not confirmed ({area_lookup.reason})")
        else:
            area_label = area_lookup.record.city or area_lookup.record.state
            area_problem = _technician_area_problem(tech, area_label)
            if area_problem:
                problems.append(area_problem)

    skill_problem = _technician_skill_problem(tech, fields.get("service_type"))
    if skill_problem:
        problems.append(skill_problem)

    vehicle_problem = _technician_vehicle_problem(tech, fields.get("vehicle"))
    if vehicle_problem:
        problems.append(vehicle_problem)

    if problems:
        return _apply_assignment_decision(
            conn, job_id=job_id, source=source, ticket_number=ticket_number,
            technician_id=None, final_action=ActionName.HANDLE_MANUALLY.value,
            reason="; ".join(problems),
        )

    action = (
        ActionName.REASSIGN.value if current_technician_id and current_technician_id != technician_id
        else ActionName.ASSIGN.value
    )
    return _apply_assignment_decision(
        conn, job_id=job_id, source=source, ticket_number=ticket_number,
        technician_id=technician_id, final_action=action,
        reason=f"{'Reassigned' if action == ActionName.REASSIGN.value else 'Assigned'} to {tech['name']}.",
    )


def _apply_assignment_decision(
    conn: sqlite3.Connection, *, job_id: int, source: str, ticket_number: str,
    technician_id: Optional[int], final_action: str, reason: str,
) -> AssignmentResult:
    now = _now()
    previous_status_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    previous_status = previous_status_row[0] if previous_status_row else "New"
    new_status = derive_job_status(previous_status, MessageType.ASSIGNMENT.value, final_action)

    cur = conn.execute(
        """
        INSERT INTO events (
            job_id, ticket_number, raw_message, message_type, source, final_action, created_at, channel
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')
        """,
        (job_id, ticket_number, reason, MessageType.ASSIGNMENT.value, source, final_action, now),
    )
    event_id = cur.lastrowid

    conn.execute(
        "UPDATE jobs SET status = ?, last_event_id = ?, last_event_at = ? WHERE id = ?",
        (new_status, event_id, now, job_id),
    )
    result_technician_id = technician_id if final_action in (ActionName.ASSIGN.value, ActionName.REASSIGN.value) else None
    if final_action in (ActionName.ASSIGN.value, ActionName.REASSIGN.value, ActionName.REMOVE.value):
        # Only a *successful* change touches the current-assignment mirror
        # column -- a blocked (Handle Manually) attempt must never silently
        # clear or alter whatever assignment already existed.
        conn.execute(
            "UPDATE jobs SET assigned_technician_id = ? WHERE id = ?", (result_technician_id, job_id),
        )
    conn.commit()

    tech_name = tech_dispatch_group = tech_closing_group = None
    if result_technician_id:
        row = conn.execute(
            "SELECT name, dispatch_group, closing_group FROM technicians WHERE id = ?", (result_technician_id,),
        ).fetchone()
        if row:
            tech_name, tech_dispatch_group, tech_closing_group = row

    outcome_map = {
        ActionName.ASSIGN.value: "assigned", ActionName.REASSIGN.value: "reassigned",
        ActionName.REMOVE.value: "removed", ActionName.HANDLE_MANUALLY.value: "handle_manually",
    }
    return AssignmentResult(
        outcome=outcome_map[final_action], reason=reason,
        technician_id=result_technician_id, technician_name=tech_name,
        dispatch_group=tech_dispatch_group, closing_group=tech_closing_group,
    )


def intake_health(conn: sqlite3.Connection) -> dict:
    """Why the board might be empty.

    A bare zero is ambiguous: it can mean a quiet morning, a dead
    capture, or groups nobody has mapped. Those demand completely
    different reactions from the dispatcher, so the UI has to be able to
    tell them apart. Read-only.
    """
    last_obs = conn.execute(
        "SELECT MAX(received_at) FROM whatsapp_pending_observations").fetchone()[0]
    quo_checkpoint = conn.execute(
        "SELECT last_checkpoint FROM quo_poll_state WHERE id = 1").fetchone()
    groups_total = conn.execute(
        "SELECT COUNT(*) FROM whatsapp_group_registry").fetchone()[0]
    groups_unmapped = conn.execute(
        "SELECT COUNT(*) FROM whatsapp_group_registry "
        "WHERE confirmed_by_owner = 0 OR role IS NULL OR role = 'UNMAPPED_WORK'").fetchone()[0]
    intake_with_company = conn.execute(
        "SELECT COUNT(*) FROM whatsapp_group_registry "
        "WHERE role = 'COMPANY_JOB_INTAKE' AND company IS NOT NULL "
        "AND TRIM(company) <> ''").fetchone()[0]
    return {
        "whatsapp_last_observation_at": last_obs,
        "quo_last_checkpoint": quo_checkpoint[0] if quo_checkpoint else None,
        "groups_total": groups_total,
        "groups_unmapped": groups_unmapped,
        "intake_groups_with_company": intake_with_company,
    }


def get_quo_checkpoint(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT last_checkpoint FROM quo_poll_state WHERE id = 1").fetchone()
    return row[0] if row else None


def set_quo_checkpoint(conn: sqlite3.Connection, iso_ts: str) -> None:
    # Only ever called after a fully successful poll (see
    # quo_ingest.poll_for_new_messages) -- clears any previously recorded
    # error at the same time, so a subsequent success promptly restores
    # the connection-state UI to "Connected" without a separate call.
    conn.execute(
        """
        INSERT INTO quo_poll_state (id, last_checkpoint, updated_at, last_error) VALUES (1, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET last_checkpoint = excluded.last_checkpoint, updated_at = excluded.updated_at, last_error = NULL
        """,
        (iso_ts, _now()),
    )
    conn.commit()


def record_poll_error(conn: sqlite3.Connection, message: str) -> None:
    """Records the most recent poll failure for the connection-state UI
    (Operator Console/CRM) -- including a failure on the very first-ever
    poll attempt, before any checkpoint exists. last_checkpoint is NOT
    NULL, so a fresh insert uses '' (empty string) as a checkpoint
    placeholder -- get_quo_checkpoint()/quo_ingest.py's `checkpoint or
    _initial_checkpoint()` already treats any falsy value the same as
    NULL, so this is behaviorally identical to "no checkpoint yet" and
    changes nothing about how the next successful poll computes its
    lookback window."""
    conn.execute(
        """
        INSERT INTO quo_poll_state (id, last_checkpoint, updated_at, last_error) VALUES (1, '', ?, ?)
        ON CONFLICT(id) DO UPDATE SET last_error = excluded.last_error, updated_at = excluded.updated_at
        """,
        (_now(), message),
    )
    conn.commit()


def get_poll_error(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT last_error FROM quo_poll_state WHERE id = 1").fetchone()
    return row[0] if row else None


def is_quo_message_processed(conn: sqlite3.Connection, message_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM quo_processed_messages WHERE message_id = ?", (message_id,)).fetchone()
    return row is not None


def mark_quo_message_processed(conn: sqlite3.Connection, message_id: str, conversation_id: Optional[str]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO quo_processed_messages (message_id, conversation_id, fetched_at) VALUES (?, ?, ?)",
        (message_id, conversation_id, _now()),
    )
    conn.commit()


def save_pending_and_mark_processed(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    conversation_id: Optional[str],
    group_label: str,
    raw_text: str,
    from_number: Optional[str],
    received_at: Optional[str],
) -> None:
    """Durably store a message's full content and mark its Quo id processed
    in one transaction -- a message id is never marked processed unless its
    content was already safely written. On any failure both writes are
    rolled back and the exception propagates, so the caller
    (quo_ingest.poll_for_new_messages) can hold its poll checkpoint back and
    retry this exact message on the next poll instead of silently losing it."""
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO quo_pending_messages (
                message_id, conversation_id, group_label, raw_text, from_number,
                received_at, fetched_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (message_id, conversation_id, group_label, raw_text, from_number, received_at, _now()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO quo_processed_messages (message_id, conversation_id, fetched_at) VALUES (?, ?, ?)",
            (message_id, conversation_id, _now()),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def get_pending_quo_messages(conn: sqlite3.Connection, limit: int = 10) -> List[dict]:
    """The oldest not-yet-actioned messages (pending or loaded), for display."""
    rows = conn.execute(
        "SELECT * FROM quo_pending_messages WHERE status IN ('pending', 'loaded') "
        "ORDER BY fetched_at ASC LIMIT ?",
        (limit,),
    ).fetchall()
    columns = [d[0] for d in conn.execute("SELECT * FROM quo_pending_messages LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


def count_pending_quo_messages(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded')"
    ).fetchone()[0]


def get_next_pending_quo_message(conn: sqlite3.Connection) -> Optional[dict]:
    """The single oldest not-yet-actioned message -- also recovers a row left
    'loaded' by an app/laptop restart that interrupted an in-progress review,
    rather than stranding it."""
    row = conn.execute(
        "SELECT * FROM quo_pending_messages WHERE status IN ('pending', 'loaded') "
        "ORDER BY fetched_at ASC, message_id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in conn.execute("SELECT * FROM quo_pending_messages LIMIT 0").description]
    return dict(zip(columns, row))


def mark_quo_pending_message_loaded(conn: sqlite3.Connection, message_id: str) -> None:
    conn.execute(
        "UPDATE quo_pending_messages SET status = 'loaded', loaded_at = ? WHERE message_id = ?",
        (_now(), message_id),
    )
    conn.commit()


def mark_quo_pending_message_actioned(conn: sqlite3.Connection, message_id: str) -> None:
    conn.execute(
        "UPDATE quo_pending_messages SET status = 'actioned', actioned_at = ? WHERE message_id = ?",
        (_now(), message_id),
    )
    conn.commit()


# ================================================================
# Shadow Mode -- see docs/SHADOW_MODE.md. shadow_episodes is the
# enrichable snapshot (mirrors `jobs`); shadow_events is the append-only
# log (mirrors `events`). Neither table is ever read or written by any
# operational command above this point -- Shadow Mode is purely
# additive and has zero effect on job status, routing, or any existing
# behavior.
# ================================================================


def shadow_schema_present(conn: sqlite3.Connection) -> bool:
    """Read-only, independent of check_schema_status()/require_current_schema()
    on purpose -- see _SCHEMA_MARKERS' own docstring for why shadow
    tables are deliberately excluded from that blanket check. Callers
    (Shadow Mode's own UI sections) use this to degrade gracefully
    (skip only Shadow Mode's own content) on a database that has not
    yet run the shadow-mode migration, without blocking any existing,
    unrelated functionality."""
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('shadow_episodes', 'shadow_events')"
        )
    }
    return {"shadow_episodes", "shadow_events"} <= tables


def whatsapp_registry_schema_present(conn: sqlite3.Connection) -> bool:
    """Same optional-capability pattern as shadow_schema_present() above:
    the multi-group WhatsApp registry/outgoing-audit tables are layered
    on top of core revision 2, not counted in SCHEMA_REVISION or
    _SCHEMA_MARKERS, and checked independently so a database missing
    only these degrades gracefully rather than being reported as
    needing an urgent core migration."""
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('whatsapp_group_registry', 'whatsapp_outgoing_audit')"
        )
    }
    return {"whatsapp_group_registry", "whatsapp_outgoing_audit"} <= tables

_SHADOW_EPISODE_JSON_FIELDS = {
    "shadow_reasons": "shadow_reasons_json",
    "validation_issues": "validation_issues_json",
    "commercial_update": "commercial_update_json",
}
_SHADOW_EPISODE_BOOL_FIELDS = frozenset({"human_review_required"})
_SHADOW_EPISODE_UPDATABLE_FIELDS = frozenset({
    "job_id", "ticket_or_pdl", "dispatcher_actor", "technician_id", "previous_job_status",
    "incoming_message_type", "normalized_intent",
    "shadow_suggested_action", "shadow_suggested_reply", "shadow_confidence", "shadow_reasons",
    "validation_issues", "human_review_required",
    "dispatcher_actual_action", "dispatcher_actual_reply", "actual_send_channel", "actual_send_confirmed_at",
    "commercial_update", "customer_response", "negotiation_outcome", "final_job_outcome",
})


def _shadow_episode_row_to_dict(columns: List[str], row: tuple) -> dict:
    d = dict(zip(columns, row))
    d["shadow_reasons"] = json.loads(d.pop("shadow_reasons_json") or "[]")
    d["validation_issues"] = json.loads(d.pop("validation_issues_json") or "[]")
    commercial_raw = d.pop("commercial_update_json")
    d["commercial_update"] = json.loads(commercial_raw) if commercial_raw else None
    d["human_review_required"] = bool(d["human_review_required"])
    d["eligible_for_export"] = bool(d["eligible_for_export"])
    return d


def _get_shadow_episode_row(conn: sqlite3.Connection, where_clause: str, params: tuple) -> Optional[dict]:
    cur = conn.execute(
        f"SELECT * FROM shadow_episodes WHERE {where_clause} ORDER BY created_at DESC LIMIT 1", params,
    )
    row = cur.fetchone()
    if row is None:
        return None
    columns = [d[0] for d in cur.description]
    return _shadow_episode_row_to_dict(columns, row)


def get_shadow_episode(conn: sqlite3.Connection, episode_id: str) -> Optional[dict]:
    return _get_shadow_episode_row(conn, "episode_id = ?", (episode_id,))


def get_shadow_episode_for_job(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    return _get_shadow_episode_row(conn, "job_id = ?", (job_id,))


def find_or_create_shadow_episode(
    conn: sqlite3.Connection, *, source: str, channel: str,
    ticket_or_pdl: Optional[str] = None, source_message_id: Optional[str] = None,
    pending_message_id: Optional[str] = None, job_id: Optional[int] = None,
    dispatcher_actor: Optional[str] = None, technician_id: Optional[int] = None,
    previous_job_status: Optional[str] = None, incoming_message_type: Optional[str] = None,
) -> dict:
    """Finds the one episode representing this job's entire operational
    story, or creates it. Correlation priority: job_id (once a job
    exists it is the one stable identity) -> pending_message_id ->
    (source, ticket_or_pdl) -> source_message_id -- the first of these
    that is both supplied and already on record wins. A job's whole
    chain (original message through negotiation through closing) shares
    one episode_id; later facts enrich it via update_shadow_episode()
    rather than creating a second episode. Never creates a jobs row,
    never requires job_id."""
    existing = None
    if job_id is not None:
        existing = _get_shadow_episode_row(conn, "job_id = ?", (job_id,))
    if existing is None and pending_message_id:
        existing = _get_shadow_episode_row(conn, "pending_message_id = ?", (pending_message_id,))
    if existing is None and ticket_or_pdl:
        existing = _get_shadow_episode_row(conn, "source = ? AND ticket_or_pdl = ?", (source, ticket_or_pdl))
    if existing is None and source_message_id:
        existing = _get_shadow_episode_row(conn, "source_message_id = ?", (source_message_id,))

    now = _now()
    if existing is not None:
        if job_id is not None and existing.get("job_id") is None:
            conn.execute(
                "UPDATE shadow_episodes SET job_id = ?, updated_at = ? WHERE episode_id = ?",
                (job_id, now, existing["episode_id"]),
            )
            conn.commit()
            existing["job_id"] = job_id
            existing["updated_at"] = now
        return existing

    episode_id = secrets.token_hex(16)
    conn.execute(
        """
        INSERT INTO shadow_episodes (
            episode_id, source, channel, source_message_id, pending_message_id, job_id,
            ticket_or_pdl, dispatcher_actor, technician_id, previous_job_status,
            incoming_message_type, shadow_reasons_json, validation_issues_json,
            human_review_required, eligible_for_export, review_status,
            created_at, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
        """,
        (
            episode_id, source, channel, source_message_id, pending_message_id, job_id,
            ticket_or_pdl, dispatcher_actor, technician_id, previous_job_status,
            incoming_message_type, json.dumps([]), json.dumps([]),
            ReviewStatus.UNREVIEWED.value, now, now, SHADOW_EPISODE_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return get_shadow_episode(conn, episode_id)


def update_shadow_episode(conn: sqlite3.Connection, episode_id: str, **fields) -> dict:
    """Incrementally enriches an existing episode -- never widens beyond
    _SHADOW_EPISODE_UPDATABLE_FIELDS (episode_id/source/channel/
    created_at/schema_version are immutable after creation). A missing
    later fact is simply never passed here; it stays NULL/UNKNOWN rather
    than being invented."""
    if not fields:
        current = get_shadow_episode(conn, episode_id)
        if current is None:
            raise ValueError(f"No shadow episode with id {episode_id}")
        return current
    unknown = set(fields) - _SHADOW_EPISODE_UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"Unknown shadow episode field(s): {sorted(unknown)}")

    set_clauses = []
    params: list = []
    for key, value in fields.items():
        if key in _SHADOW_EPISODE_JSON_FIELDS:
            column = _SHADOW_EPISODE_JSON_FIELDS[key]
            value = json.dumps(value)
        elif key in _SHADOW_EPISODE_BOOL_FIELDS:
            column = key
            value = 1 if value else 0
        else:
            column = key
        set_clauses.append(f"{column} = ?")
        params.append(value)
    set_clauses.append("updated_at = ?")
    params.append(_now())
    params.append(episode_id)

    conn.execute(f"UPDATE shadow_episodes SET {', '.join(set_clauses)} WHERE episode_id = ?", params)
    conn.commit()
    updated = get_shadow_episode(conn, episode_id)
    if updated is None:
        raise ValueError(f"No shadow episode with id {episode_id}")
    return updated


def get_shadow_event_by_idempotency_key(conn: sqlite3.Connection, idempotency_key: str) -> Optional[dict]:
    """Read-side counterpart to record_shadow_event()'s dedup check --
    lets a caller determine beforehand whether a call would be a replay,
    for ActionResult.idempotent_replay reporting. Mirrors
    get_event_by_idempotency_key()."""
    row = conn.execute("SELECT * FROM shadow_events WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in conn.execute("SELECT * FROM shadow_events LIMIT 0").description]
    d = dict(zip(columns, row))
    d["payload"] = json.loads(d.pop("payload_json"))
    return d


def record_shadow_event(
    conn: sqlite3.Connection, *, episode_id: str, event_type: str, channel: str,
    payload: dict, actor: Optional[str] = None, idempotency_key: Optional[str] = None,
) -> int:
    """Append-only -- one row per shadow action, never updated or
    deleted; a corrected commercial update is a new row, the original
    stays exactly as recorded. Idempotent when idempotency_key is
    supplied (same partial-unique-index pattern as events.idempotency_key
    -- see docs/EVENT_CONTRACT.md): a second call with the same key
    returns the existing row's id instead of inserting a duplicate."""
    if idempotency_key:
        existing = conn.execute(
            "SELECT id FROM shadow_events WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            return existing[0]
    now = _now()
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            """
            INSERT INTO shadow_events (episode_id, event_type, channel, actor, payload_json, idempotency_key, created_at, schema_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (episode_id, event_type, channel, actor, json.dumps(payload), idempotency_key, now, SHADOW_EPISODE_SCHEMA_VERSION),
        )
        event_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        if idempotency_key:
            existing = conn.execute(
                "SELECT id FROM shadow_events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return existing[0]
        raise
    except Exception:
        conn.rollback()
        raise
    return event_id


def get_shadow_episode_events(conn: sqlite3.Connection, episode_id: str) -> List[dict]:
    """Full chronological log for one episode -- every SHADOW_OBSERVATION/
    SHADOW_SUGGESTION/DISPATCHER_DECISION/COMMERCIAL_UPDATE/
    CUSTOMER_RESPONSE/NEGOTIATION_OUTCOME/SHADOW_REVIEWED/SHADOW_EXCLUDED
    ever recorded for this episode, oldest first -- including every
    superseded commercial update, never collapsed to just the latest."""
    rows = conn.execute(
        "SELECT * FROM shadow_events WHERE episode_id = ? ORDER BY id ASC", (episode_id,)
    ).fetchall()
    columns = [d[0] for d in conn.execute("SELECT * FROM shadow_events LIMIT 0").description]
    events = []
    for row in rows:
        d = dict(zip(columns, row))
        d["payload"] = json.loads(d.pop("payload_json"))
        events.append(d)
    return events


def get_latest_verified_commercial_update_for_job(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    """The latest technician commercial update the dispatcher actually
    reviewed and saved for this job (never a stale/superseded one --
    commercial_update_json always holds the latest; the complete history
    including every superseded quote remains in shadow_events, never
    lost). Never reads across jobs. Returns None if no commercial update
    has ever been recorded for this job."""
    episode = get_shadow_episode_for_job(conn, job_id)
    if episode is None:
        return None
    return episode.get("commercial_update")


def set_shadow_review_status(conn: sqlite3.Connection, episode_id: str, review_status: str, reviewed_by: Optional[str] = None) -> dict:
    """The only way an episode becomes export-eligible -- see
    shadow.EXPORTABLE_REVIEW_STATES. Also records a SHADOW_REVIEWED (or
    SHADOW_EXCLUDED, for EXCLUDE) shadow_event so the review decision
    itself is auditable."""
    valid_statuses = {s.value for s in ReviewStatus}
    if review_status not in valid_statuses:
        raise ValueError(f"Unknown review status: {review_status!r} -- must be one of {sorted(valid_statuses)}")
    now = _now()
    eligible = review_status in {s.value for s in EXPORTABLE_REVIEW_STATES}
    conn.execute(
        "UPDATE shadow_episodes SET review_status = ?, eligible_for_export = ?, reviewed_at = ?, updated_at = ? WHERE episode_id = ?",
        (review_status, 1 if eligible else 0, now, now, episode_id),
    )
    conn.commit()
    updated = get_shadow_episode(conn, episode_id)
    if updated is None:
        raise ValueError(f"No shadow episode with id {episode_id}")
    event_type = ShadowEventType.SHADOW_EXCLUDED.value if review_status == ReviewStatus.EXCLUDE.value else ShadowEventType.SHADOW_REVIEWED.value
    record_shadow_event(
        conn, episode_id=episode_id, event_type=event_type, channel=ShadowChannel.CRM_DISPATCHER_ACTION.value,
        payload={"review_status": review_status, "reviewed_by": reviewed_by}, actor=reviewed_by,
    )
    return updated


def confirm_shadow_reply_sent(conn: sqlite3.Connection, episode_id: str, sent_channel: str, actor: Optional[str] = None) -> dict:
    """Records that the dispatcher actually sent a prepared commercial
    reply -- this module has no outbound capability of any kind, so
    this call only ever documents a send that already happened outside
    this application (in the real Quo or company-WhatsApp thread).
    `sent_channel` is descriptive only (e.g. "Quo", "WhatsApp company
    thread")."""
    now = _now()
    update_shadow_episode(conn, episode_id, actual_send_channel=sent_channel, actual_send_confirmed_at=now)
    record_shadow_event(
        conn, episode_id=episode_id, event_type=ShadowEventType.DISPATCHER_REPLY_CONFIRMED.value,
        channel=ShadowChannel.CRM_DISPATCHER_ACTION.value, actor=actor, payload={"sent_channel": sent_channel},
    )
    updated = get_shadow_episode(conn, episode_id)
    if updated is None:
        raise ValueError(f"No shadow episode with id {episode_id}")
    return updated


def list_shadow_episodes(conn: sqlite3.Connection, *, limit: int = 50) -> List[dict]:
    """Most-recently-updated first -- backs the read-only Shadow Review
    screen. No customer data is masked here; callers displaying this to
    a human review it exactly like any other operational screen (see
    luxor_desk_shared.mask_sensitive() usage in the Shadow Review page)."""
    cur = conn.execute("SELECT * FROM shadow_episodes ORDER BY updated_at DESC LIMIT ?", (limit,))
    columns = [d[0] for d in cur.description]
    return [_shadow_episode_row_to_dict(columns, row) for row in cur.fetchall()]


def list_shadow_episodes_for_export(conn: sqlite3.Connection) -> List[dict]:
    """Only VERIFIED/CORRECTED episodes -- see set_shadow_review_status().
    An UNREVIEWED, EXCLUDE, SENSITIVE, or INCOMPLETE episode is never
    returned here, regardless of what other fields it has filled in."""
    cur = conn.execute("SELECT * FROM shadow_episodes WHERE eligible_for_export = 1 ORDER BY created_at ASC")
    columns = [d[0] for d in cur.description]
    return [_shadow_episode_row_to_dict(columns, row) for row in cur.fetchall()]


# ---------------------------------------------------------------------
# Live Operations / health read models.
#
# These exist here rather than in operator_api.py because that module
# deliberately owns NO SQL -- every persisted fact goes through
# storage.py, which structurally rules out a hidden write path in the
# HTTP layer (see test_operator_api_module_owns_no_sql). All of the
# following are strictly read-only.
# ---------------------------------------------------------------------

def observation_totals(conn: sqlite3.Connection) -> tuple:
    """(total observations, most recent received_at) -- backs the health
    bar's "last message" and total counters."""
    row = conn.execute(
        "SELECT COUNT(*), MAX(received_at) FROM whatsapp_pending_observations"
    ).fetchone()
    return (row[0], row[1]) if row else (0, None)


def count_observations_since(conn: sqlite3.Connection, since_iso: str) -> int:
    """Observations received at or after an ISO timestamp -- backs
    "observations today"."""
    row = conn.execute(
        "SELECT COUNT(*) FROM whatsapp_pending_observations WHERE received_at >= ?",
        (since_iso,),
    ).fetchone()
    return row[0] if row else 0


def integrity_snapshot(conn: sqlite3.Connection) -> dict:
    """PRAGMA quick_check + foreign_key_check, surfaced to the operator.

    Returns the failure text rather than raising: a health bar that goes
    blank when the database is unhappy is worse than one that says so.
    """
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
        violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        return {"integrity": result, "foreign_key_violations": violations}
    except sqlite3.Error as exc:
        return {"integrity": f"check failed: {exc}", "foreign_key_violations": None}


# Owner-confirmed intake and dispatch groups only. Unconfirmed groups are
# excluded at the source: an unmapped group must never contribute
# evidence toward a routing suggestion.
_CONFIRMED_ROUTING_SQL = """
    SELECT o.text, o.received_at,
           r.role, r.company, r.service_area,
           t.name AS technician_name
    FROM whatsapp_pending_observations o
    JOIN whatsapp_group_registry r ON r.group_id = o.group_id
    LEFT JOIN technicians t ON t.id = r.technician_id
    WHERE r.confirmed_by_owner = 1
      AND r.role IN ('COMPANY_JOB_INTAKE', 'TECH_DISPATCH')
"""


def list_confirmed_routing_observations(conn: sqlite3.Connection) -> List[dict]:
    """Raw material for the read-only Apprentice evidence aggregation.
    Read-only, and confirmed groups only."""
    cur = conn.execute(_CONFIRMED_ROUTING_SQL)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]
