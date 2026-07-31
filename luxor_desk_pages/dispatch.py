"""New Jobs -- a minimal mobile dispatch action screen, not a CRM
dashboard. Every row here is a genuine quo_pending_messages record;
nothing is fabricated. Reuses luxor_desk_read_model for all data access
and dispatcher.storage/outbound for every write action -- the exact
same audit/tracking paths luxor_console_pages/dispatch_queue.py already
uses. Quo outbound stays disabled; WhatsApp stays manual copy/open only.

The 60-minute live-window/"Older unresolved jobs" split below is a
presentation rule only -- it never deletes, rejects, completes, or
otherwise modifies a pending record; it only changes which section a
row is listed under. See docs/operator_ui_blueprint.md's "New Jobs
mobile action screen" note for why this replaced the earlier workflow
strip / Needs action-Waiting-All view pills for this page specifically.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import luxor_desk_read_model as rm
import luxor_desk_shared as shared_desk
import luxor_ops_shared as shared
from dispatcher import outbound, quo_client, storage
from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError
from dispatcher.quo_ingest import poll_for_new_messages

if not shared_desk.is_authenticated():
    st.stop()

conn = storage.connect()

# ------------------------------------------------------------ Sync -------
if "desk_refresh_message" not in st.session_state:
    st.session_state.desk_refresh_message = None

sync_clicked = shared_desk.render_compact_new_jobs_header(conn)
if sync_clicked:
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        st.session_state.desk_refresh_message = ("error", "Quo is not configured in this environment.")
    else:
        try:
            client = quo_client.QuoClient(api_key=api_key)
            outcome = poll_for_new_messages(conn, client)
            st.session_state.desk_refresh_message = ("success", f"Synced -- {len(outcome.queued)} new message(s).")
        except QuoConfigError as exc:
            storage.record_poll_error(conn, str(exc))
            st.session_state.desk_refresh_message = ("error", "Quo is not configured.")
        except QuoAPIError as exc:
            storage.record_poll_error(conn, str(exc))
            st.session_state.desk_refresh_message = ("error", "Quo temporarily unavailable.")
        shared.clear_pending_caches()
    st.rerun()

if st.session_state.desk_refresh_message:
    _kind, _msg = st.session_state.desk_refresh_message
    getattr(st, _kind)(_msg)
    st.session_state.desk_refresh_message = None

if not shared_desk.require_current_schema(conn):
    conn.close()
    st.stop()

# --------------------------------------------------------- Queue build ---
live_entries, backlog_pending, other_messages = rm.get_live_dispatch_queue(conn)

FILTER_OPTIONS = ("All", "AGN", "Lucky", "Main Leads", "Closings & Replies", "Needs Review")


def entry_filter_match(e: rm.QueueEntry, name: str) -> bool:
    if name == "All":
        return True
    if name in ("AGN", "Lucky", "Main Leads"):
        return e.job.source.value == name
    if name == "Closings & Replies":
        return e.item["group_label"] == "CLOSINGS" or e.classify_result.message_type.value in rm.UPDATES_CLOSINGS_TYPES
    if name == "Needs Review":
        return (
            e.classify_result.message_type.value in shared.ATTENTION_MESSAGE_TYPES
            or bool(e.result.decision.missing_information)
            or bool(e.classify_result.conflicting_fields)
        )
    return True


def entry_identity(e: rm.QueueEntry) -> str:
    """The canonical representative's own message_id -- always unique
    per canonical entry (it's quo_pending_messages' primary key, and
    canonicalize() picks exactly one representative row per group), so
    this stays correct now that a (source, ticket) pair can legitimately
    canonicalize into several distinct lifecycle-event entries (original
    new job, a correction, a cancellation, ...) rather than always one."""
    return e.item["message_id"]


# The company/type filter is a supporting control, not part of the
# primary operating view -- its widget is instantiated later, inside a
# collapsed "Filters" expander placed after the selected job and
# compact queue, but its current value is read here (standard Streamlit
# pattern: session_state persists across reruns even before the widget
# that owns it is re-instantiated later in the same script run).
active_filter = st.session_state.get("desk_dispatch_filter", "All")
filtered = [e for e in live_entries if entry_filter_match(e, active_filter)]

# --------------------------------------------------- Live window split ---
# "Live" = received within the last 60 minutes -- a presentation cutoff
# only (see module docstring). Sorted newest-first within each section,
# per the live-queue rule; get_live_dispatch_queue()'s own urgency-first
# sort is not used for this page's default ordering.
live_window = sorted(
    (e for e in filtered if e.age_seconds <= shared_desk.NEW_JOBS_LIVE_WINDOW_SECONDS),
    key=lambda e: e.age_seconds,
)
older_unresolved = sorted(
    (e for e in filtered if e.age_seconds > shared_desk.NEW_JOBS_LIVE_WINDOW_SECONDS),
    key=lambda e: e.age_seconds,
)

# ------------------------------------------------------------ Selection --
# Never auto-select an older stale job on a fresh pick -- only ever from
# the live window. But a job already selected (even one that aged past
# the 60-minute cutoff mid-sequence) stays selected -- selection is
# always resolved against the full filtered set, never the live-window
# subset, so a job never disappears out from under the dispatcher
# mid-acknowledgment/dispatch.
all_identities = [entry_identity(e) for e in filtered]
window_identities = [entry_identity(e) for e in live_window]

selected = None
selected_id = None
if filtered:
    selected_id = st.session_state.get("desk_dispatch_selected")
    if selected_id not in all_identities:
        selected_id = window_identities[0] if window_identities else None
    if selected_id is not None:
        st.session_state.desk_dispatch_selected = selected_id
        selected = next(e for e in filtered if entry_identity(e) == selected_id)

# --------------------------------------------------------------- Layout --
if selected is not None:
    shared_desk.render_job_workspace(conn, selected)
elif not live_window:
    st.info("No new live jobs need attention.")

if live_window:
    st.markdown(f'<div class="desk-section-title">Live queue ({len(live_window)})</div>', unsafe_allow_html=True)
    for e in live_window:
        shared_desk.render_queue_row(e, entry_identity(e), selected_id)

if older_unresolved:
    with st.expander(f"Older unresolved jobs ({len(older_unresolved)})"):
        for e in older_unresolved:
            shared_desk.render_queue_row(e, entry_identity(e), selected_id)

with st.expander("Filters"):
    filter_counts = {name: sum(1 for e in live_entries if entry_filter_match(e, name)) for name in FILTER_OPTIONS}
    st.pills(
        "Filter", FILTER_OPTIONS, selection_mode="single", default="All",
        key="desk_dispatch_filter",
        format_func=lambda n: f"{n} ({filter_counts[n]})",
    )

with st.expander(f"Other Messages ({len(other_messages)})"):
    for item in other_messages:
        st.caption(f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — {shared.mask_sensitive(item['raw_text'])[:80]}")

with st.expander(f"Previous Backlog ({len(backlog_pending)})"):
    for item in backlog_pending:
        st.caption(f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — {item['group_label']} · {shared.mask_sensitive(item['raw_text'])[:80]}")

conn.close()
