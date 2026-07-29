"""Activity -- compact, read-only recent-events reference."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared

if not st.session_state.get("luxor_console_authenticated"):
    st.stop()

conn = shared.get_readonly_connection()
events = shared.fetch_all_events(conn, limit=30)

if not events:
    st.info("No activity recorded yet.")
for event in events:
    with st.container(border=True):
        st.write(f"{shared.humanize_event_label(event)} — {event.get('source') or 'Unknown'} · {event.get('ticket_number') or 'No reference'}")
        st.caption(shared.humanize_timestamp(event["created_at"]))

conn.close()
