"""CRM Jobs -- canonical saved jobs only, never raw pending messages.
Compact aligned rows + search/filters/views; selecting "Details" opens
the full record (customer snapshot, current state, audit history) inline.
Customer fields are a job-specific snapshot only -- this is not a full
customer CRM (no cross-job customer linkage exists in the schema)."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared

if not st.session_state.get(shared.AUTH_SESSION_KEY):
    st.stop()

st.subheader("CRM Jobs")

conn = shared.get_readonly_connection()
jobs = shared.fetch_jobs(conn)

VIEW_STATUSES = {
    "Active": shared.ACTIVE_STATUSES | shared.MANUAL_REVIEW_STATUSES,
    "Completed": {"Closed"},
    "Cancelled": {"Cancelled"},
    "All": None,
}
view = st.segmented_control(
    "View", options=list(VIEW_STATUSES.keys()), default="Active", required=True,
    key="luxor_ops_crm_view", label_visibility="collapsed",
)

search = st.text_input("Search ticket or customer name", key="luxor_ops_crm_search")

sources = ["All"] + sorted({j["source"] for j in jobs})
statuses = ["All"] + sorted({j["status"] for j in jobs})
areas = ["All"] + sorted({j["service_area"] for j in jobs})

filter_col1, filter_col2, filter_col3 = st.columns(3)
source_filter = filter_col1.selectbox("Source", sources, key="luxor_ops_crm_source_filter")
status_filter = filter_col2.selectbox("Status", statuses, key="luxor_ops_crm_status_filter")
area_filter = filter_col3.selectbox("Area", areas, key="luxor_ops_crm_area_filter")

filtered = jobs
view_statuses = VIEW_STATUSES[view]
if view_statuses is not None:
    filtered = [j for j in filtered if j["status"] in view_statuses]
if search:
    needle = search.strip().lower()
    filtered = [
        j for j in filtered
        if needle in (j["ticket_number"] or "").lower() or needle in (j["customer_name"] or "").lower()
    ]
if source_filter != "All":
    filtered = [j for j in filtered if j["source"] == source_filter]
if status_filter != "All":
    filtered = [j for j in filtered if j["status"] == status_filter]
if area_filter != "All":
    filtered = [j for j in filtered if j["service_area"] == area_filter]

st.caption(f"{len(filtered)} job(s)")

if not filtered:
    st.info("No jobs match these filters.")

ROW_RATIOS = [1.2, 1, 1.4, 1.3, 1.4, 1.3, 1.3]
header = st.columns(ROW_RATIOS)
for col, label in zip(header, ["Ticket", "Status", "Customer", "Area", "Service", "Source", "Assigned"]):
    col.caption(f"**{label}**")

for job in filtered:
    cols = st.columns(ROW_RATIOS)
    cols[0].write(job["ticket_number"])
    cols[1].write(shared.status_badge(job["status"]))
    cols[2].write(job["customer_name"] or "Unknown")
    cols[3].write(job["service_area"])
    cols[4].write(job["description"])
    cols[5].write(job["source"])
    cols[6].write(job["assigned_technician_name"] or "Unassigned")
    with st.expander(f"Details — {job['ticket_number']}"):
        st.caption(f"Updated {shared.humanize_timestamp(job['last_event_at'] or job['updated_at'])}")
        st.markdown("**Customer snapshot**")
        st.write(f"Name: {job['customer_name'] or 'Not provided'}")
        st.write(f"Phone: {job['customer_phone'] or 'Not provided'}")
        st.write(f"Address: {job['customer_address'] or 'Not provided'}")
        st.markdown("**Audit history**")
        timeline = shared.fetch_timeline(conn, job["id"])
        if not timeline:
            st.caption("No history recorded.")
        for event in timeline:
            st.write(f"{shared.humanize_event_label(event)} — {shared.humanize_timestamp(event['created_at'])}")

conn.close()
