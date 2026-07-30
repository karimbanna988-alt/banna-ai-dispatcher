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

Seven real sections, one persistent top-of-page navigation (visible on
both desktop and portrait iPhone, never hidden in the collapsed
sidebar): New Jobs, Active Jobs, Closings, History, Technicians,
Activity, Shadow Review. The first four are the primary dispatcher
workflow; Technicians/Activity/Shadow Review stay available as secondary
reference/audit views. Every page renders its own shared header via
luxor_desk_shared.render_header() -- no separate top-level app title here.
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

dispatch = st.Page("luxor_desk_pages/dispatch.py", title="New Jobs", url_path="new-jobs", default=True)
active_jobs = st.Page("luxor_desk_pages/active_jobs.py", title="Active Jobs", url_path="active-jobs")
closings = st.Page("luxor_desk_pages/closings.py", title="Closings", url_path="closings")
history = st.Page("luxor_desk_pages/history.py", title="History", url_path="history")
technicians = st.Page("luxor_desk_pages/technicians.py", title="Technicians", url_path="technicians")
activity = st.Page("luxor_desk_pages/activity.py", title="Activity", url_path="activity")
shadow_review = st.Page("luxor_desk_pages/shadow_review.py", title="Shadow Review", url_path="shadow-review")

# Canonical order: New Jobs -> Active Jobs -> Closings -> History are the
# primary dispatcher workflow. Technicians stays available as secondary
# reference; Activity stays available as a secondary generic audit/
# developer feed -- normal dispatch history now belongs on History
# instead. Shadow Review is appended last -- a secondary, passive-
# observation-only audit view, never part of the primary dispatch
# workflow.
pg = st.navigation([dispatch, active_jobs, closings, history, technicians, activity, shadow_review], position="top")

pg.run()
