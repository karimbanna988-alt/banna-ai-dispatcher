"""Activity -- real append-only audit events. shared.fetch_all_events()
via luxor_desk_read_model, unchanged. Business-facing labels via the
existing humanize_event_label(), not raw message_type/final_action
strings."""

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
events = rm.get_recent_activity(conn, limit=50)

if not events:
    st.info("No activity recorded yet.")
for event in events:
    st.markdown(
        f'<div class="desk-meta">{shared.humanize_event_label(event)} — '
        f'{event.get("source") or "Unknown"} · {event.get("ticket_number") or "No reference"} — '
        f'{shared.humanize_timestamp(event["created_at"])}</div>',
        unsafe_allow_html=True,
    )

conn.close()
