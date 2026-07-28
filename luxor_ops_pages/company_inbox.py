"""Company Inbox -- pick one operational stream, see its messages grouped
by what they actually are, as compact aligned rows. One-letter
acknowledgments and other content-free replies collapse into "Other
Messages" and never reach the Luxor formatter (see
luxor_ops_shared.shows_job_transformation)."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
from dispatcher.storage import MessageType

if not st.session_state.get(shared.AUTH_SESSION_KEY):
    st.stop()

conn = shared.get_readonly_connection()

stream_names = [s.display_name for s in shared.STREAMS]
selected_name = st.segmented_control(
    "Operational stream", options=stream_names, default=stream_names[0], required=True,
    key="luxor_ops_selected_stream", label_visibility="collapsed",
)
stream = next(s for s in shared.STREAMS if s.display_name == selected_name)
st.subheader(stream.display_name)

pending = shared.fetch_pending_messages(conn, group_label=stream.group_label)

_BUCKET_FOR_TYPE = {
    MessageType.NEW.value: "New Jobs",
    MessageType.CORRECTION.value: "Corrections",
    MessageType.CANCELLATION.value: "Cancellations",
    MessageType.CLOSING.value: "Closings",
    MessageType.CLOSING_WITHOUT_DISPATCH.value: "Closings",
    MessageType.DUPLICATE.value: "Updates",
    MessageType.UPDATE_REQUEST.value: "Updates",
    MessageType.STATUS.value: "Updates",
    MessageType.UNRECOGNIZED.value: "Updates",
    MessageType.ASSIGNMENT.value: "Updates",
}
BUCKET_ORDER = ["New Jobs", "Updates", "Corrections", "Cancellations", "Closings"]

buckets: dict[str, list] = {name: [] for name in BUCKET_ORDER}
other_messages = []

for item in pending:
    job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
    shows = shared.shows_job_transformation(classify_result, job)
    if not shows:
        other_messages.append(item)
        continue
    bucket_name = _BUCKET_FOR_TYPE.get(classify_result.message_type.value, "Updates")
    buckets[bucket_name].append((item, job, classify_result))

ROW_RATIOS = [1.3, 1.3, 1.6, 1.6, 1.3, 1.2, 1]


def render_header() -> None:
    cols = st.columns(ROW_RATIOS)
    for col, label in zip(cols, ["Ticket", "Area", "Service", "Type", "Received", "Review state", ""]):
        col.caption(f"**{label}**")


def render_message_row(item: dict, job, classify_result) -> None:
    area = ", ".join(p for p in (job.city.value, job.state.value) if p) or "Area unknown"
    cols = st.columns(ROW_RATIOS)
    cols[0].write(job.ticket_number.value or "No reference")
    cols[1].write(area)
    cols[2].write(job.service_type.value or "Not specified")
    cols[3].write(shared.message_type_label(classify_result.message_type.value))
    cols[4].write(shared.humanize_timestamp(item["received_at"] or item["fetched_at"]))
    cols[5].write(shared.pending_review_state(job, classify_result))
    if cols[6].button("Open", key=f"luxor_ops_open_{item['message_id']}"):
        st.session_state["luxor_ops_selected_pending_message_id"] = item["message_id"]
        st.switch_page("luxor_ops_pages/job_workspace.py")


for bucket_name in BUCKET_ORDER:
    entries = buckets[bucket_name]
    st.markdown(f"**{bucket_name}** ({len(entries)})")
    if not entries:
        st.caption("None right now.")
        continue
    render_header()
    for item, job, classify_result in entries:
        render_message_row(item, job, classify_result)

with st.expander(f"Other Messages ({len(other_messages)})"):
    if not other_messages:
        st.caption("None right now.")
    for item in other_messages:
        cols = st.columns([2, 5])
        cols[0].caption(shared.humanize_timestamp(item["received_at"] or item["fetched_at"]))
        cols[1].caption(shared.mask_sensitive(item["raw_text"])[:80])

conn.close()
