"""Active Jobs -- the real dispatcher workspace for already-dispatched
jobs: record a technician's real WhatsApp reply, prepare/copy/mark-sent
the company-facing update, pass/cancel with a reason, or move on to
Closing once the technician reports Completed. Reuses
luxor_desk_read_model/luxor_desk_shared exactly like New Jobs -- every
write goes through the purpose-built dispatcher.storage.* operational
methods (job_id + validated input), never a second database, parser, or
state machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import luxor_desk_read_model as rm
import luxor_desk_shared as shared_desk
import luxor_ops_shared as shared
from dispatcher import storage

if not shared_desk.is_authenticated():
    st.stop()

conn = storage.connect()
shared_desk.render_header(conn, "Active Jobs")
if not shared_desk.require_current_schema(conn):
    conn.close()
    st.stop()
shared_desk.render_workflow_strip(conn, "Active")
active = rm.get_active_jobs_detailed(conn)

# ---------------------------------------------------- Needs action / Waiting
# See shared_desk.active_job_bucket()'s own docstring: derived only from
# active_job_next_action_label(), the same label every Active Jobs row
# already shows -- no new classification invented. `active` (all active
# jobs) stays the *selectable* universe -- a job the dispatcher is
# mid-flow on can move from "Needs action" to "Waiting" the moment they
# act on it (e.g. saving a technician update); it must not vanish out
# from under them just because the view narrowed. `view_filtered` only
# drives what the compact queue list and "Next job" show.
VIEW_OPTIONS = ("Needs action", "Waiting", "All")
view_counts = {
    name: sum(1 for j in active if name == "All" or shared_desk.active_job_bucket(j) == ("needs_action" if name == "Needs action" else "waiting"))
    for name in VIEW_OPTIONS
}
active_view = st.pills(
    "View", VIEW_OPTIONS, selection_mode="single", default="Needs action",
    key="desk_active_view", label_visibility="collapsed",
    format_func=lambda n: f"{n} ({view_counts[n]})",
) or "Needs action"
if active_view == "All":
    view_filtered = list(active)
else:
    bucket = "needs_action" if active_view == "Needs action" else "waiting"
    view_filtered = [j for j in active if shared_desk.active_job_bucket(j) == bucket]

# Oldest-actionable-first within the view -- no urgency concept exists
# for active jobs today, so age is the only honest ordering signal.
view_filtered.sort(key=lambda j: j.get("last_event_at") or j.get("updated_at") or "")

st.caption(f"{len(view_filtered)} of {len(active)} active job(s) in this view")

all_ids = [j["id"] for j in active]
view_ids = [j["id"] for j in view_filtered]
selected_id = None
if not active:
    st.info("No active jobs.")
else:
    selected_id = st.session_state.get("desk_active_selected")
    if selected_id not in all_ids:
        fallback_pool = view_ids or all_ids
        selected_id = fallback_pool[0]
    st.session_state.desk_active_selected = selected_id
    shared_desk.render_next_job_button(view_ids or all_ids, selected_id, "desk_active_selected", "desk_active_next")

with st.container(key="desk_layout"):
    with st.container(key="desk_area_workspace"):
        if selected_id is not None:
            shared_desk.render_active_job_workspace(conn, selected_id)

    with st.container(key="desk_area_queue"):
        if view_filtered:
            st.markdown(f'<div class="desk-section-title">Active Jobs ({len(view_filtered)})</div>', unsafe_allow_html=True)
            for job in view_filtered:
                waiting_bit = f" — waiting {shared.humanize_timestamp(job.get('last_event_at') or job.get('updated_at'))}" if shared_desk.active_job_bucket(job) == "waiting" else ""
                subtitle = f"{job['assigned_technician_name'] or 'Unassigned'} — {shared_desk.active_job_next_action_label(job)}{waiting_bit}"
                shared_desk.render_job_row(job, job["id"], selected_id, "active", subtitle)
        elif active:
            st.caption("No jobs in this view.")

conn.close()
