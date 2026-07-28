"""LUXOR LOCKSMITH -- Operations CRM / Rule-based Dispatch Assistant.

Main entry for the review candidate: a real multipage Streamlit app built
with st.Page / st.navigation, replacing the earlier single-page
luxor_demo.py / luxor_demo_v2.py candidates (both left untouched and still
serving on their own ports until this candidate is explicitly approved).

Reuses the existing GET-only Quo integration, pending-message queue,
parser, classifier, decision engine, Luxor formatter, jobs table,
events/audit trail, password protection, and masking logic exactly as
built -- see luxor_ops_shared.py's docstring. No second client, parser,
classifier, decision engine, or persistence layer is created anywhere in
this candidate.

Positioning: this is a rule-based dispatch assistant. No LLM is connected.
Nothing in this UI claims AI-powered automation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

load_dotenv(Path(__file__).resolve().parent / ".env")

from dispatcher.quo_client import API_KEY_ENV_VAR

import luxor_ops_shared as shared

st.set_page_config(page_title="LUXOR LOCKSMITH -- Operations CRM", layout="wide")
st.markdown(shared.MOBILE_CSS, unsafe_allow_html=True)

_password = os.environ.get(shared.PASSWORD_ENV_VAR)
if not _password:
    st.error(f"This application is not configured ({shared.PASSWORD_ENV_VAR} is not set). Access refused.")
    st.stop()

if shared.AUTH_SESSION_KEY not in st.session_state:
    st.session_state[shared.AUTH_SESSION_KEY] = False

if not st.session_state[shared.AUTH_SESSION_KEY]:
    st.title("LUXOR LOCKSMITH")
    st.caption("Operations CRM · Rule-based Dispatch Assistant")
    entered = st.text_input("Password", type="password", key="luxor_ops_password_input")
    if st.button("Enter", type="primary"):
        import hmac
        if hmac.compare_digest(entered, _password):
            st.session_state[shared.AUTH_SESSION_KEY] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

operations = st.Page("luxor_ops_pages/operations.py", title="Operations", url_path="operations", default=True)
company_inbox = st.Page("luxor_ops_pages/company_inbox.py", title="Company Inbox", url_path="company-inbox")
job_workspace = st.Page("luxor_ops_pages/job_workspace.py", title="Job Workspace", url_path="job-workspace")
crm_jobs = st.Page("luxor_ops_pages/crm_jobs.py", title="CRM Jobs", url_path="crm-jobs")
activity = st.Page("luxor_ops_pages/activity.py", title="Activity", url_path="activity")

pg = st.navigation([operations, company_inbox, job_workspace, crm_jobs, activity])

# Shared frame: rendered on every page, above the selected page's own
# content. Deliberately compact -- one title line, one caption line, one
# small status line. No giant colored banners.
header_col, status_col = st.columns([3, 2])
with header_col:
    st.markdown("### LUXOR LOCKSMITH")
    st.caption("Operations CRM · Rule-based Dispatch Assistant")
with status_col:
    _connected = bool(os.environ.get(API_KEY_ENV_VAR))
    _connection_label = "🟢 Connected" if _connected else "🔴 Not configured"
    st.caption(f"{_connection_label} · 🔵 Automated dispatch preview")
st.caption(f"**{pg.title}**")
st.divider()

pg.run()
