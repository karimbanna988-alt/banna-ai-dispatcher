"""Closings -- the real dispatcher workspace: enter a technician's real
closing (from Quo, a WhatsApp paste, or an already-persisted record),
prepare/copy/mark-sent the company-specific closing message via the
existing closing.py formatter, and complete the job. Every write goes
through the purpose-built dispatcher.storage.* operational methods --
no second closing template, no second state machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import luxor_desk_read_model as rm
import luxor_desk_shared as shared_desk
from dispatcher import storage

if not shared_desk.is_authenticated():
    st.stop()

conn = storage.connect()
shared_desk.render_header(conn, "Closings")
if not shared_desk.require_current_schema(conn):
    conn.close()
    st.stop()
shared_desk.render_workflow_strip(conn, "Closing")
candidates = rm.get_closing_candidates(conn)

# ---------------------------------------------------- Needs action / Waiting
# See shared_desk.closing_job_bucket()'s own docstring -- maps directly to
# get_closing_candidates()'s two existing branches. `candidates` (all
# closing candidates) stays the *selectable* universe so a job the
# dispatcher just recorded a closing for doesn't vanish out from under
# them the moment its bucket changes; `view_filtered` only drives what
# the compact queue list and "Next job" show.
VIEW_OPTIONS = ("Needs action", "Waiting", "All")
view_counts = {
    name: sum(1 for j in candidates if name == "All" or shared_desk.closing_job_bucket(j) == ("needs_action" if name == "Needs action" else "waiting"))
    for name in VIEW_OPTIONS
}
closings_view = st.pills(
    "View", VIEW_OPTIONS, selection_mode="single", default="Needs action",
    key="desk_closings_view", label_visibility="collapsed",
    format_func=lambda n: f"{n} ({view_counts[n]})",
) or "Needs action"
if closings_view == "All":
    view_filtered = list(candidates)
else:
    bucket = "needs_action" if closings_view == "Needs action" else "waiting"
    view_filtered = [j for j in candidates if shared_desk.closing_job_bucket(j) == bucket]

st.caption(f"{len(view_filtered)} of {len(candidates)} job(s) in the closing workflow")

all_ids = [j["id"] for j in candidates]
view_ids = [j["id"] for j in view_filtered]
selected_id = None
if not candidates:
    st.success("No closings are waiting.")
else:
    selected_id = st.session_state.get("desk_closings_selected")
    if selected_id not in all_ids:
        fallback_pool = view_ids or all_ids
        selected_id = fallback_pool[0]
    st.session_state.desk_closings_selected = selected_id
    shared_desk.render_next_job_button(view_ids or all_ids, selected_id, "desk_closings_selected", "desk_closings_next")

with st.container(key="desk_layout"):
    with st.container(key="desk_area_workspace"):
        if selected_id is not None:
            shared_desk.render_closing_workspace(conn, selected_id)

    with st.container(key="desk_area_queue"):
        if view_filtered:
            st.markdown(f'<div class="desk-section-title">Closings ({len(view_filtered)})</div>', unsafe_allow_html=True)
            for job in view_filtered:
                subtitle = "Enter closing" if job["status"] != "Closed" else "Ready to send/complete"
                shared_desk.render_job_row(job, job["id"], selected_id, "closings", subtitle)
        elif candidates:
            st.caption("No jobs in this view.")

conn.close()
