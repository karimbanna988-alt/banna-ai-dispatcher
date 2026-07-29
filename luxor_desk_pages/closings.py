"""Closings -- only real closing work. A closing only ever appears here
when storage.get_latest_closing_details() returns a real structured
record for that job; amount/payment method are never invented. Empty
state is literal, never a fabricated placeholder card."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import luxor_desk_read_model as rm
import luxor_desk_shared as shared_desk
from dispatcher import storage

if not shared_desk.is_authenticated():
    st.stop()

conn = storage.connect()
unsettled = rm.get_pending_closings(conn)

st.caption(f"{len(unsettled)} closed job(s) awaiting reconciliation")
if not unsettled:
    st.success("No closings awaiting action.")

for job in unsettled:
    details = storage.get_latest_closing_details(conn, job["id"])
    st.markdown(
        f'<div class="desk-card-top">'
        f'{shared_desk.source_chip_html(job["source"])}'
        f'<span class="desk-ticket">{job["ticket_number"]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if details:
        st.markdown(
            f'<div class="desk-meta">Amount: <strong>{details["amount"] or "—"}</strong> · '
            f'{details["payment_method"] or "Payment method not recorded"}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption("No structured closing amount recorded yet for this job.")
    st.markdown('<div class="desk-divider"></div>', unsafe_allow_html=True)

conn.close()
