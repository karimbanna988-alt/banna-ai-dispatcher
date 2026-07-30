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

# ------------------------------------------------------------ Sync -------
if "desk_refresh_message" not in st.session_state:
    st.session_state.desk_refresh_message = None

sync_clicked = shared_desk.render_header(conn, "New Jobs", show_sync=True)
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

shared_desk.render_workflow_strip(conn, "New")

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

# ------------------------------------------------- Needs action / Waiting -
# "Waiting" = the dispatcher already tapped something for this job (K sent
# and/or WhatsApp opened) but hasn't confirmed dispatch yet -- come back
# and finish it. "Needs action" = everything untouched, or blocked by
# missing/conflicting data. See docs/operator_ui_blueprint.md's mobile
# addendum for why New Jobs has no third, "nothing to do" bucket.
VIEW_OPTIONS = ("Needs action", "Waiting", "All")


def entry_bucket(e: rm.QueueEntry) -> str:
    needs_review = bool(e.result.decision.missing_information) or bool(e.classify_result.conflicting_fields)
    started = bool(e.tracking["acknowledged_at"]) or bool(e.tracking["whatsapp_opened_at"])
    if needs_review or not started:
        return "needs_action"
    return "waiting"


def view_match(e: rm.QueueEntry, name: str) -> bool:
    if name == "All":
        return True
    return entry_bucket(e) == ("needs_action" if name == "Needs action" else "waiting")


view_counts = {name: sum(1 for e in filtered if view_match(e, name)) for name in VIEW_OPTIONS}
active_view = st.pills(
    "View", VIEW_OPTIONS, selection_mode="single", default="Needs action",
    key="desk_dispatch_view", label_visibility="collapsed",
    format_func=lambda n: f"{n} ({view_counts[n]})",
) or "Needs action"
# The company-filtered set stays the *selectable* universe -- a job the
# dispatcher is mid-flow on (e.g. just sent K) can move from "Needs
# action" to "Waiting" the moment they act on it; it must not vanish out
# from under them just because the view narrowed. view_filtered only
# drives what the compact queue list and "Next job" show.
company_filtered = filtered
view_filtered = [e for e in company_filtered if view_match(e, active_view)]


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


company_identities = [entry_identity(e) for e in company_filtered]
view_identities = [entry_identity(e) for e in view_filtered]

if not company_filtered:
    st.success("No jobs need your attention right now.")
    selected = None
    selected_id = None
else:
    selected_id = st.session_state.get("desk_dispatch_selected")
    if selected_id not in company_identities:
        fallback_pool = view_identities or company_identities
        selected_id = fallback_pool[0]
    st.session_state.desk_dispatch_selected = selected_id
    selected = next(e for e in company_filtered if entry_identity(e) == selected_id)
    shared_desk.render_next_job_button(view_identities or company_identities, selected_id, "desk_dispatch_selected", "desk_dispatch_next")

with st.container(key="desk_layout"):
    with st.container(key="desk_area_workspace"):
        if selected is not None:
            shared_desk.render_job_workspace(conn, selected)

    with st.container(key="desk_area_queue"):
        if view_filtered:
            st.markdown(f'<div class="desk-section-title">Queue ({len(view_filtered)})</div>', unsafe_allow_html=True)
            if active_filter == "All":
                groups: dict = {}
                order = []
                for e in view_filtered:
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
                for e in view_filtered:
                    shared_desk.render_queue_row(e, entry_identity(e), selected_id)
        elif company_filtered:
            st.caption("No jobs in this view.")

with st.expander(f"Other Messages ({len(other_messages)})"):
    for item in other_messages:
        st.caption(f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — {shared.mask_sensitive(item['raw_text'])[:80]}")

with st.expander(f"Previous Backlog ({len(backlog_pending)})"):
    st.caption("Historical messages received before the live queue boundary. Preserved for audit; never urgent.")
    for item in backlog_pending:
        st.caption(f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — {item['group_label']} · {shared.mask_sensitive(item['raw_text'])[:80]}")

conn.close()
