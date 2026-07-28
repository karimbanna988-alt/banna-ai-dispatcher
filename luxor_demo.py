"""LUXOR LOCKSMITH -- Live Dispatcher Preview (temporary, read-only, shadow mode).

Reuses the project's existing GET-only Quo pipeline exactly as built --
storage.connect(), dispatcher.quo_client.QuoClient, and
dispatcher.quo_ingest.poll_for_new_messages() -- rather than a second
client/poller/parser/persistence layer. This file adds no new way to
reach Quo or the database; it only adds a UI in front of what already
exists.

Two connections, deliberately kept separate and narrow in scope:
- A read-only connection (SQLite's own `mode=ro` URI -- any write attempt
  raises sqlite3.OperationalError at the database engine level) used for
  every query that renders the page.
- A normal read-write connection, opened only inside the "Refresh live
  jobs from Quo" button handler and closed immediately after, used for
  exactly one call: the existing, unmodified quo_ingest.poll_for_new_messages().
  That call only ever writes poll/dedup bookkeeping (quo_pending_messages,
  quo_processed_messages, quo_poll_state) -- it has never created a jobs
  or events row in this codebase. Turning a pending Quo message into a
  full job record still requires a dispatcher reviewing and approving it
  in the internal tool; this demo has no Approve/Edit/Reject/Assign/
  Delete/Close/Send control of any kind, so it cannot do that.

Quo access stays strictly GET-only: poll_for_new_messages() is the same
function already covered by tests/test_quo_ingest.py and
tests/test_app_integration.py -- direct polling of the four configured
dispatch-group contacts (never /v1/conversations discovery), only
direction=="incoming" messages queued, CLOSINGS outgoing replies
skipped, existing message-ID + checkpoint deduplication preserved.
Nothing in this file, or in the modules it calls, is capable of a Quo
POST/PUT/PATCH/DELETE.

Password-gated via the LUXOR_DEMO_PASSWORD environment variable only --
never hardcoded, never logged, never committed.

Layout is mobile-first (primary viewers use phones): a responsive
CSS grid for the metrics row, bordered single-column cards for jobs
instead of a wide table, and a full-width refresh button. This is
display-layer only -- the query/masking/status-bucket functions below
are unchanged by it.
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

from dispatcher import quo_client, storage
from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError
from dispatcher.quo_ingest import poll_for_new_messages

PASSWORD_ENV_VAR = "LUXOR_DEMO_PASSWORD"

st.set_page_config(page_title="LUXOR LOCKSMITH -- Live Dispatcher Preview", layout="wide")

st.markdown(
    """
    <style>
    /* Metrics row: 2-column grid on phones, 5-column on wider screens. */
    .st-key-luxor_overview_metrics [data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 0.75rem !important;
    }
    .st-key-luxor_overview_metrics [data-testid="stColumn"] {
        width: 100% !important;
        min-width: 0 !important;
        flex: unset !important;
    }
    @media (min-width: 640px) {
        .st-key-luxor_overview_metrics [data-testid="stHorizontalBlock"] {
            grid-template-columns: repeat(5, 1fr) !important;
        }
    }
    /* Comfortable touch target for the primary action on a phone. */
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
    """Redacts phone numbers and extensions from freeform text. Quo
    Contact IDs and API identifiers are never masked here because they
    never appear in anything this file reads or displays -- the only
    Quo-related call this file makes is the existing poll function, whose
    result is never rendered verbatim (only a count and a timestamp)."""
    if not text:
        return text
    text = _PHONE_RE.sub("[phone masked]", text)
    text = _EXT_RE.sub("[extension masked]", text)
    return text


def masked_event(event: dict) -> dict:
    result = dict(event)
    for field in _TEXT_FIELDS_TO_MASK:
        result[field] = mask_sensitive(result.get(field))
    return result


# --------------------------------------------------- Read-only data access
def get_readonly_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Opens the database in SQLite's own read-only URI mode -- any write
    attempt against this connection raises sqlite3.OperationalError at
    the database engine level, regardless of what application code does.
    This is the same kind of structural (not just conventional) guarantee
    the rest of this project already uses for Quo's GET-only access.
    Never used for the refresh action -- see module docstring.

    Deliberately reads the path from storage.DB_PATH (the same source of
    truth the write connection below already uses) rather than a second,
    separate constant in this file -- one path, two connections, both
    correctly redirectable to an isolated test database via the existing
    `monkeypatch.setattr(storage, "DB_PATH", ...)` pattern this whole
    test suite already relies on."""
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


def fetch_pending_review_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded')"
    ).fetchone()[0]


def fetch_last_refresh(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT updated_at FROM quo_poll_state WHERE id = 1").fetchone()
    return row[0] if row else None


# Deterministic, documented partition of the 8 fixed job statuses (see
# storage.VALID_JOB_STATUSES) into the 3 summary buckets used for the
# metrics row and the per-card attention badge. Every status falls into
# exactly one bucket.
_ACTIVE_STATUSES = {"New", "Approved", "In Progress"}
_MANUAL_REVIEW_STATUSES = {"Needs Review", "Handle Manually"}
_TERMINAL_STATUSES = {"Closed", "Cancelled", "Rejected"}


def summarize_statuses(jobs: list[dict]) -> tuple[int, int, int]:
    active = sum(1 for j in jobs if j["status"] in _ACTIVE_STATUSES)
    needs_review = sum(1 for j in jobs if j["status"] in _MANUAL_REVIEW_STATUSES)
    terminal = sum(1 for j in jobs if j["status"] in _TERMINAL_STATUSES)
    return active, needs_review, terminal


def status_badge(status: str) -> str:
    if status in _MANUAL_REVIEW_STATUSES:
        return f"\U0001F7E0 {status}"
    if status in _TERMINAL_STATUSES:
        return f"⚪ {status}"
    return f"\U0001F7E2 {status}"


def render_job_card(job: dict) -> None:
    """Single-column, phone-friendly card -- replaces a wide table row.
    Only fields already shown in the old table are shown here; the
    internal database id is never displayed, matching the old table."""
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


# ------------------------------------------------------------------- UI ---
st.title("LUXOR LOCKSMITH")
st.subheader("Live Dispatcher Preview")

badge_col1, badge_col2, badge_col3 = st.columns(3)
badge_col1.success("\U0001F7E2 LIVE QUO DATA")
badge_col2.info("\U0001F512 READ-ONLY")
badge_col3.warning("\U0001F6A7 UNDER DEVELOPMENT / SHADOW MODE")

st.markdown("**What this system does**")
st.markdown(
    "- Reads real incoming jobs through the existing Quo GET-only integration.\n"
    "- Extracts and structures job information.\n"
    "- Detects duplicates, cancellations, corrections, and status requests.\n"
    "- Checks service area and deterministic business restrictions.\n"
    "- Preserves each job and its chronological audit history.\n"
    "- Sends uncertain cases to manual review instead of guessing.\n"
    "- Prepares the foundation for technician routing and controlled dispatch."
)

st.markdown("**Operational value**")
st.markdown(
    "- Reduces repetitive copying and manual message interpretation.\n"
    "- Keeps jobs and updates in one searchable operational record.\n"
    "- Reduces the chance of missed, duplicated, or incorrectly handled jobs.\n"
    "- Makes previous decisions and edits traceable.\n"
    "- Allows management to view current activity without accessing Quo directly."
)

st.markdown("**Current limitations**")
st.markdown(
    "- This is shadow mode: dispatch continues normally through Quo -- nothing here is authoritative yet.\n"
    "- No outbound Quo writes of any kind (structurally GET-only).\n"
    "- No automated WhatsApp sending.\n"
    "- Technician roster and real routing configuration will be added later."
)

_password = os.environ.get(PASSWORD_ENV_VAR)
if not _password:
    st.error(f"This demo is not configured ({PASSWORD_ENV_VAR} is not set). Access refused.")
    st.stop()

if "luxor_demo_authenticated" not in st.session_state:
    st.session_state.luxor_demo_authenticated = False

if not st.session_state.luxor_demo_authenticated:
    entered = st.text_input("Password", type="password", key="luxor_demo_password_input")
    if st.button("Enter", type="primary"):
        if hmac.compare_digest(entered, _password):
            st.session_state.luxor_demo_authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

st.divider()

conn = get_readonly_connection()

last_refresh = fetch_last_refresh(conn)
pending_count = fetch_pending_review_count(conn)
jobs = fetch_jobs(conn)
status_counts = fetch_job_status_counts(conn)
active_count, needs_review_count, terminal_count = summarize_statuses(jobs)

# Priority order for a phone screen: what jobs exist, what needs
# attention, and when data last refreshed -- all in one glance, before
# anything else on the authenticated view.
st.subheader("Overview")
with st.container(key="luxor_overview_metrics"):
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total jobs", len(jobs))
    m2.metric("Active / open", active_count)
    m3.metric("Needs manual review", needs_review_count)
    m4.metric("Completed / cancelled", terminal_count)
    m5.metric("Last Quo refresh", last_refresh or "Never")

if "luxor_demo_refresh_message" not in st.session_state:
    st.session_state.luxor_demo_refresh_message = None

st.subheader("Live Quo refresh (GET-only)")
st.caption(
    "Same read-only polling pipeline as the internal tool's Quo Inbox (Live) tab -- direct GET "
    "requests only, existing dedup preserved. A new message joins the same pending-review queue "
    "the dispatcher already uses; a dispatcher must still approve it in the internal tool before "
    "it becomes a job here."
)
if st.button("Refresh live jobs from Quo", type="primary", key="luxor_demo_refresh", width="stretch"):
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        st.session_state.luxor_demo_refresh_message = ("error", "Quo is not configured in this environment.")
    else:
        write_conn = None
        try:
            write_conn = storage.connect()
            client = quo_client.QuoClient(api_key=api_key)
            outcome = poll_for_new_messages(write_conn, client)
            message = f"Refreshed -- found {len(outcome.queued)} new message(s) awaiting dispatcher review."
            st.session_state.luxor_demo_refresh_message = ("success", message)
            if outcome.failed_count:
                st.session_state.luxor_demo_refresh_message = (
                    "error",
                    f"{outcome.failed_count} message(s) could not be saved durably and will be retried on the next refresh.",
                )
        except QuoConfigError:
            st.session_state.luxor_demo_refresh_message = ("error", "Quo is not configured in this environment.")
        except QuoAPIError:
            st.session_state.luxor_demo_refresh_message = ("error", "Quo is temporarily unavailable. Please try again shortly.")
        finally:
            if write_conn is not None:
                write_conn.close()
    st.rerun()

if st.session_state.luxor_demo_refresh_message:
    _kind, _message = st.session_state.luxor_demo_refresh_message
    getattr(st, _kind)(_message)

st.caption(f"{pending_count} new Quo message(s) queued and awaiting dispatcher review (not yet a job record).")

with st.expander("Status breakdown"):
    st.table([{"Status": status, "Count": count} for status, count in status_counts])

st.subheader("Jobs")
if not jobs:
    st.info("No jobs yet.")
for _job in jobs:
    render_job_card(_job)

if jobs:
    st.subheader("Job audit timeline")
    st.caption("Optional -- pick a job to view its full chronological history.")
    job_options = {f"{j['source']} | {j['ticket_number']} ({j['status']})": j["id"] for j in jobs}
    selected_label = st.selectbox("Select a job", options=list(job_options.keys()), key="luxor_demo_job_select")
    if st.button("Show timeline", key="luxor_demo_show_timeline"):
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
