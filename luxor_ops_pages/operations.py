"""Operations -- landing page. Answers within one screen: what arrived,
which stream sent it, what needs attention, what's active, and whether
sync is current. Compact aligned rows (st.columns), not oversized cards --
these render as an aligned row on desktop and stack automatically into a
compact mobile list below ~640px (Streamlit's own column behavior), so
desktop and mobile intentionally differ without custom dual-markup."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
from dispatcher import quo_client, storage
from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError
from dispatcher.quo_ingest import poll_for_new_messages

if not st.session_state.get(shared.AUTH_SESSION_KEY):
    st.stop()

conn = shared.get_readonly_connection()

jobs = shared.fetch_jobs(conn)
active_count = sum(1 for j in jobs if j["status"] in shared.ACTIVE_STATUSES)
completed_count = sum(1 for j in jobs if j["status"] == "Closed")
cancelled_count = sum(1 for j in jobs if j["status"] == "Cancelled")

last_refresh = shared.fetch_last_refresh(conn)

# ------------------------------------------------------------- Streams ----
stream_rows = []
attention_entries = []
newest_valid_entries = []

for stream in shared.STREAMS:
    pending = shared.fetch_pending_messages(conn, group_label=stream.group_label)
    new_job_count = 0
    attention_count = 0
    for item in pending:
        job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
        shows = shared.shows_job_transformation(classify_result, job)
        if shows and classify_result.message_type.value == "NEW":
            new_job_count += 1
            newest_valid_entries.append((item, job, stream))
        if classify_result.message_type.value in shared.ATTENTION_MESSAGE_TYPES:
            attention_count += 1
            attention_entries.append((item, classify_result, stream))

    if stream.source:
        active_crm_jobs = sum(1 for j in jobs if j["source"] == stream.source and j["status"] in shared.ACTIVE_STATUSES)
        stream_attention_jobs = sum(1 for j in jobs if j["source"] == stream.source and j["status"] in shared.MANUAL_REVIEW_STATUSES)
    else:
        active_crm_jobs = None
        stream_attention_jobs = 0

    latest = pending[0]["received_at"] or pending[0]["fetched_at"] if pending else None

    stream_rows.append({
        "stream": stream,
        "new_jobs": new_job_count,
        "awaiting_review": len(pending),
        "active_crm_jobs": active_crm_jobs,
        "needs_attention": attention_count + stream_attention_jobs,
        "latest": shared.humanize_timestamp(latest) if latest else "No recent activity",
    })

st.subheader("Operational Streams")
header = st.columns([2, 1, 1, 1, 1, 1.5])
for col, label in zip(header, ["Stream", "New", "Review", "Active", "Attention", "Latest"]):
    col.caption(f"**{label}**")
for row in stream_rows:
    cols = st.columns([2, 1, 1, 1, 1, 1.5])
    cols[0].write(row["stream"].display_name)
    cols[1].write(str(row["new_jobs"]))
    cols[2].write(str(row["awaiting_review"]))
    cols[3].write(str(row["active_crm_jobs"]) if row["active_crm_jobs"] is not None else "—")
    cols[4].write(f"🟠 {row['needs_attention']}" if row["needs_attention"] else "0")
    cols[5].write(row["latest"])

st.divider()

# --------------------------------------------------------- Attention queue
st.subheader("Needs Attention")
attention_entries.sort(key=lambda e: e[0]["received_at"] or e[0]["fetched_at"] or "", reverse=True)
if not attention_entries:
    st.caption("Nothing needs attention right now.")
else:
    for item, classify_result, stream in attention_entries[:5]:
        cols = st.columns([2, 2, 2])
        cols[0].write(stream.display_name)
        cols[1].write(shared.message_type_label(classify_result.message_type.value))
        cols[2].write(shared.humanize_timestamp(item["received_at"] or item["fetched_at"]))

st.divider()

# ------------------------------------------------------ Newest valid jobs
st.subheader("Newest Job Messages")
newest_valid_entries.sort(key=lambda e: e[0]["received_at"] or e[0]["fetched_at"] or "", reverse=True)
if not newest_valid_entries:
    st.caption("No new job messages right now.")
else:
    for item, job, stream in newest_valid_entries[:3]:
        area = ", ".join(p for p in (job.city.value, job.state.value) if p) or "Area unknown"
        cols = st.columns([2, 2, 2, 2])
        cols[0].write(stream.display_name)
        cols[1].write(area)
        cols[2].write(job.service_type.value or "Not specified")
        cols[3].write(shared.humanize_timestamp(item["received_at"] or item["fetched_at"]))

st.divider()

# --------------------------------------------------------------- Summary
st.subheader("Active Job Summary")
with st.container(key="luxor_ops_metrics"):
    m1, m2, m3 = st.columns(3)
    m1.metric("Active", active_count)
    m2.metric("Completed", completed_count)
    m3.metric("Cancelled", cancelled_count)

st.divider()

# --------------------------------------------------------------- Refresh
if "luxor_ops_refresh_message" not in st.session_state:
    st.session_state.luxor_ops_refresh_message = None

if st.button("Refresh", type="primary", key="luxor_ops_refresh", width="stretch"):
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        st.session_state.luxor_ops_refresh_message = ("error", "Quo is not configured in this environment.")
    else:
        write_conn = None
        try:
            write_conn = storage.connect()
            client = quo_client.QuoClient(api_key=api_key)
            outcome = poll_for_new_messages(write_conn, client)
            message = f"Refreshed -- {len(outcome.queued)} new message(s) found."
            st.session_state.luxor_ops_refresh_message = ("success", message)
            if outcome.failed_count:
                st.session_state.luxor_ops_refresh_message = (
                    "error", f"{outcome.failed_count} message(s) could not be saved and will be retried.",
                )
        except QuoConfigError:
            st.session_state.luxor_ops_refresh_message = ("error", "Quo is not configured in this environment.")
        except QuoAPIError:
            st.session_state.luxor_ops_refresh_message = ("error", "Quo is temporarily unavailable. Please try again shortly.")
        finally:
            if write_conn is not None:
                write_conn.close()
        shared.clear_pending_caches()
    st.rerun()

if st.session_state.luxor_ops_refresh_message:
    _kind, _message = st.session_state.luxor_ops_refresh_message
    getattr(st, _kind)(_message)

st.caption(f"Last synchronized: {shared.humanize_timestamp(last_refresh) if last_refresh else 'Never'} · shared across all streams")

conn.close()
