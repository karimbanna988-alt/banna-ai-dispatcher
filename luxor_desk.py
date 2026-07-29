"""LUXOR LOCKSMITH OPERATIONS -- the real, database-backed dispatcher
application. Fabricated data (luxor_desk_data.py) is never imported here
or by any page under luxor_desk_pages/ -- every displayed value traces
back to a real row in the existing SQLite database via
luxor_desk_read_model.py, which itself only calls existing, already-
tested backend functions (dispatcher.storage, dispatcher.policy,
dispatcher.pipeline via luxor_ops_shared's cached wrappers). No second
database, no second parser, no second decision engine, no second
persistence layer. Quo outbound stays disabled; WhatsApp stays manual
copy/open only -- see luxor_desk_shared.render_job_workspace().

Five real sections, one persistent top-of-page navigation (visible on
both desktop and portrait iPhone, never hidden in the collapsed
sidebar): Dispatch, Active Jobs, Closings, Technicians, Activity.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import luxor_desk_shared as shared

st.set_page_config(page_title="LUXOR OPERATIONS DESK", layout="wide")
st.markdown(shared.DESK_CSS, unsafe_allow_html=True)

shared.require_login()

dispatch = st.Page("luxor_desk_pages/dispatch.py", title="Dispatch", url_path="dispatch", default=True)
active_jobs = st.Page("luxor_desk_pages/active_jobs.py", title="Active Jobs", url_path="active-jobs")
closings = st.Page("luxor_desk_pages/closings.py", title="Closings", url_path="closings")
technicians = st.Page("luxor_desk_pages/technicians.py", title="Technicians", url_path="technicians")
activity = st.Page("luxor_desk_pages/activity.py", title="Activity", url_path="activity")

pg = st.navigation([dispatch, active_jobs, closings, technicians, activity], position="top")

st.markdown('<div class="desk-title" style="margin-bottom:0.5rem;">LUXOR LOCKSMITH OPERATIONS</div>', unsafe_allow_html=True)

pg.run()
