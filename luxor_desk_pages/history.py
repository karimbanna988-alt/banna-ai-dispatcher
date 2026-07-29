"""Completed / History -- real completed jobs (Closed + reconciled
"Settled" via COMPLETE JOB, or Cancelled/Rejected) with a full
chronological audit trail per job. Normal dispatch history belongs here,
not on the generic Activity feed (kept available as a secondary
audit/developer view). No parser internals shown by default -- only
workflow-level summaries and, inside "View details", the existing
masked audit timeline.
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
completed = rm.get_completed_jobs(conn)

st.caption(f"{len(completed)} completed job(s)")
if not completed:
    st.info("No completed jobs yet.")

for job in completed:
    shared_desk.render_completed_job_card(conn, job)

conn.close()
