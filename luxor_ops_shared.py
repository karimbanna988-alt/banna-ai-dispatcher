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
               jobs.reconciliation_state,
               jobs.created_at, jobs.updated_at, jobs.last_event_at,
               jobs.latest_fields_json, technicians.name AS assigned_technician_name,
               technicians.dispatch_group AS assigned_technician_dispatch_group
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


def _split_multivalue(value) -> list:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def parse_technician_csv(csv_text: str, existing_names: set, service_areas: list) -> tuple:
    """The one shared technician-roster-import parser -- used by both
    luxor_ops_pages/technicians.py's file uploader and any offline
    preview script, so there is never a second CSV parser reimplementing
    this. Every area is validated against the canonical service-area
    configuration via resolve_area_text() and stored using its RESOLVED
    canonical label (e.g. "NJ" for "New Jersey", not the raw input text)
    -- the technician-eligibility matching functions in storage.py match
    on that exact canonical label, so storing anything else would
    silently make the technician unmatchable. A row with any
    unresolvable area is rejected entirely (not partially imported) with
    a specific reason. Returns (proposed_records, errors) -- nothing is
    written to any database by this function."""
    import csv
    import io

    from dispatcher.rules.service_areas import resolve_area_text

    proposed: list = []
    errors: list = []

    reader = csv.DictReader(io.StringIO(csv_text))
    required_columns = {"name", "areas"}
    if not required_columns.issubset(set(reader.fieldnames or [])):
        errors.append(f"CSV must include at least columns: {', '.join(sorted(required_columns))}")
        return proposed, errors

    for i, row in enumerate(reader, start=2):  # header is row 1
        name = (row.get("name") or "").strip()
        raw_areas = _split_multivalue(row.get("areas"))
        if not name:
            errors.append(f"Row {i}: missing name -- skipped")
            continue
        if not raw_areas:
            errors.append(f"Row {i}: {name!r} has no service areas -- skipped")
            continue
        if name in existing_names:
            errors.append(f"Row {i}: {name!r} is already on the roster -- skipped")
            continue

        resolved_areas = []
        area_rejected = False
        for raw_area in raw_areas:
            lookup = resolve_area_text(raw_area, service_areas)
            if lookup.status != "confirmed":
                errors.append(f"Row {i}: {name!r}'s area {raw_area!r} could not be confirmed ({lookup.reason}) -- row skipped")
                area_rejected = True
                break
            resolved_areas.append(lookup.record.city or lookup.record.state)
        if area_rejected:
            continue

        proposed.append({
            "name": name,
            "areas": resolved_areas,
            "skills": _split_multivalue(row.get("skills")),
            "vehicle_restrictions": _split_multivalue(row.get("vehicle_restrictions")),
            "dispatch_group": (row.get("dispatch_group") or "").strip() or None,
            "closing_group": (row.get("closing_group") or "").strip() or None,
        })

    return proposed, errors


# Main Leads messages carry an advertising/market label as the line
# right after the "Power By Ucallz App" header, before the Ticket: line
# (confirmed against real fixture shape -- see tests/fixtures/main_leads_*.txt).
# Display-only: never fed into parser.py/ExtractedJob or any
# classification/decision logic, so it carries zero risk to the existing,
# already-tested extraction pipeline.
_MARKET_LABEL_RE = re.compile(r"Power By Ucallz App\s*\n+\s*(?P<label>[^\n]+)\n+\s*(?:Ticket|PDL):", re.IGNORECASE)


def extract_market_label(raw_text: str) -> Optional[str]:
    match = _MARKET_LABEL_RE.search(raw_text or "")
    if not match:
        return None
    label = match.group("label").strip()
    return label or None


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


@st.cache_data(show_spinner=False, ttl=5)
def fetch_pending_messages(_conn: sqlite3.Connection, group_label: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Newest-first pending (not yet actioned) messages, optionally scoped
    to one operational stream. Cached with a short TTL (in addition to the
    explicit clear on refresh, see clear_pending_caches) since the
    Operator Console and Management CRM can run as separate processes
    sharing one database -- each has its own process-local cache, so a
    short TTL keeps the two reasonably in sync without any cross-process
    invalidation mechanism."""
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


MAX_ARCHIVE_PAGE_SIZE = 50


def _archive_where_clause(search: Optional[str], unmatched_only: bool) -> tuple[str, list]:
    clauses = ["status IN ('pending', 'loaded')"]
    params: list = []
    if search:
        clauses.append("(raw_text LIKE ? OR group_label LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if unmatched_only:
        clauses.append(
            "message_id NOT IN (SELECT message_id FROM dispatch_tracking WHERE dispatched_manually_at IS NOT NULL)"
        )
    return " AND ".join(clauses), params


def paginated_pending_messages(
    conn: sqlite3.Connection, page: int = 1, page_size: int = 25,
    search: Optional[str] = None, unmatched_only: bool = False,
) -> tuple[list[dict], int]:
    """Real, honest pagination over quo_pending_messages for Archive /
    Inbox Review -- newest first, capped page size, a real COUNT(*) of
    the matching set returned alongside the page (never just the size of
    the page actually fetched). Deliberately does NOT run these rows
    through get_live_dispatch_queue()'s correlation/dedup/
    shows_job_transformation gate -- that gate exists to decide what's
    actionable *right now* on the Jobs screen, and would silently hide
    exactly the "unmatched" messages this screen exists to surface.
    "unmatched_only" is a real, cheap SQL filter (no
    dispatch_tracking.dispatched_manually_at row), not a fabricated
    classification. Text is caller's responsibility to mask (see
    mask_sensitive) before ever reaching an HTTP response."""
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_ARCHIVE_PAGE_SIZE))
    where, params = _archive_where_clause(search, unmatched_only)
    total = conn.execute(f"SELECT COUNT(*) FROM quo_pending_messages WHERE {where}", params).fetchone()[0]
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT message_id, group_label, raw_text, received_at, fetched_at, status "
        f"FROM quo_pending_messages WHERE {where} "
        f"ORDER BY COALESCE(received_at, fetched_at) DESC LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    ).fetchall()
    columns = ["message_id", "group_label", "raw_text", "received_at", "fetched_at", "status"]
    return [dict(zip(columns, row)) for row in rows], total


def count_pending_messages(conn: sqlite3.Connection, group_label: Optional[str] = None) -> int:
    """The real, uncapped total of not-yet-actioned quo_pending_messages
    rows -- fetch_pending_messages() above caps its result at `limit`
    (default 100) for performance, which silently hides how large the
    real backlog is from anything that only reads its return value. This
    exists so the UI can be honest about that (e.g. "showing newest 100
    of 858") instead of implying the fetched page is the whole backlog."""
    if group_label:
        return conn.execute(
            "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded') AND group_label = ?",
            (group_label,),
        ).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded')"
    ).fetchone()[0]


@st.cache_data(show_spinner=False, ttl=5)
def fetch_last_refresh(_conn: sqlite3.Connection) -> Optional[str]:
    try:
        row = _conn.execute("SELECT updated_at FROM quo_poll_state WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        # Pre-migration database missing quo_poll_state -- read-only pages
        # must keep rendering regardless of schema staleness elsewhere.
        return None
    return row[0] if row else None


CONNECTION_DELAYED_THRESHOLD_SECONDS = 600  # 10 min
CONNECTION_STALLED_THRESHOLD_SECONDS = 3600  # 60 min


def connection_state(conn: sqlite3.Connection, api_key_configured: bool) -> tuple[str, str]:
    """One shared connection-state computation for every surface (Operator
    Console, Management CRM). States, in priority order:
    - "not_configured": no QUO_API_KEY at all
    - "error": the most recent poll attempt (from any surface -- console,
      CRM refresh button, or the standalone background poller script)
      recorded a failure via storage.record_poll_error(), not yet cleared
      by a subsequent success
    - "never_synced": no poll has ever succeeded
    - "stalled": last success was over an hour ago
    - "delayed": last success was over 10 minutes ago
    - "connected": everything nominal
    Never hides a failure behind stale-but-present data -- "error" is
    checked before staleness would otherwise just read as "delayed"."""
    if not api_key_configured:
        return "not_configured", "🔴 Not configured"

    error = storage.get_poll_error(conn)
    if error:
        return "error", "🔴 API error"

    last_refresh = fetch_last_refresh(conn)
    if not last_refresh:
        return "never_synced", "⚪ Never synced"

    try:
        text = last_refresh.replace("Z", "+00:00")
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return "never_synced", "⚪ Never synced"

    if elapsed > CONNECTION_STALLED_THRESHOLD_SECONDS:
        return "stalled", "🟠 Ingestion delayed"
    if elapsed > CONNECTION_DELAYED_THRESHOLD_SECONDS:
        return "delayed", "🟡 Ingestion delayed"
    return "connected", "🟢 Connected"


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
