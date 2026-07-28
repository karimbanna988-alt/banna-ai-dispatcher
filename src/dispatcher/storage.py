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
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

from .models import ExtractedJob
from .parser import REQUIRED_FIELDS
from .rules import Decision
from .updates import map_status_to_reply

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "dispatcher.db"


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


@dataclass
class ClassifyResult:
    message_type: MessageType
    existing_job_id: Optional[int]
    conflicting_fields: List[str] = field(default_factory=list)
    reason: str = ""


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the Streamlit UI reruns the script on a fresh
    # thread each time while reusing one cached connection (st.cache_resource
    # in app.py) -- this is a single local dispatcher session, not concurrent
    # multi-writer access, so relaxing SQLite's default thread affinity here
    # is safe.
    conn = sqlite3.connect(path, check_same_thread=False)
    _ensure_schema(conn)
    _configure_foreign_key_enforcement(conn)
    return conn


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
    turned on when events.job_id is currently known-clean against jobs.id
    (cheap indexed check via idx_events_job_id_id + the jobs primary key),
    so enforcement can never itself break on data it hasn't verified."""
    orphans = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id IS NOT NULL AND job_id NOT IN (SELECT id FROM jobs)"
    ).fetchone()[0]
    if orphans == 0:
        conn.execute("PRAGMA foreign_keys = ON")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_fields(job: ExtractedJob) -> dict:
    return {name: fv.value for name, fv in job.all_fields().items()}


def _looks_like_closing(raw_text: str) -> bool:
    return bool(re.search(r"\bclosed\b", raw_text.lower()) and re.search(r"\$\s*\d", raw_text))


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

    if "cancel" in text_lower:
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
) -> int:
    now = _now()
    ticket = job.ticket_number.value
    fields_snapshot = json.dumps(_current_fields(job))

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
        conn.execute(
            """
            INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (source, ticket_number) DO UPDATE SET
                latest_fields_json = excluded.latest_fields_json,
                updated_at = excluded.updated_at
            """,
            (ticket, job.source.value, fields_snapshot, now, now),
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

    conn.execute(
        """
        INSERT INTO events (
            job_id, ticket_number, raw_message, message_type, source,
            confidence, confidence_reasons_json, decision_verdict,
            decision_reasons_json, suggested_quo_reply, luxor_message,
            dispatcher_edits, final_action, created_at, channel
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id, ticket, job.raw_text, classify_result.message_type.value, job.source.value,
            job.overall_confidence, json.dumps(job.status_reasons),
            decision.verdict.value if decision else None,
            json.dumps(decision.reasons) if decision else None,
            suggested_quo_reply, luxor_message, dispatcher_edits, final_action, now, channel,
        ),
    )
    conn.commit()
    return job_id


def get_audit_timeline(conn: sqlite3.Connection, ticket_number: str) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM events WHERE ticket_number = ? ORDER BY id ASC", (ticket_number,)
    ).fetchall()
    columns = [d[0] for d in conn.execute("SELECT * FROM events LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


def get_quo_checkpoint(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT last_checkpoint FROM quo_poll_state WHERE id = 1").fetchone()
    return row[0] if row else None


def set_quo_checkpoint(conn: sqlite3.Connection, iso_ts: str) -> None:
    conn.execute(
        """
        INSERT INTO quo_poll_state (id, last_checkpoint, updated_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET last_checkpoint = excluded.last_checkpoint, updated_at = excluded.updated_at
        """,
        (iso_ts, _now()),
    )
    conn.commit()


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
