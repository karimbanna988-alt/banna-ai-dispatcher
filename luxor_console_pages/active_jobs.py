"""Active Jobs -- compact reference list of committed, in-progress jobs.
Read-only. Full history/search stays in the Management CRM; this is a
quick-glance reference only, not the primary workflow."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared

if not st.session_state.get("luxor_console_authenticated"):
    st.stop()

conn = shared.get_readonly_connection()
jobs = shared.fetch_jobs(conn)
active = [j for j in jobs if j["status"] in shared.ACTIVE_STATUSES]

st.caption(f"{len(active)} active job(s)")
if not active:
    st.info("No active jobs right now.")
for job in active:
    with st.container(border=True):
        st.write(f"**{shared.status_badge(job['status'])}** · {job['ticket_number']} · {job['source']}")
        st.caption(f"{job['service_area']} · {job['description']}")
        st.caption(f"{job['assigned_technician_name'] or 'Unassigned'} · Updated {shared.humanize_timestamp(job['last_event_at'] or job['updated_at'])}")

conn.close()
