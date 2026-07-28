"""Activity -- global, read-only chronological feed: message arrivals and
committed audit events, newest first, human-readable labels only."""

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

st.subheader("Activity")

conn = shared.get_readonly_connection()

feed = []
for event in shared.fetch_all_events(conn):
    feed.append({
        "timestamp": event["created_at"],
        "label": shared.humanize_event_label(event),
        "detail": f"{event.get('source') or 'Unknown source'} · {event.get('ticket_number') or 'No reference'}",
    })
for item in shared.fetch_pending_messages(conn):
    ts = item["received_at"] or item["fetched_at"]
    stream = next((s for s in shared.STREAMS if s.group_label == item["group_label"]), None)
    job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
    review_state = shared.pending_review_state(job, classify_result)
    label = "Information missing" if review_state == "Information missing" else "Sent for review" if review_state != "Other message" else "Message received"
    feed.append({
        "timestamp": ts,
        "label": label,
        "detail": stream.display_name if stream else item["group_label"],
    })

feed.sort(key=lambda e: e["timestamp"] or "", reverse=True)
feed = feed[:60]

if not feed:
    st.info("No activity recorded yet.")

last_bucket = None
for item in feed:
    bucket = shared.date_bucket_label(item["timestamp"])
    if bucket != last_bucket:
        st.markdown(f"**{bucket}**")
        last_bucket = bucket
    with st.container(border=True):
        st.write(f"{item['label']} — {item['detail']}")
        st.caption(shared.humanize_timestamp(item["timestamp"]))

conn.close()
