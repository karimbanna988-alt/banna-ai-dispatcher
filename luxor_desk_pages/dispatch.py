"""Dispatch -- the real, live-database-backed dispatch queue. Every row
here is a genuine quo_pending_messages record; nothing is fabricated.
Reuses luxor_desk_read_model for all data access and dispatcher.storage/
outbound for every write action -- the exact same audit/tracking paths
luxor_console_pages/dispatch_queue.py already uses. Quo outbound stays
disabled; WhatsApp stays manual copy/open only.
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
if not shared_desk.require_current_schema(conn):
    conn.close()
    st.stop()

# ------------------------------------------------------------ Sync -------
if "desk_refresh_message" not in st.session_state:
    st.session_state.desk_refresh_message = None

top_l, top_r = st.columns([3, 1])
with top_r:
    if st.button("Sync", key="desk_dispatch_sync", width="stretch"):
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
with top_l:
    _connected = bool(os.environ.get(API_KEY_ENV_VAR))
    _state, _ = shared.connection_state(conn, _connected)
    _live_label = {"connected": "🟢 Live"}.get(_state, "🟡 Delayed" if _state == "delayed" else "🔴 Disconnected")
    _last_poll = shared.fetch_last_refresh(conn)
    st.caption(f"{_live_label} · last synced {shared.humanize_timestamp(_last_poll)}")

if st.session_state.desk_refresh_message:
    _kind, _msg = st.session_state.desk_refresh_message
    getattr(st, _kind)(_msg)
    st.session_state.desk_refresh_message = None

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


filter_counts = {name: sum(1 for e in live_entries if entry_filter_match(e, name)) for name in FILTER_OPTIONS}
active_filter = st.pills(
    "Filter", FILTER_OPTIONS, selection_mode="single", default="All",
    key="desk_dispatch_filter", label_visibility="collapsed",
    format_func=lambda n: f"{n} ({filter_counts[n]})",
) or "All"

filtered = [e for e in live_entries if entry_filter_match(e, active_filter)]
st.caption(f"Previous backlog: {len(backlog_pending)} (not urgent, not counted here)")


def entry_identity(e: rm.QueueEntry) -> str:
    """The canonical representative's own message_id -- always unique
    per canonical entry (it's quo_pending_messages' primary key, and
    canonicalize() picks exactly one representative row per group), so
    this stays correct now that a (source, ticket) pair can legitimately
    canonicalize into several distinct lifecycle-event entries (original
    new job, a correction, a cancellation, ...) rather than always one."""
    return e.item["message_id"]


GROUP_LABELS = {
    "AGN": "AGN", "Lucky": "Lucky", "Main Leads": "Main Leads",
}


def group_for(e: rm.QueueEntry) -> str:
    if e.item["group_label"] == "CLOSINGS":
        return "Lucky — Closings & Replies"
    return GROUP_LABELS.get(e.job.source.value, e.job.source.value)


if not filtered:
    st.success("No jobs need your attention right now.")
    selected = None
else:
    working_identities = [entry_identity(e) for e in filtered]
    selected_id = st.session_state.get("desk_dispatch_selected")
    if selected_id not in working_identities:
        selected_id = working_identities[0]
    st.session_state.desk_dispatch_selected = selected_id
    selected = next(e for e in filtered if entry_identity(e) == selected_id)

with st.container(key="desk_layout"):
    with st.container(key="desk_area_workspace"):
        if selected is not None:
            shared_desk.render_job_workspace(conn, selected)

    with st.container(key="desk_area_queue"):
        if filtered:
            st.markdown(f'<div class="desk-section-title">Queue ({len(filtered)})</div>', unsafe_allow_html=True)
            if active_filter == "All":
                groups: dict = {}
                order = []
                for e in filtered:
                    g = group_for(e)
                    if g not in groups:
                        groups[g] = []
                        order.append(g)
                    groups[g].append(e)
                for g in order:
                    st.markdown(f'<div class="desk-meta" style="margin-top:0.4rem;"><strong>{g}</strong></div>', unsafe_allow_html=True)
                    for e in groups[g]:
                        shared_desk.render_queue_row(e, entry_identity(e), selected_id)
            else:
                for e in filtered:
                    shared_desk.render_queue_row(e, entry_identity(e), selected_id)

with st.expander(f"Other Messages ({len(other_messages)})"):
    for item in other_messages:
        st.caption(f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — {shared.mask_sensitive(item['raw_text'])[:80]}")

with st.expander(f"Previous Backlog ({len(backlog_pending)})"):
    st.caption("Historical messages received before the live queue boundary. Preserved for audit; never urgent.")
    for item in backlog_pending:
        st.caption(f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — {item['group_label']} · {shared.mask_sensitive(item['raw_text'])[:80]}")

conn.close()
