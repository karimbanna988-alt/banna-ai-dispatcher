"""Job Workspace -- the central preview screen. Opened from Company Inbox
via a selected pending message. Renders Original -> Extracted -> Missing/
Conflicting -> Assessment -> Luxor message -> History, but ONLY when the
job-transformation gate passes; otherwise explains why, and never calls
the Luxor formatter."""

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

st.subheader("Job Workspace")

selected_id = st.session_state.get("luxor_ops_selected_pending_message_id")
if not selected_id:
    st.info("Select a message from Company Inbox to preview it here.")
    st.stop()

conn = shared.get_readonly_connection()

item = shared.fetch_pending_message_by_id(conn, selected_id)
if item is None:
    st.warning("This message is no longer available. Return to Company Inbox and select another.")
    conn.close()
    st.stop()

job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
shows = shared.shows_job_transformation(classify_result, job)

stream = next((s for s in shared.STREAMS if s.group_label == item["group_label"]), None)
stream_name = stream.display_name if stream else item["group_label"]

review_state = shared.pending_review_state(job, classify_result)
header_cols = st.columns([1.4, 1, 1, 1.4, 1.2, 1.2])
header_cols[0].write(f"**{job.ticket_number.value or 'No reference'}**")
header_cols[1].write(stream_name)
header_cols[2].write("Pending")
area = ", ".join(p for p in (job.city.value, job.state.value) if p) or "Area unknown"
header_cols[3].write(area)
header_cols[4].write(shared.humanize_timestamp(item["received_at"] or item["fetched_at"]))
header_cols[5].write(review_state)

if not shows:
    st.warning(shared.GATE_FAILURE_MESSAGE)
    st.caption(f"Reason: {classify_result.reason}")
    with st.expander("Original message"):
        st.code(shared.mask_sensitive(item["raw_text"]) or "")
    conn.close()
    st.stop()

# Gate passed -- only now does the full pipeline (including the Luxor
# formatter) get called.
result = shared.cached_full_preview(item["raw_text"], conn)

with st.container(border=True):
    st.markdown("**1. Original source message**")
    st.code(shared.mask_sensitive(item["raw_text"]) or "")

with st.container(border=True):
    st.markdown("**2. Extracted canonical job**")
    phone = job.phone.value
    if phone and job.extension.value:
        phone = f"{phone} ext {job.extension.value}"
    rows = [
        ("Customer", shared.mask_customer_name(job.customer_name.value) or "Not provided"),
        ("Phone", shared.mask_sensitive(phone) or "Not provided"),
        ("Address", job.address.value or "Not provided"),
        ("Service requested", job.service_type.value or "Not provided"),
        ("Vehicle", job.vehicle.value or "Not applicable"),
        ("Ticket / reference", job.ticket_number.value or "Not provided"),
        ("Source company", job.source.value),
    ]
    for label, value in rows:
        st.write(f"**{label}:** {value}")

with st.container(border=True):
    st.markdown("**3. Missing or conflicting information**")
    missing = result.decision.missing_information
    conflicts = classify_result.conflicting_fields
    if not missing and not conflicts:
        st.write("None -- all required fields present.")
    else:
        if missing:
            st.write("Missing: " + ", ".join(missing))
        if conflicts:
            st.write("Conflicting: " + ", ".join(conflicts))

with st.container(border=True):
    st.markdown("**4. Deterministic assessment**")
    st.write(f"Classification: {shared.message_type_label(classify_result.message_type.value)}")
    st.write(f"Result: {shared.verdict_label(result.decision.verdict.value)}")
    if result.decision.verdict.value in ("NEEDS_REVIEW", "HANDLE_MANUALLY"):
        st.warning("Marked for manual review -- not auto-accepted.")
    if result.decision.reasons:
        st.caption("Reasons: " + "; ".join(result.decision.reasons))
    if result.decision.restrictions_triggered:
        st.caption("Restrictions: " + "; ".join(result.decision.restrictions_triggered))
    if result.decision.service_area:
        st.caption(f"Service area: {result.decision.service_area}")

with st.container(border=True):
    st.markdown("**5. Luxor technician-ready message**")
    st.caption("Prepared preview — not sent · use the copy icon on the message below")
    st.code(result.luxor_message)

st.markdown("**6. Activity history**")
if classify_result.existing_job_id is not None:
    timeline = shared.fetch_timeline(conn, classify_result.existing_job_id)
    if not timeline:
        st.caption("No prior history recorded for this job.")
    for event in timeline:
        with st.container(border=True):
            st.write(shared.humanize_event_label(event))
            st.caption(shared.humanize_timestamp(event["created_at"]))
else:
    st.caption("No prior history -- this will be the first record for this job once reviewed.")

conn.close()
