"""History -- real completed jobs (Closed + reconciled "Settled" via
COMPLETE JOB, or Cancelled/Rejected) with a full chronological audit
trail per job. Normal dispatch history belongs here, not on the generic
Activity feed (kept available as a secondary audit/developer view). No
parser internals shown by default -- only workflow-level summaries and,
inside "Job details", the existing masked audit timeline.

Search/filter controls operate entirely on the already-fetched
`completed` list (client-side) -- no new query surface, no new database
access pattern.
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
shared_desk.render_header(conn, "History")
shared_desk.render_workflow_strip(conn, "History")
completed = rm.get_completed_jobs(conn)

if not completed:
    st.info("No completed jobs yet.")
    conn.close()
    st.stop()

sources = sorted({j["source"] for j in completed if j.get("source")})
technicians = sorted({j["assigned_technician_name"] for j in completed if j.get("assigned_technician_name")})
statuses = sorted({j["status"] for j in completed if j.get("status")})

filter_cols = st.columns([2, 1, 1, 1])
with filter_cols[0]:
    search_text = st.text_input("Search by ticket / PDL", key="desk_history_search")
with filter_cols[1]:
    source_filter = st.selectbox("Source", ["All"] + sources, key="desk_history_source")
with filter_cols[2]:
    technician_filter = st.selectbox("Technician", ["All"] + technicians, key="desk_history_technician")
with filter_cols[3]:
    status_filter = st.selectbox("Status", ["All"] + statuses, key="desk_history_status")

date_cols = st.columns(2)
with date_cols[0]:
    date_from = st.date_input("From date", value=None, key="desk_history_date_from")
with date_cols[1]:
    date_to = st.date_input("To date", value=None, key="desk_history_date_to")


def _matches(job: dict) -> bool:
    if search_text and search_text.strip().lower() not in (job.get("ticket_number") or "").lower():
        return False
    if source_filter != "All" and job.get("source") != source_filter:
        return False
    if technician_filter != "All" and job.get("assigned_technician_name") != technician_filter:
        return False
    if status_filter != "All" and job.get("status") != status_filter:
        return False
    job_date_raw = job.get("last_event_at") or job.get("updated_at") or job.get("created_at")
    if job_date_raw and (date_from or date_to):
        job_date = str(job_date_raw)[:10]
        if date_from and job_date < date_from.isoformat():
            return False
        if date_to and job_date > date_to.isoformat():
            return False
    return True


filtered = [j for j in completed if _matches(j)]

st.caption(f"{len(filtered)} of {len(completed)} completed job(s)")
if not filtered:
    st.info("No jobs match these filters.")

for job in filtered:
    shared_desk.render_completed_job_card(conn, job)

conn.close()
