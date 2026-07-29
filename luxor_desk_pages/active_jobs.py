"""Active Jobs -- real committed jobs currently active. Same
shared.fetch_jobs()/ACTIVE_STATUSES query luxor_console_pages/active_jobs.py
already uses, via luxor_desk_read_model.get_active_jobs(). Clicking a job
opens the same Dispatch workspace pattern is meant for -- since active
jobs are already committed (no longer pending messages), this page shows
their latest status/technician/timing directly rather than re-opening
the message-workspace UI, matching Phase 7's own field list."""

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
active = rm.get_active_jobs(conn)

st.caption(f"{len(active)} active job(s)")
if not active:
    st.info("No active jobs right now.")

for job in active:
    st.markdown(
        f'<div class="desk-card-top">'
        f'{shared_desk.source_chip_html(job["source"])}'
        f'<span class="desk-ticket">{job["ticket_number"]}</span>'
        f'<span class="desk-age">{shared.humanize_timestamp(job["last_event_at"] or job["updated_at"])}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="desk-meta">{job["service_area"]} · {job["description"]}</div>'
        f'<div class="desk-meta">Technician: <strong>{job["assigned_technician_name"] or "Unassigned"}</strong> · '
        f'Status: <strong>{job["status"]}</strong></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="desk-divider"></div>', unsafe_allow_html=True)

conn.close()
