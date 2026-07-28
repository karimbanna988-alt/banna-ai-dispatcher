"""LUXOR LOCKSMITH -- AI Dispatcher Preview (candidate v2, read-only, shadow mode).

Corrective replacement for luxor_demo.py. That file only ever rendered
`jobs` (rows created by a dispatcher clicking Approve/Edit/Reject/Handle
Manually in the internal app) -- so a "Refresh" click correctly queued new
incoming Quo messages into the durable `quo_pending_messages` table, but
nothing about that refresh could ever become visible on screen, since
turning a pending message into a job requires a human review step this
demo intentionally never exposes. Confirmed against the real database
2026-07-28: 64 pending messages awaiting review, newest received far more
recently than the newest committed job.

This file fixes that gap by rendering the pending queue itself (Live
Inbox) and by running the *exact* existing extract -> classify -> decide ->
format pipeline (dispatcher.pipeline.process_raw_message) against a
selected pending message in a non-mutating preview (Job Preview) -- no
second parser, classifier, rules engine, or formatter. process_raw_message
never writes to storage; classify_incoming only SELECTs.

Two connections, same separation as luxor_demo.py:
- A read-only connection (SQLite `mode=ro` URI) for every display query.
- A write connection, opened only inside the refresh button handler and
  closed immediately after, used for exactly one call:
  quo_ingest.poll_for_new_messages() -- writes only quo_pending_messages /
  quo_processed_messages / quo_poll_state, never jobs/events.

Quo access stays strictly GET-only -- see luxor_demo.py's docstring for the
full guarantee; identical here, same function, same tests-covered code
path. Nothing in this file, or in the modules it calls, is capable of a
Quo POST/PUT/PATCH/DELETE.

Password-gated via the same LUXOR_DEMO_PASSWORD environment variable as
luxor_demo.py -- never hardcoded, never logged, never committed.

Mobile navigation is conditional, not tab-based: ordinary st.tabs execute
every tab body's Python code on every rerun regardless of which tab is
visually selected, which is wasteful once a page has five sections
including a per-message pipeline preview. st.segmented_control + an
if/elif chain means only the selected section's code runs at all.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

load_dotenv(Path(__file__).resolve().parent / ".env")

from dispatcher import pipeline, quo_client, storage
from dispatcher.parser import load_vehicle_rules
from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError
from dispatcher.quo_ingest import poll_for_new_messages
from dispatcher.rules import Verdict
from dispatcher.rules.service_areas import load_service_areas
from dispatcher.storage import MessageType

PASSWORD_ENV_VAR = "LUXOR_DEMO_PASSWORD"

st.set_page_config(page_title="LUXOR LOCKSMITH -- AI Dispatcher Preview", layout="wide")

st.markdown(
    """
    <style>
    .st-key-luxor_v2_metrics [data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 0.75rem !important;
    }
    .st-key-luxor_v2_metrics [data-testid="stColumn"] {
        width: 100% !important;
        min-width: 0 !important;
        flex: unset !important;
    }
    @media (min-width: 640px) {
        .st-key-luxor_v2_metrics [data-testid="stHorizontalBlock"] {
            grid-template-columns: repeat(5, 1fr) !important;
        }
    }
    button[kind="primary"] {
        min-height: 3rem;
        font-size: 1.05rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------- Masking ----
_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_EXT_RE = re.compile(r"\b(?:ext\.?|extension)\s*:?\s*\d+\b", re.IGNORECASE)
_TEXT_FIELDS_TO_MASK = ("raw_message", "dispatcher_edits", "luxor_message", "suggested_quo_reply")


def mask_sensitive(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    text = _PHONE_RE.sub("[phone masked]", text)
    text = _EXT_RE.sub("[extension masked]", text)
    return text


def mask_customer_name(name: Optional[str]) -> Optional[str]:
    """First name kept, remaining name parts reduced to an initial --
    enough for the owner to recognize a real customer without a full name
    being exposed in a temporary web demo."""
    if not name:
        return name
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + " ".join(f"{p[0]}." for p in parts[1:] if p)


def masked_event(event: dict) -> dict:
    result = dict(event)
    for field in _TEXT_FIELDS_TO_MASK:
        result[field] = mask_sensitive(result.get(field))
    return result


def short_preview(text: Optional[str], limit: int = 140) -> str:
    masked = mask_sensitive(text) or ""
    masked = " ".join(masked.split())
    if len(masked) > limit:
        return masked[:limit].rstrip() + "…"
    return masked


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


def fetch_job_status_counts(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    return conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY status").fetchall()


def fetch_jobs(conn: sqlite3.Connection) -> list[dict]:
    sql = """
        SELECT jobs.id, jobs.source, jobs.ticket_number, jobs.status,
               jobs.created_at, jobs.updated_at, jobs.last_event_at,
               jobs.latest_fields_json, technicians.name AS assigned_technician_name,
               events.channel AS latest_channel
        FROM jobs
        LEFT JOIN technicians ON technicians.id = jobs.assigned_technician_id
        LEFT JOIN events ON events.id = jobs.last_event_id
        ORDER BY COALESCE(jobs.last_event_at, jobs.updated_at) DESC
    """
    cur = conn.execute(sql)
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    for row in rows:
        fields = json.loads(row.pop("latest_fields_json") or "{}")
        city, state = fields.get("city"), fields.get("state")
        row["service_area"] = ", ".join(p for p in (city, state) if p) or "(unknown)"
        row["description"] = " -- ".join(p for p in (fields.get("service_type"), fields.get("vehicle")) if p) or "(none)"
    return rows


def fetch_timeline(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    cur = conn.execute("SELECT * FROM events WHERE job_id = ? ORDER BY id ASC", (job_id,))
    columns = [d[0] for d in cur.description]
    return [masked_event(dict(zip(columns, row))) for row in cur.fetchall()]


@st.cache_data(show_spinner=False)
def fetch_pending_messages(_conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """Newest-first, unlike storage.get_pending_quo_messages() (which is
    oldest-first, by design, for the internal app's FIFO review queue) --
    this is a read-only display ordering only, not a persistence change.
    Cached (invalidated explicitly after a successful refresh, see
    clear_pending_caches()) since this list backs both the Live Inbox and
    the Overview count, and is read on every section render."""
    rows = _conn.execute(
        "SELECT * FROM quo_pending_messages WHERE status IN ('pending', 'loaded') "
        "ORDER BY COALESCE(received_at, fetched_at) DESC LIMIT ?",
        (limit,),
    ).fetchall()
    columns = [d[0] for d in _conn.execute("SELECT * FROM quo_pending_messages LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


@st.cache_data(show_spinner=False)
def fetch_pending_review_count(_conn: sqlite3.Connection) -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded')"
    ).fetchone()[0]


@st.cache_data(show_spinner=False)
def fetch_last_refresh(_conn: sqlite3.Connection) -> Optional[str]:
    row = _conn.execute("SELECT updated_at FROM quo_poll_state WHERE id = 1").fetchone()
    return row[0] if row else None


def clear_pending_caches() -> None:
    """Refresh only ever writes quo_pending_messages/quo_poll_state -- only
    the caches reading those tables need invalidating. fetch_jobs() and
    fetch_job_status_counts() are deliberately left uncached (jobs table is
    small and refresh never touches it)."""
    fetch_pending_messages.clear()
    fetch_pending_review_count.clear()
    fetch_last_refresh.clear()


@st.cache_data(show_spinner=False)
def cached_preview(raw_text: str, _conn: sqlite3.Connection) -> pipeline.ProcessResult:
    """Non-mutating preview: the exact same extract -> classify -> decide ->
    format sequence app.py's Process tab uses, run against a pending
    message's text instead of a pasted one. classify_incoming() only
    SELECTs; nothing here writes to storage. Cached per message content
    since Live Inbox may render this for many messages in one render."""
    return pipeline.process_raw_message(raw_text, get_vehicle_rules(), get_service_areas(), _conn, None)


# ------------------------------------------------------ Status vocabulary -
# Deterministic, documented partition of the 8 fixed job statuses (see
# storage.VALID_JOB_STATUSES) into the 3 summary buckets used for the
# metrics row and the per-card badge. Every status falls into exactly one
# bucket.
_ACTIVE_STATUSES = {"New", "Approved", "In Progress"}
_MANUAL_REVIEW_STATUSES = {"Needs Review", "Handle Manually"}
_TERMINAL_STATUSES = {"Closed", "Cancelled", "Rejected"}

_MESSAGE_TYPE_LABELS = {
    MessageType.NEW.value: "New",
    MessageType.DUPLICATE.value: "Duplicate",
    MessageType.CORRECTION.value: "Correction",
    MessageType.UPDATE_REQUEST.value: "Update Request",
    MessageType.STATUS.value: "Status Update",
    MessageType.CLOSING.value: "Closing",
    MessageType.CANCELLATION.value: "Cancellation",
    MessageType.CLOSING_WITHOUT_DISPATCH.value: "Closing (No Prior Dispatch)",
    MessageType.UNRECOGNIZED.value: "Unrecognized",
    MessageType.ASSIGNMENT.value: "Assignment",
}

_ATTENTION_MESSAGE_TYPES = {
    MessageType.CANCELLATION.value, MessageType.UNRECOGNIZED.value, MessageType.CLOSING_WITHOUT_DISPATCH.value,
}
_ATTENTION_VERDICTS = {Verdict.NEEDS_REVIEW.value, Verdict.HANDLE_MANUALLY.value}


def summarize_statuses(jobs: list[dict]) -> tuple[int, int, int, int]:
    active = sum(1 for j in jobs if j["status"] in _ACTIVE_STATUSES)
    needs_review = sum(1 for j in jobs if j["status"] in _MANUAL_REVIEW_STATUSES)
    terminal = sum(1 for j in jobs if j["status"] in _TERMINAL_STATUSES)
    cancelled = sum(1 for j in jobs if j["status"] == "Cancelled")
    return active, needs_review, terminal, cancelled


def status_badge(status: str) -> str:
    if status in _MANUAL_REVIEW_STATUSES:
        return f"\U0001F7E0 {status}"
    if status in _TERMINAL_STATUSES:
        return f"⚪ {status}"
    return f"\U0001F7E2 {status}"


def assessment_emoji(message_type: str, verdict: str) -> str:
    if message_type in _ATTENTION_MESSAGE_TYPES or verdict in _ATTENTION_VERDICTS:
        return "\U0001F7E0"
    return "\U0001F7E2"


# ------------------------------------------------------------ Card render -
def render_job_card(job: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**{status_badge(job['status'])}**")
        st.write(f"\U0001F4CD {job['service_area']}")
        st.write(f"\U0001F527 {job['description']}")
        st.caption(
            f"{job['source']} · {job['latest_channel'] or '(no channel)'} · "
            f"{job['assigned_technician_name'] or 'Unassigned'}"
        )
        st.caption(f"Received {job['created_at']} · Updated {job['last_event_at'] or job['updated_at']}")
        with st.expander("Details"):
            st.write(f"**Ticket:** {job['ticket_number']}")
            st.write(f"**Source:** {job['source']}")
            st.write(f"**Status:** {job['status']}")
            st.write(f"**Assigned technician:** {job['assigned_technician_name'] or 'Unassigned'}")


def render_pending_message_card(item: dict, preview: Optional[pipeline.ProcessResult]) -> None:
    with st.container(border=True):
        badge = "\U0001F4E5 Pending" if item["status"] == "pending" else "\U0001F4E4 Loaded"
        st.markdown(f"**{badge}**")
        st.write(f"Source: {item['group_label']}")
        st.caption(f"Received {item['received_at'] or item['fetched_at']}")
        if preview is not None:
            mtype = preview.classify_result.message_type.value
            verdict = preview.decision.verdict.value
            emoji = assessment_emoji(mtype, verdict)
            st.write(f"{emoji} {_MESSAGE_TYPE_LABELS.get(mtype, mtype)} · {verdict.replace('_', ' ').title()}")
        st.caption(short_preview(item["raw_text"]))


# ------------------------------------------------------------------- UI ---
st.title("LUXOR LOCKSMITH")
st.subheader("AI Dispatcher Preview")

badge_col1, badge_col2, badge_col3 = st.columns(3)
badge_col1.success("\U0001F7E2 LIVE QUO DATA")
badge_col2.info("\U0001F512 READ-ONLY")
badge_col3.warning("\U0001F6E1 SHADOW MODE")

_password = os.environ.get(PASSWORD_ENV_VAR)
if not _password:
    st.error(f"This demo is not configured ({PASSWORD_ENV_VAR} is not set). Access refused.")
    st.stop()

if "luxor_v2_authenticated" not in st.session_state:
    st.session_state.luxor_v2_authenticated = False

if not st.session_state.luxor_v2_authenticated:
    entered = st.text_input("Password", type="password", key="luxor_v2_password_input")
    if st.button("Enter", type="primary"):
        if hmac.compare_digest(entered, _password):
            st.session_state.luxor_v2_authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

if "luxor_v2_refresh_message" not in st.session_state:
    st.session_state.luxor_v2_refresh_message = None

conn = get_readonly_connection()

section = st.segmented_control(
    "Section",
    options=["Overview", "Live Inbox", "Job Preview", "CRM Records", "Audit"],
    default="Overview",
    required=True,
    key="luxor_v2_section",
    label_visibility="collapsed",
)

# ================================================================ Overview
if section == "Overview":
    jobs = fetch_jobs(conn)
    pending = fetch_pending_messages(conn)
    pending_count = fetch_pending_review_count(conn)
    last_refresh = fetch_last_refresh(conn)
    active_count, needs_review_count, terminal_count, cancelled_count = summarize_statuses(jobs)
    newest_incoming = pending[0]["received_at"] if pending else None

    st.subheader("Overview")
    with st.container(key="luxor_v2_metrics"):
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Incoming visible", len(pending))
        m2.metric("Committed jobs", len(jobs))
        m3.metric("Needs attention", needs_review_count)
        m4.metric("Cancellations", cancelled_count)
        m5.metric("Last refresh", last_refresh or "Never")
    st.caption(f"Newest incoming Quo message: {newest_incoming or '(none yet)'}")
    st.caption(f"Pending review messages: {pending_count} · Active jobs: {active_count} · Completed/cancelled: {terminal_count}")

    st.markdown("**Quo → Extract → Check Rules → Luxor Format → Human Review → CRM Audit**")

    st.divider()
    st.subheader("Live Quo refresh (GET-only)")
    st.caption(
        "Polls the four configured dispatch-group contacts directly, queues only incoming messages, "
        "preserves existing dedup and checkpoint safety. Never sends, replies to, or modifies anything "
        "on Quo. New messages appear in Live Inbox immediately -- no dispatcher approval required to see them."
    )
    if st.button("Refresh live jobs from Quo", type="primary", key="luxor_v2_refresh", width="stretch"):
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            st.session_state.luxor_v2_refresh_message = ("error", "Quo is not configured in this environment.")
        else:
            write_conn = None
            try:
                write_conn = storage.connect()
                client = quo_client.QuoClient(api_key=api_key)
                outcome = poll_for_new_messages(write_conn, client)
                message = f"Refreshed -- found {len(outcome.queued)} new message(s), now visible in Live Inbox."
                st.session_state.luxor_v2_refresh_message = ("success", message)
                if outcome.failed_count:
                    st.session_state.luxor_v2_refresh_message = (
                        "error",
                        f"{outcome.failed_count} message(s) could not be saved durably and will be retried on the next refresh.",
                    )
            except QuoConfigError:
                st.session_state.luxor_v2_refresh_message = ("error", "Quo is not configured in this environment.")
            except QuoAPIError:
                st.session_state.luxor_v2_refresh_message = ("error", "Quo is temporarily unavailable. Please try again shortly.")
            finally:
                if write_conn is not None:
                    write_conn.close()
            clear_pending_caches()
        st.rerun()

    if st.session_state.luxor_v2_refresh_message:
        _kind, _message = st.session_state.luxor_v2_refresh_message
        getattr(st, _kind)(_message)

# ============================================================== Live Inbox
elif section == "Live Inbox":
    st.subheader("Live Inbox")
    st.caption(
        "Real incoming Quo messages, newest first -- shown here as soon as they're fetched, before any "
        "dispatcher review. Use Refresh on the Overview section to fetch new ones."
    )
    pending = fetch_pending_messages(conn)
    if not pending:
        st.info("No pending messages. Refresh from the Overview section to check for new ones.")
    for item in pending:
        preview = None
        try:
            preview = cached_preview(item["raw_text"], conn)
        except Exception:
            preview = None
        render_pending_message_card(item, preview)

# ============================================================= Job Preview
elif section == "Job Preview":
    st.subheader("Job Preview")
    st.caption(
        "Runs the existing parser, rules, and Luxor formatter against a real pending message -- "
        "a non-mutating preview. Nothing here is saved, approved, or sent."
    )
    pending = fetch_pending_messages(conn)
    if not pending:
        st.info("No pending messages to preview. Refresh from the Overview section first.")
    else:
        options = {
            f"{item['group_label']} · {item['received_at'] or item['fetched_at']}": item["message_id"]
            for item in pending
        }
        selected_label = st.selectbox("Select a pending message", options=list(options.keys()), key="luxor_v2_preview_select")
        selected_id = options[selected_label]
        selected_item = next(item for item in pending if item["message_id"] == selected_id)

        if st.button("Generate preview", type="primary", key="luxor_v2_generate_preview", width="stretch"):
            st.session_state.luxor_v2_previewed_message_id = selected_id

        if st.session_state.get("luxor_v2_previewed_message_id") == selected_id:
            result = cached_preview(selected_item["raw_text"], conn)
            job = result.job

            with st.container(border=True):
                st.markdown("**A. Original message**")
                st.caption(f"Source: {selected_item['group_label']} · Received {selected_item['received_at'] or selected_item['fetched_at']}")
                st.code(mask_sensitive(selected_item["raw_text"]) or "")

            with st.container(border=True):
                st.markdown("**B. Extracted job information**")
                phone = job.phone.value
                if phone and job.extension.value:
                    phone = f"{phone} ext {job.extension.value}"
                rows = [
                    ("Customer", mask_customer_name(job.customer_name.value) or "(not provided)"),
                    ("Phone", mask_sensitive(phone) or "(not provided)"),
                    ("Address", job.address.value or "(not provided)"),
                    ("Service type", job.service_type.value or "(not provided)"),
                    ("Job description", job.notes.value or "(not provided)"),
                    ("Vehicle", job.vehicle.value or "(not applicable)"),
                    ("Ticket / reference", job.ticket_number.value or "(not provided)"),
                    ("Source", job.source.value),
                ]
                for label, value in rows:
                    st.write(f"**{label}:** {value}")
                missing = result.decision.missing_information
                st.write(f"**Missing fields:** {', '.join(missing) if missing else 'none'}")
                st.write(f"**Confidence:** {job.overall_confidence}")
                if job.status_reasons:
                    st.caption("Reasons: " + "; ".join(job.status_reasons))

            with st.container(border=True):
                st.markdown("**C. System assessment**")
                mtype = result.classify_result.message_type.value
                verdict = result.decision.verdict.value
                emoji = assessment_emoji(mtype, verdict)
                st.write(f"{emoji} **Classification:** {_MESSAGE_TYPE_LABELS.get(mtype, mtype)}")
                st.write(f"**Result:** {verdict.replace('_', ' ').title()}")
                if verdict in _ATTENTION_VERDICTS:
                    st.warning("Marked for manual review -- not auto-accepted.")
                if result.decision.reasons:
                    st.caption("Reasons: " + "; ".join(result.decision.reasons))
                if result.classify_result.reason:
                    st.caption(f"Classification reason: {result.classify_result.reason}")
                if result.decision.restrictions_triggered:
                    st.caption("Restrictions: " + "; ".join(result.decision.restrictions_triggered))
                if result.decision.service_area:
                    st.caption(f"Service area: {result.decision.service_area}")

            with st.container(border=True):
                st.markdown("**D. Luxor Locksmith format**")
                st.caption("Prepared preview — not sent")
                st.code(result.luxor_message)

# ============================================================= CRM Records
elif section == "CRM Records":
    st.subheader("CRM Records")
    st.caption(
        "Committed, dispatcher-approved jobs only. A message here has already gone through human review "
        "in the internal tool -- see Live Inbox for messages still awaiting that review."
    )
    jobs = fetch_jobs(conn)
    status_counts = fetch_job_status_counts(conn)
    with st.expander("Status breakdown"):
        st.table([{"Status": status, "Count": count} for status, count in status_counts])
    if not jobs:
        st.info("No committed jobs yet.")
    for job in jobs:
        render_job_card(job)

# ===================================================================Audit
elif section == "Audit":
    st.subheader("Audit")
    st.caption("Chronological, append-only history for a committed job.")
    jobs = fetch_jobs(conn)
    if not jobs:
        st.info("No committed jobs yet.")
    else:
        job_options = {f"{j['source']} | {j['ticket_number']} ({j['status']})": j["id"] for j in jobs}
        selected_label = st.selectbox("Select a job", options=list(job_options.keys()), key="luxor_v2_audit_select")
        if st.button("Show timeline", key="luxor_v2_show_timeline"):
            timeline = fetch_timeline(conn, job_options[selected_label])
            if not timeline:
                st.info("No events found.")
            for event in timeline:
                label = f"{event['created_at']} -- {event['message_type']} -- {event['final_action']}"
                with st.expander(label):
                    st.write(f"**Channel:** {event.get('channel') or '(none)'}")
                    if event.get("raw_message"):
                        st.write(f"**Message:** {event['raw_message']}")
                    if event.get("dispatcher_edits"):
                        st.write(f"**Dispatcher edits:** {event['dispatcher_edits']}")
                    if event.get("suggested_quo_reply"):
                        st.write(f"**Suggested Quo reply:** {event['suggested_quo_reply']}")

conn.close()
