"""Closings & Payments -- makes unpaid/unsettled jobs immediately visible.

reconciliation_state is stored separately from jobs.status (see
storage._add_closing_and_reconciliation_support's docstring) -- "Closed"
here always means the operational lifecycle status; "Awaiting
Reconciliation" / "Settled" are a second, independent lens on the same
Closed jobs. "Open Jobs" and "Awaiting Closing" currently show the same
underlying set (every active job is, by definition, awaiting a closing) --
an honest reflection of what the current data model actually
distinguishes, not a claim of a finer-grained status that doesn't exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
from dispatcher import storage

if not st.session_state.get(shared.AUTH_SESSION_KEY):
    st.stop()

st.subheader("Closings & Payments")

conn = shared.get_readonly_connection()
jobs = shared.fetch_jobs(conn)

closed_jobs = [j for j in jobs if j["status"] == "Closed"]
unsettled = []
for j in closed_jobs:
    details = storage.get_latest_closing_details(conn, j["id"])
    reconciliation_row = conn.execute(
        "SELECT reconciliation_state FROM jobs WHERE id = ?", (j["id"],)
    ).fetchone()
    j["_closing_details"] = details
    j["_reconciliation_state"] = reconciliation_row[0] if reconciliation_row else None
    if j["_reconciliation_state"] != "Settled":
        unsettled.append(j)

if unsettled:
    st.warning(f"{len(unsettled)} closed job(s) awaiting reconciliation.")
else:
    st.success("No unsettled closed jobs.")

VIEWS = ("Open Jobs", "Awaiting Closing", "Closed", "Awaiting Reconciliation", "Settled")
view = st.segmented_control(
    "View", options=VIEWS, default="Awaiting Reconciliation", required=True,
    key="luxor_ops_closings_view", label_visibility="collapsed",
)

if view in ("Open Jobs", "Awaiting Closing"):
    view_jobs = [j for j in jobs if j["status"] in shared.ACTIVE_STATUSES]
elif view == "Closed":
    view_jobs = closed_jobs
elif view == "Awaiting Reconciliation":
    view_jobs = [j for j in closed_jobs if j["_reconciliation_state"] != "Settled"]
else:  # Settled
    view_jobs = [j for j in closed_jobs if j["_reconciliation_state"] == "Settled"]

st.caption(f"{len(view_jobs)} job(s)")
if not view_jobs:
    st.info("No jobs in this view.")

for job in view_jobs:
    with st.container(border=True):
        st.markdown(f"**{shared.status_badge(job['status'])}** · {job['ticket_number']} · {job['source']}")
        st.write(f"{job['customer_name'] or 'Customer unknown'} · {job['service_area']}")
        details = job.get("_closing_details") or (
            storage.get_latest_closing_details(conn, job["id"]) if job["status"] == "Closed" else None
        )
        if details:
            st.write(f"Amount: {details['amount']} · Payment: {details['payment_method'] or 'not recorded'}")
            if details["parts_amount"]:
                st.caption(f"Parts: {details['parts_amount']}")
            if details["company_share"] or details["technician_share"]:
                st.caption(f"Company share: {details['company_share'] or 'n/a'} · Technician share: {details['technician_share'] or 'n/a'}")
        elif job["status"] == "Closed":
            st.caption("No structured closing amount recorded for this job.")
        reconciliation_state = job.get("_reconciliation_state")
        if job["status"] == "Closed":
            st.write(f"Reconciliation: {'Settled' if reconciliation_state == 'Settled' else 'Awaiting reconciliation'}")
            if reconciliation_state != "Settled":
                if st.button("Mark Settled", key=f"luxor_ops_settle_{job['id']}"):
                    write_conn = storage.connect()
                    storage.mark_job_settled(write_conn, job["id"])
                    write_conn.close()
                    st.rerun()

conn.close()
