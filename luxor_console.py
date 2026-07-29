"""LUXOR LOCKSMITH -- Speed Dispatch Console.

Optimized for the real workflow: new job arrives -> acknowledge (K) if
the source requires it -> Luxor message is already prepared -> copy ->
open WhatsApp -> mark dispatched. Default page is Dispatch Queue, not
statistics -- this is a real, write-capable tool (unlike the read-only
Management CRM candidates), using the exact same storage.save_audit_entry()
+ mark_quo_pending_message_actioned() path app.py's own buttons use.

Top navigation (position="top") so the five destinations are always
visible on portrait mobile, never hidden behind the collapsed sidebar.
"""

from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
load_dotenv(Path(__file__).resolve().parent / ".env")

import luxor_ops_shared as shared
from dispatcher.quo_client import API_KEY_ENV_VAR

AUTH_SESSION_KEY = "luxor_console_authenticated"

st.set_page_config(page_title="LUXOR LOCKSMITH -- Speed Dispatch", layout="centered")
st.markdown(shared.MOBILE_CSS, unsafe_allow_html=True)

_password = os.environ.get(shared.PASSWORD_ENV_VAR)
if not _password:
    st.error(f"This application is not configured ({shared.PASSWORD_ENV_VAR} is not set). Access refused.")
    st.stop()

if AUTH_SESSION_KEY not in st.session_state:
    st.session_state[AUTH_SESSION_KEY] = False

if not st.session_state[AUTH_SESSION_KEY]:
    st.title("LUXOR LOCKSMITH")
    st.caption("Speed Dispatch Console")
    entered = st.text_input("Password", type="password", key="luxor_console_password_input")
    if st.button("Enter", type="primary"):
        if hmac.compare_digest(entered, _password):
            st.session_state[AUTH_SESSION_KEY] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

dispatch_queue = st.Page("luxor_console_pages/dispatch_queue.py", title="Dispatch Queue", url_path="dispatch-queue", default=True)
active_jobs = st.Page("luxor_console_pages/active_jobs.py", title="Active Jobs", url_path="active-jobs")
closings = st.Page("luxor_console_pages/closings.py", title="Closings", url_path="closings")
technicians = st.Page("luxor_console_pages/technicians.py", title="Technicians", url_path="technicians")
activity = st.Page("luxor_console_pages/activity.py", title="Activity", url_path="activity")

pg = st.navigation([dispatch_queue, active_jobs, closings, technicians, activity], position="top")

_connected = bool(os.environ.get(API_KEY_ENV_VAR))
header_col, status_col = st.columns([3, 2])
with header_col:
    st.markdown("##### LUXOR LOCKSMITH")
with status_col:
    _header_conn = shared.get_readonly_connection()
    _state, _state_label = shared.connection_state(_header_conn, _connected)
    _header_conn.close()
    st.caption(_state_label)

pg.run()
