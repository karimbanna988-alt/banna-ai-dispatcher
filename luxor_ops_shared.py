"""Shared read-only helpers for the LUXOR LOCKSMITH Operations CRM review
candidate (luxor_ops.py + luxor_ops_pages/*.py).

Reuses the project's existing pipeline exactly as built -- storage.py,
dispatcher.parser, dispatcher.rules, dispatcher.formatter,
dispatcher.pipeline, dispatcher.quo_client, dispatcher.quo_ingest. Adds no
second client, parser, classifier, decision engine, or persistence layer;
this module only composes those existing pieces for read-only display and
one non-mutating preview.

Import ordering note: every luxor_ops_pages/*.py file does its own
sys.path insert + load_dotenv before importing this module (matching
luxor_demo.py/luxor_demo_v2.py's existing pattern), so this module can
safely `from dispatcher import ...` at import time.

Terminology: the four configured Quo contacts polled are OPERATIONAL
STREAMS, not four companies. AGN, Lucky, and Main Leads are real Source
companies (dispatcher.models.Source); "Lucky — Closings & Replies" is
Lucky's own secondary reply channel, not a fourth company -- see
STREAMS below and rules/quo_dispatch_groups.yaml's own comments.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import streamlit as st

from dispatcher import pipeline, storage
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.rules.service_areas import load_service_areas
from dispatcher.storage import ClassifyResult, MessageType

PASSWORD_ENV_VAR = "LUXOR_DEMO_PASSWORD"
AUTH_SESSION_KEY = "luxor_ops_authenticated"

MOBILE_CSS = """
<style>
.st-key-luxor_ops_metrics [data-testid="stHorizontalBlock"] {
    display: grid !important;
    grid-template-columns: repeat(2, 1fr) !important;
    gap: 0.75rem !important;
}
.st-key-luxor_ops_metrics [data-testid="stColumn"] {
    width: 100% !important;
    min-width: 0 !important;
    flex: unset !important;
}
@media (min-width: 640px) {
    .st-key-luxor_ops_metrics [data-testid="stHorizontalBlock"] {
        grid-template-columns: repeat(5, 1fr) !important;
    }
}
button[kind="primary"] {
    min-height: 3rem;
    font-size: 1.05rem;
}
</style>
"""


# ------------------------------------------------------------- Masking ----
_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_EXT_RE = re.compile(r"\b(?:ext\.?|extension)\s*:?\s*\d+\b", re.IGNORECASE)


def mask_sensitive(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    text = _PHONE_RE.sub("[phone masked]", text)
    text = _EXT_RE.sub("[extension masked]", text)
    return text


def mask_customer_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + " ".join(f"{p[0]}." for p in parts[1:] if p)


# ------------------------------------------------------- Operational streams
@dataclass(frozen=True)
class OperationalStream:
    key: str
    display_name: str
    group_label: str  # matches quo_pending_messages.group_label / quo_dispatch_groups.yaml label
    source: Optional[str]  # matches jobs.source / Source enum value; None where not applicable


# Locked display names, per approved correction: four operational streams,
# not four companies. "Lucky — Closings & Replies" is Lucky's own
# secondary reply channel (see rules/quo_dispatch_groups.yaml's own
# comments on the "CLOSINGS" contact) -- its job-level metrics are marked
# not applicable rather than attributed to a fake source.
STREAMS: tuple[OperationalStream, ...] = (
    OperationalStream(key="agn", display_name="AGN", group_label="AGN", source="AGN"),
    OperationalStream(key="lucky", display_name="Lucky", group_label="Lucky", source="Lucky"),
    OperationalStream(key="main_leads", display_name="Main Leads", group_label="Main Leads 2", source="Main Leads"),
    OperationalStream(
        key="lucky_closings", display_name="Lucky — Closings & Replies", group_label="CLOSINGS", source=None,
    ),
)


# --------------------------------------------------- Cached rule resources
@st.cache_resource
def get_vehicle_rules():
    return load_vehicle_rules()


@st.cache_resource
def get_service_areas():
    return load_service_areas()


# --------------------------------------------------- Read-only data access
def get_readonly_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or storage.DB_PATH
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def fetch_jobs(conn: sqlite3.Connection) -> list[dict]:
    import json

    sql = """
        SELECT jobs.id, jobs.source, jobs.ticket_number, jobs.status,
               jobs.created_at, jobs.updated_at, jobs.last_event_at,
               jobs.latest_fields_json, technicians.name AS assigned_technician_name
        FROM jobs
        LEFT JOIN technicians ON technicians.id = jobs.assigned_technician_id
        ORDER BY COALESCE(jobs.last_event_at, jobs.updated_at) DESC
    """
    cur = conn.execute(sql)
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    for row in rows:
        fields = json.loads(row.pop("latest_fields_json") or "{}")
        city, state = fields.get("city"), fields.get("state")
        row["service_area"] = ", ".join(p for p in (city, state) if p) or "Unknown area"
        row["description"] = " -- ".join(p for p in (fields.get("service_type"), fields.get("vehicle")) if p) or "No description"
        row["customer_name"] = mask_customer_name(fields.get("customer_name"))
        row["customer_phone"] = mask_sensitive(fields.get("phone"))
        row["customer_address"] = fields.get("address")
    return rows


def fetch_pending_message_by_id(conn: sqlite3.Connection, message_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM quo_pending_messages WHERE message_id = ?", (message_id,)).fetchone()
    if row is None:
        return None
    columns = [d[0] for d in conn.execute("SELECT * FROM quo_pending_messages LIMIT 0").description]
    return dict(zip(columns, row))


def fetch_timeline(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    cur = conn.execute("SELECT * FROM events WHERE job_id = ? ORDER BY id ASC", (job_id,))
    columns = [d[0] for d in cur.description]
    events = [dict(zip(columns, row)) for row in cur.fetchall()]
    for event in events:
        event["raw_message"] = mask_sensitive(event.get("raw_message"))
        event["dispatcher_edits"] = mask_sensitive(event.get("dispatcher_edits"))
        event["luxor_message"] = mask_sensitive(event.get("luxor_message"))
        event["suggested_quo_reply"] = mask_sensitive(event.get("suggested_quo_reply"))
    return events


@st.cache_data(show_spinner=False)
def fetch_pending_messages(_conn: sqlite3.Connection, group_label: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Newest-first pending (not yet actioned) messages, optionally scoped
    to one operational stream. Cached; invalidated explicitly after a
    successful refresh (see clear_pending_caches)."""
    if group_label:
        rows = _conn.execute(
            "SELECT * FROM quo_pending_messages WHERE status IN ('pending', 'loaded') AND group_label = ? "
            "ORDER BY COALESCE(received_at, fetched_at) DESC LIMIT ?",
            (group_label, limit),
        ).fetchall()
    else:
        rows = _conn.execute(
            "SELECT * FROM quo_pending_messages WHERE status IN ('pending', 'loaded') "
            "ORDER BY COALESCE(received_at, fetched_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    columns = [d[0] for d in _conn.execute("SELECT * FROM quo_pending_messages LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


@st.cache_data(show_spinner=False)
def fetch_last_refresh(_conn: sqlite3.Connection) -> Optional[str]:
    row = _conn.execute("SELECT updated_at FROM quo_poll_state WHERE id = 1").fetchone()
    return row[0] if row else None


def clear_pending_caches() -> None:
    """Refresh only ever writes quo_pending_messages/quo_poll_state (see
    quo_ingest.poll_for_new_messages) -- only caches reading those tables
    need invalidating after a successful refresh."""
    fetch_pending_messages.clear()
    fetch_last_refresh.clear()


# ------------------------------------------------ Pipeline steps, in order
# process_raw_message() unconditionally calls the Luxor formatter -- to
# satisfy "the gate must run before calling the formatter," this module
# calls extract_job()/classify_incoming() (the same functions
# pipeline.process_raw_message already uses, in the same order) on their
# own first, evaluates the gate, and only calls the full pipeline
# (formatter included) when the gate passes. No parsing/classification
# logic is reimplemented -- both steps here are the existing public
# functions, called directly.
def extract_and_classify(raw_text: str, conn: sqlite3.Connection):
    job = extract_job(raw_text, get_vehicle_rules())
    classify_result = storage.classify_incoming(conn, job)
    return job, classify_result


def shows_job_transformation(classify_result: ClassifyResult, job) -> bool:
    """The critical message gate: a Luxor transformation may be shown only
    when the message correlates to an existing canonical job, or is a
    genuine new job with a usable ticket/reference. A bare "k"/"ok"/other
    acknowledgment has no ticket and no correlation -- classify_incoming's
    own first branch returns MessageType.NEW for it purely because there's
    no ticket to correlate against, which is NOT the same thing as a real
    new dispatch. MessageType.NEW alone is therefore not a sufficient
    condition on its own."""
    if classify_result.existing_job_id is not None:
        return True
    return classify_result.message_type == MessageType.NEW and job.ticket_number.value is not None


@st.cache_data(show_spinner=False)
def cached_extract_and_classify(raw_text: str, _conn: sqlite3.Connection):
    return extract_and_classify(raw_text, _conn)


@st.cache_data(show_spinner=False)
def cached_full_preview(raw_text: str, _conn: sqlite3.Connection) -> pipeline.ProcessResult:
    """Only ever called after shows_job_transformation() has confirmed this
    message represents a real job -- this is the point the Luxor formatter
    actually gets called, via the existing, unmodified
    pipeline.process_raw_message()."""
    return pipeline.process_raw_message(raw_text, get_vehicle_rules(), get_service_areas(), _conn, None)


# ------------------------------------------------------------ Status vocab
ACTIVE_STATUSES = {"New", "Approved", "In Progress"}
MANUAL_REVIEW_STATUSES = {"Needs Review", "Handle Manually"}
TERMINAL_STATUSES = {"Closed", "Cancelled", "Rejected"}

_MESSAGE_TYPE_LABELS = {
    MessageType.NEW.value: "New job",
    MessageType.DUPLICATE.value: "Duplicate",
    MessageType.CORRECTION.value: "Correction",
    MessageType.UPDATE_REQUEST.value: "Update request",
    MessageType.STATUS.value: "Status update",
    MessageType.CLOSING.value: "Closing",
    MessageType.CANCELLATION.value: "Cancellation",
    MessageType.CLOSING_WITHOUT_DISPATCH.value: "Closing (no prior dispatch)",
    MessageType.UNRECOGNIZED.value: "Unrecognized",
    MessageType.ASSIGNMENT.value: "Assignment",
}

ATTENTION_MESSAGE_TYPES = {
    MessageType.CANCELLATION.value, MessageType.UNRECOGNIZED.value, MessageType.CLOSING_WITHOUT_DISPATCH.value,
}


def message_type_label(message_type: str) -> str:
    return _MESSAGE_TYPE_LABELS.get(message_type, message_type.replace("_", " ").title())


def status_badge(status: str) -> str:
    if status in MANUAL_REVIEW_STATUSES:
        return f"\U0001F7E0 {status}"
    if status in TERMINAL_STATUSES:
        return f"⚪ {status}"
    return f"\U0001F7E2 {status}"


# HANDLE_MANUALLY is relabeled "Needs Review" for display only -- the
# stored internal verdict value is never changed.
_VERDICT_LABELS = {
    "ACCEPT": "Accept",
    "PASS": "Pass",
    "NEEDS_REVIEW": "Needs Review",
    "HANDLE_MANUALLY": "Needs Review",
}


def verdict_label(verdict: str) -> str:
    return _VERDICT_LABELS.get(verdict, verdict.replace("_", " ").title())


GATE_FAILURE_MESSAGE = "This message is not a complete job request and was not formatted."


def pending_review_state(job, classify_result: ClassifyResult) -> str:
    """Single shared vocabulary for a not-yet-actioned message's state,
    used on Company Inbox cards, the Job Workspace header, and the
    Activity feed. Grounded entirely in job.status_reasons /
    shows_job_transformation() -- nothing here is a separate judgment."""
    if not shows_job_transformation(classify_result, job):
        if job.status_reasons and any("missing" in r for r in job.status_reasons):
            return "Information missing"
        return "Other message"
    if classify_result.message_type.value in ATTENTION_MESSAGE_TYPES:
        return "Needs attention"
    return "Sent for review"


# --------------------------------------------------------- Activity labels
def humanize_event_label(event: dict) -> str:
    """Maps existing (message_type, final_action) combinations to the
    management-facing vocabulary from the approved wireframes. Every
    branch is grounded in a field that already exists on `events` --
    nothing here is inferred beyond what was actually recorded."""
    mtype = event.get("message_type")
    action = event.get("final_action")
    if mtype == MessageType.ASSIGNMENT.value:
        return {"Assign": "Assigned", "Reassign": "Reassigned", "Remove": "Assignment removed"}.get(action, "Assignment updated")
    if action == "Reject":
        return "Rejected"
    if action == "Handle Manually":
        return "Needs review"
    if mtype == MessageType.CANCELLATION.value and action in ("Approve", "Edit"):
        return "Cancelled"
    if mtype == MessageType.CLOSING.value and action in ("Approve", "Edit"):
        return "Closed"
    if mtype == MessageType.CORRECTION.value:
        return "Corrected"
    if mtype == MessageType.DUPLICATE.value:
        return "Duplicate noted"
    if mtype == MessageType.UPDATE_REQUEST.value:
        return "Update answered"
    if mtype == MessageType.STATUS.value and action in ("Approve", "Edit"):
        raw = (event.get("raw_message") or "").strip().lower()
        return "En route" if raw == "going" else "Status updated"
    if mtype in (MessageType.UNRECOGNIZED.value, MessageType.CLOSING_WITHOUT_DISPATCH.value):
        return "Needs review"
    if mtype == MessageType.NEW.value and action in ("Approve", "Edit"):
        return "Approved"
    return "Updated"


# ------------------------------------------------------------ Human dates
def humanize_timestamp(iso_str: Optional[str]) -> str:
    """Never shows a raw ISO timestamp -- relative for anything recent,
    a short absolute date otherwise."""
    if not iso_str:
        return "Unknown time"
    try:
        text = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return "Unknown time"

    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = delta.total_seconds()
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} hr ago"
    days = int(hours // 24)
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    # %-d (no leading zero) is a Unix-only strftime extension -- %d then a
    # manual lstrip keeps this correct on Windows too.
    return f"{dt.strftime('%b')} {dt.strftime('%d').lstrip('0') or '0'}, {dt.year}"


def date_bucket_label(iso_str: Optional[str]) -> str:
    """Today / Yesterday / Earlier -- calendar-day grouping for the
    Activity feed, independent of humanize_timestamp's relative wording."""
    if not iso_str:
        return "Earlier"
    try:
        text = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return "Earlier"
    today = datetime.now(timezone.utc).date()
    event_date = dt.date()
    if event_date == today:
        return "Today"
    if (today - event_date).days == 1:
        return "Yesterday"
    return "Earlier"


def fetch_all_events(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Global, cross-job chronological feed for the Activity page. events
    already carries ticket_number/source directly -- no join needed."""
    cur = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    columns = [d[0] for d in cur.description]
    events = [dict(zip(columns, row)) for row in cur.fetchall()]
    for event in events:
        event["raw_message"] = mask_sensitive(event.get("raw_message"))
    return events
