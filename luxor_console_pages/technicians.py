"""Technicians -- compact, read-only roster reference grouped by area.
Import/edit stays in the Management CRM; this is a quick-glance lookup
for the dispatcher mid-workflow."""

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
technicians = storage.list_technicians(conn, active_only=True)

by_area: dict = {}
for tech in technicians:
    for area in (tech["areas"] or ["(no area configured)"]):
        by_area.setdefault(area, []).append(tech)

st.caption(f"{len(technicians)} active technician(s)")
for area in sorted(by_area.keys()):
    with st.container(border=True):
        st.write(f"**{area}**")
        for t in by_area[area]:
            dest = t["dispatch_group"] or "WhatsApp destination not configured"
            skills = ", ".join(t["skills"]) if t["skills"] else "no specific skill restriction"
            st.caption(f"{t['name']} — {skills} — {dest}")

conn.close()
