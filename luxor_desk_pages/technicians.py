"""Technician Coverage -- the real 15-row technician roster, unchanged.
storage.list_technicians() directly via luxor_desk_read_model, grouped
by canonical area. No substitution, no fabricated names."""

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
        skills = ", ".join(t["skills"]) if t["skills"] else "no specific skill restriction"
        dest = t["dispatch_group"] or "WhatsApp destination not configured"
        st.markdown(
            f'<div class="desk-meta"><strong>{t["name"]}{status}</strong> — {skills} — {dest}</div>',
            unsafe_allow_html=True,
        )

conn.close()
