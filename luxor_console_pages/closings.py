"""Closings -- compact reference: closed jobs awaiting reconciliation.
Read-only quick reference; full Closings & Payments workflow stays in
the Management CRM."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
from dispatcher import storage

if not st.session_state.get("luxor_console_authenticated"):
    st.stop()

conn = shared.get_readonly_connection()
jobs = shared.fetch_jobs(conn)
closed = [j for j in jobs if j["status"] == "Closed"]

unsettled = []
for job in closed:
    row = conn.execute("SELECT reconciliation_state FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    if not row or row[0] != "Settled":
        unsettled.append(job)

st.caption(f"{len(unsettled)} closed job(s) awaiting reconciliation")
if not unsettled:
    st.success("No unsettled closed jobs.")
for job in unsettled:
    details = storage.get_latest_closing_details(conn, job["id"])
    with st.container(border=True):
        st.write(f"**{job['ticket_number']}** · {job['source']}")
        if details:
            st.caption(f"Amount: {details['amount']} · Payment: {details['payment_method'] or 'not recorded'}")
        else:
            st.caption("No structured closing amount recorded.")

conn.close()
