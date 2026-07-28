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
from .rules.service_areas import ServiceAreaRecord, find_service_area
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
    # Dispatcher-initiated (not derived from an inbound message classification):
    # a technician was assigned, reassigned, or removed for this job. See
    # assign_technician() and derive_job_status().
    ASSIGNMENT = "ASSIGNMENT"


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
    _add_job_status_columns_and_backfill(conn)
    _add_technician_assignment_support(conn)
    _add_whatsapp_handoff_support(conn)
    _add_outbound_log_support(conn)
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
    turned on when every known foreign key relationship is currently
    clean (cheap indexed checks), so enforcement can never itself break on
    data it hasn't verified."""
    event_orphans = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id IS NOT NULL AND job_id NOT IN (SELECT id FROM jobs)"
    ).fetchone()[0]
    technician_orphans = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE assigned_technician_id IS NOT NULL "
        "AND assigned_technician_id NOT IN (SELECT id FROM technicians)"
    ).fetchone()[0]
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

_APPROVE_LIKE_ACTIONS = frozenset({"Approve", "Edit"})

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

    if final_action == "Handle Manually":
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
        if final_action in _APPROVE_LIKE_ACTIONS:
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
        if final_action in ("Assign", "Reassign", "Remove"):
            return prev
        return "Needs Review"

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
) -> int:
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

    cur = conn.execute(
        """
        INSERT INTO events (
            job_id, ticket_number, raw_message, message_type, source,
            confidence, confidence_reasons_json, decision_verdict,
            decision_reasons_json, suggested_quo_reply, luxor_message,
            dispatcher_edits, final_action, created_at, channel
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id, ticket, job.raw_text, message_type_value, job.source.value,
            job.overall_confidence, json.dumps(job.status_reasons),
            decision.verdict.value if decision else None,
            json.dumps(decision.reasons) if decision else None,
            suggested_quo_reply, luxor_message, dispatcher_edits, final_action, now, channel,
        ),
    )
    event_id = cur.lastrowid

    if job_id is not None and new_status is not None:
        conn.execute(
            "UPDATE jobs SET status = ?, last_event_id = ?, last_event_at = ? WHERE id = ?",
            (new_status, event_id, now, job_id),
        )

    conn.commit()
    return job_id


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
            technician_id=None, final_action="Remove",
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
            tech_areas_lower = {a.lower() for a in tech["areas"]}
            if not tech["areas"]:
                problems.append(f"technician {tech['name']!r} has no configured coverage areas")
            elif (area_label or "").lower() not in tech_areas_lower:
                problems.append(
                    f"technician {tech['name']!r} does not cover service area "
                    f"{area_label!r} (covers: {', '.join(tech['areas'])})"
                )

    service_type = (fields.get("service_type") or "").lower()
    if tech["skills"]:
        if not service_type:
            problems.append(f"job has no service type on record -- cannot verify technician {tech['name']!r}'s configured skills")
        elif not any(skill.lower() in service_type for skill in tech["skills"]):
            problems.append(f"technician {tech['name']!r}'s configured skills don't cover this job's service type")

    vehicle = (fields.get("vehicle") or "").lower()
    if tech["vehicle_restrictions"]:
        if not vehicle:
            problems.append(
                f"job has no vehicle info on record -- cannot verify technician {tech['name']!r}'s configured vehicle restrictions"
            )
        elif any(r.lower() in vehicle for r in tech["vehicle_restrictions"]):
            problems.append(f"technician {tech['name']!r} is explicitly restricted from this job's vehicle")

    if problems:
        return _apply_assignment_decision(
            conn, job_id=job_id, source=source, ticket_number=ticket_number,
            technician_id=None, final_action="Handle Manually",
            reason="; ".join(problems),
        )

    action = "Reassign" if current_technician_id and current_technician_id != technician_id else "Assign"
    return _apply_assignment_decision(
        conn, job_id=job_id, source=source, ticket_number=ticket_number,
        technician_id=technician_id, final_action=action,
        reason=f"{'Reassigned' if action == 'Reassign' else 'Assigned'} to {tech['name']}.",
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
    result_technician_id = technician_id if final_action in ("Assign", "Reassign") else None
    if final_action in ("Assign", "Reassign", "Remove"):
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

    outcome_map = {"Assign": "assigned", "Reassign": "reassigned", "Remove": "removed", "Handle Manually": "handle_manually"}
    return AssignmentResult(
        outcome=outcome_map[final_action], reason=reason,
        technician_id=result_technician_id, technician_name=tech_name,
        dispatch_group=tech_dispatch_group, closing_group=tech_closing_group,
    )


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
