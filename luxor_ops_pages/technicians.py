"""Technicians -- roster import preview. Shows proposed records before
inserting anything; nothing is written to the database until an explicit
Approve & Import click. Uses the existing, already-tested
storage.add_technician() -- no new persistence path. The real roster is
kept untouched until real business data is supplied; this page has never
been used against real data during this build."""

from __future__ import annotations

import csv
import io
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
    "dispatch_group, closing_group -- semicolon-separated for multi-value fields). Nothing is "
    "written until you review the preview below and explicitly approve it."
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


def _split(value) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


proposed: list[dict] = []
errors: list[str] = []

if uploaded is not None:
    text = uploaded.getvalue().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    required_columns = {"name", "areas"}
    if not required_columns.issubset(set(reader.fieldnames or [])):
        errors.append(f"CSV must include at least columns: {', '.join(sorted(required_columns))}")
    else:
        for i, row in enumerate(reader, start=2):  # header is row 1
            name = (row.get("name") or "").strip()
            areas = _split(row.get("areas"))
            if not name:
                errors.append(f"Row {i}: missing name -- skipped")
                continue
            if not areas:
                errors.append(f"Row {i}: {name!r} has no service areas -- skipped")
                continue
            proposed.append({
                "name": name,
                "areas": areas,
                "skills": _split(row.get("skills")),
                "vehicle_restrictions": _split(row.get("vehicle_restrictions")),
                "dispatch_group": (row.get("dispatch_group") or "").strip() or None,
                "closing_group": (row.get("closing_group") or "").strip() or None,
            })

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

    existing_names = {t["name"] for t in existing}
    duplicates = [r["name"] for r in proposed if r["name"] in existing_names]
    if duplicates:
        st.error(f"Already on the roster, will be skipped: {', '.join(duplicates)}")

    to_import = [r for r in proposed if r["name"] not in existing_names]
    if to_import and st.button(
        f"Approve & Import {len(to_import)} technician(s)", type="primary", key="luxor_ops_import_technicians",
    ):
        conn = storage.connect()
        imported = 0
        for rec in to_import:
            storage.add_technician(
                conn, name=rec["name"], areas=rec["areas"], skills=rec["skills"] or None,
                vehicle_restrictions=rec["vehicle_restrictions"] or None,
                dispatch_group=rec["dispatch_group"], closing_group=rec["closing_group"],
            )
            imported += 1
        conn.close()
        st.success(f"Imported {imported} technician(s).")
        st.rerun()
