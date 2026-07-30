"""Technicians -- the real technician roster, unchanged.
storage.list_technicians() directly via luxor_desk_read_model, grouped
by canonical area. No substitution, no fabricated names, no Python
lists/JSON/database IDs shown in the normal view."""

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
shared_desk.render_header(conn, "Technicians")
technicians = rm.get_technician_coverage(conn)
active_count = sum(1 for t in technicians if t["active"])

by_area: dict = {}
for tech in technicians:
    for area in (tech["areas"] or ["(no area configured)"]):
        by_area.setdefault(area, []).append(tech)

st.caption(f"{active_count} active · {len(technicians) - active_count} inactive")
for area in sorted(by_area.keys()):
    st.markdown(f'<div class="desk-section-title">{area}</div>', unsafe_allow_html=True)
    for t in by_area[area]:
        status = "" if t["active"] else " (inactive)"
        skills = ", ".join(t["skills"]) if t["skills"] else "No specific skill restriction"
        vehicle = ", ".join(t["vehicle_restrictions"]) if t["vehicle_restrictions"] else "No vehicle restriction"
        dispatch_dest = t["dispatch_group"] or None
        closing_dest = t["closing_group"] or None
        st.markdown(
            f'<div class="desk-meta"><strong>{t["name"]}{status}</strong></div>'
            f'<div class="desk-meta">Skills: {skills}</div>'
            f'<div class="desk-meta">Vehicle restrictions: {vehicle}</div>'
            f'<div class="desk-meta">Dispatch destination: {dispatch_dest or "Not configured"}</div>'
            f'<div class="desk-meta">Closing destination: {closing_dest or "Not configured"}</div>',
            unsafe_allow_html=True,
        )
        if not dispatch_dest or not closing_dest:
            missing = ", ".join(
                label for label, val in (("dispatch destination", dispatch_dest), ("closing destination", closing_dest)) if not val
            )
            st.markdown(
                f'<span class="desk-warning-chip">Missing {missing}</span>',
                unsafe_allow_html=True,
            )
        st.markdown('<div class="desk-divider"></div>', unsafe_allow_html=True)

conn.close()
