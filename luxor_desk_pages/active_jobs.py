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
from dispatcher import storage

if not shared_desk.is_authenticated():
    st.stop()

conn = storage.connect()
active = rm.get_active_jobs_detailed(conn)

st.caption(f"{len(active)} active job(s)")

selected_id = None
if not active:
    st.info("No active jobs right now.")
else:
    ids = [j["id"] for j in active]
    selected_id = st.session_state.get("desk_active_selected")
    if selected_id not in ids:
        selected_id = ids[0]
    st.session_state.desk_active_selected = selected_id

with st.container(key="desk_layout"):
    with st.container(key="desk_area_workspace"):
        if selected_id is not None:
            shared_desk.render_active_job_workspace(conn, selected_id)

    with st.container(key="desk_area_queue"):
        if active:
            st.markdown(f'<div class="desk-section-title">Active Jobs ({len(active)})</div>', unsafe_allow_html=True)
            for job in active:
                subtitle = f"{job['assigned_technician_name'] or 'Unassigned'} — {shared_desk.active_job_next_action_label(job)}"
                shared_desk.render_job_row(job, job["id"], selected_id, "active", subtitle)

conn.close()
