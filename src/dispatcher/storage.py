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
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_number TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            latest_fields_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
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
    conn.commit()


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

    row = conn.execute(
        "SELECT id, latest_fields_json FROM jobs WHERE ticket_number = ?", (ticket,)
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
) -> int:
    now = _now()
    ticket = job.ticket_number.value
    job_id = classify_result.existing_job_id
    fields_snapshot = json.dumps(_current_fields(job))

    if job_id is None and ticket:
        cur = conn.execute(
            "INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ticket, job.source.value, fields_snapshot, now, now),
        )
        job_id = cur.lastrowid
    elif job_id is not None:
        conn.execute(
            "UPDATE jobs SET latest_fields_json = ?, updated_at = ? WHERE id = ?",
            (fields_snapshot, now, job_id),
        )

    conn.execute(
        """
        INSERT INTO events (
            job_id, ticket_number, raw_message, message_type, source,
            confidence, confidence_reasons_json, decision_verdict,
            decision_reasons_json, suggested_quo_reply, luxor_message,
            dispatcher_edits, final_action, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id, ticket, job.raw_text, classify_result.message_type.value, job.source.value,
            job.overall_confidence, json.dumps(job.status_reasons),
            decision.verdict.value if decision else None,
            json.dumps(decision.reasons) if decision else None,
            suggested_quo_reply, luxor_message, dispatcher_edits, final_action, now,
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
