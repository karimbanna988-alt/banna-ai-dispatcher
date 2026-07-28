"""Technicians -- roster import preview. Shows proposed records before
inserting anything; nothing is written to the database until an explicit
Approve & Import click. Uses the existing, already-tested
storage.add_technician() -- no new persistence path. The real roster is
kept untouched until real business data is supplied; this page has never
been used against real data during this build."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
from dispatcher import storage

if not st.session_state.get(shared.AUTH_SESSION_KEY):
    st.stop()

st.subheader("Technicians")
st.caption(
    "Import a roster from a CSV file (columns: name, areas, skills, vehicle_restrictions, "
    "dispatch_group, closing_group -- semicolon-separated for multi-value fields). Each area is "
    "validated against the canonical service-area configuration. Nothing is written until you "
    "review the preview below and explicitly approve it."
)

conn_ro = shared.get_readonly_connection()
existing = storage.list_technicians(conn_ro)
conn_ro.close()

st.markdown(f"**Current roster: {len(existing)} technician(s)**")
if not existing:
    st.info("No technicians on the roster yet.")
for tech in existing:
    st.write(f"{tech['name']} · {'Active' if tech['active'] else 'Inactive'} · Areas: {', '.join(tech['areas']) or 'none'}")

st.divider()
uploaded = st.file_uploader("Roster CSV", type=["csv"], key="luxor_ops_technician_csv")

proposed: list[dict] = []
errors: list[str] = []

if uploaded is not None:
    text = uploaded.getvalue().decode("utf-8")
    existing_names = {t["name"] for t in existing}
    proposed, errors = shared.parse_technician_csv(text, existing_names, shared.get_service_areas())

for error in errors:
    st.warning(error)

if proposed:
    st.markdown(f"**Proposed records ({len(proposed)})** — not yet saved")
    for rec in proposed:
        with st.container(border=True):
            st.write(f"**{rec['name']}**")
            st.caption(f"Areas: {', '.join(rec['areas'])}")
            if rec["skills"]:
                st.caption(f"Skills: {', '.join(rec['skills'])}")
            if rec["vehicle_restrictions"]:
                st.caption(f"Vehicle restrictions: {', '.join(rec['vehicle_restrictions'])}")
            st.caption(f"Dispatch destination: {rec['dispatch_group'] or 'not set'}")
            st.caption(f"Closing destination: {rec['closing_group'] or 'not set'}")

    if proposed and st.button(
        f"Approve & Import {len(proposed)} technician(s)", type="primary", key="luxor_ops_import_technicians",
    ):
        conn = storage.connect()
        imported = 0
        for rec in proposed:
            storage.add_technician(
                conn, name=rec["name"], areas=rec["areas"], skills=rec["skills"] or None,
                vehicle_restrictions=rec["vehicle_restrictions"] or None,
                dispatch_group=rec["dispatch_group"], closing_group=rec["closing_group"],
            )
            imported += 1
        conn.close()
        st.success(f"Imported {imported} technician(s).")
        st.rerun()
