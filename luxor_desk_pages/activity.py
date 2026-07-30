"""Activity -- real append-only audit events. shared.fetch_all_events()
via luxor_desk_read_model, unchanged. Reuses
luxor_desk_shared.history_event_label() -- the same business-facing
labels the History page uses -- for consistent wording across both
audit surfaces. A secondary, cross-job feed: normal dispatch history
belongs on History; this page stays available as the generic audit/
developer view."""

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
shared_desk.render_header(conn, "Activity")
events = rm.get_recent_activity(conn, limit=50)

st.caption("A secondary audit feed across every job -- for normal dispatch history, use the History page instead.")

if not events:
    st.info("No activity recorded yet.")
for event in events:
    st.markdown(
        f'<div class="desk-meta">{shared_desk.history_event_label(event)} — '
        f'{event.get("source") or "Unknown"} · {event.get("ticket_number") or "No reference"} — '
        f'{shared.humanize_timestamp(event["created_at"])}</div>',
        unsafe_allow_html=True,
    )

conn.close()
