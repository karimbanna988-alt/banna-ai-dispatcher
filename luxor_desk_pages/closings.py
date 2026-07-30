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
candidates = rm.get_closing_candidates(conn)

st.caption(f"{len(candidates)} job(s) in the closing workflow")

selected_id = None
if not candidates:
    st.success("No closings are waiting.")
else:
    ids = [j["id"] for j in candidates]
    selected_id = st.session_state.get("desk_closings_selected")
    if selected_id not in ids:
        selected_id = ids[0]
    st.session_state.desk_closings_selected = selected_id

with st.container(key="desk_layout"):
    with st.container(key="desk_area_workspace"):
        if selected_id is not None:
            shared_desk.render_closing_workspace(conn, selected_id)

    with st.container(key="desk_area_queue"):
        if candidates:
            st.markdown(f'<div class="desk-section-title">Closings ({len(candidates)})</div>', unsafe_allow_html=True)
            for job in candidates:
                subtitle = "Enter closing" if job["status"] != "Closed" else "Ready to send/complete"
                shared_desk.render_job_row(job, job["id"], selected_id, "closings", subtitle)

conn.close()
