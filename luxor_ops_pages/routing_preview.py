"""Technicians & Coverage -- roster grouped by area, plus a live routing
preview for real pending messages. Read-only: no assignment write, no
Quo/WhatsApp send. Reuses list_eligible_technicians_for() (never
auto-selects) and the same job-transformation gate as every other
page -- a message that doesn't qualify never reaches the Luxor
formatter here either."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
from dispatcher import storage
from dispatcher.rules.market_aliases import resolve_market_and_area
from dispatcher.rules.service_areas import find_service_area

if not st.session_state.get(shared.AUTH_SESSION_KEY):
    st.stop()

st.subheader("Technicians & Coverage")
st.info("Eligible candidates shown — no automatic assignment or message sending.")

conn = shared.get_readonly_connection()

# --------------------------------------------------- A. Roster by area ----
technicians = storage.list_technicians(conn, active_only=False)
by_area: dict = {}
for tech in technicians:
    for area in (tech["areas"] or ["(no area configured)"]):
        by_area.setdefault(area, []).append(tech)

st.markdown("**Roster by service area**")
if not technicians:
    st.caption("No technicians on the roster yet.")
for area in sorted(by_area.keys()):
    techs = by_area[area]
    with st.container(border=True):
        st.write(f"**{area}**")
        for t in techs:
            dest = t["dispatch_group"] or "WhatsApp destination not configured"
            skills = ", ".join(t["skills"]) if t["skills"] else "no specific skill restriction"
            status = "" if t["active"] else " (inactive)"
            st.caption(f"{t['name']}{status} — {skills} — {dest}")

st.divider()

# ------------------------------------ B. Live routing preview (pending) ----
st.markdown("**Live routing preview (real pending messages)**")
pending = shared.fetch_pending_messages(conn)
if not pending:
    st.info("No pending messages right now.")

service_areas = shared.get_service_areas()

for item in pending[:25]:
    job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
    stream = next((s for s in shared.STREAMS if s.group_label == item["group_label"]), None)
    stream_name = stream.display_name if stream else item["group_label"]

    market_label = shared.extract_market_label(item["raw_text"]) if job.source.value == "Main Leads" else None

    address_lookup = find_service_area(job.city.value, job.state.value, service_areas)
    address_area = (
        address_lookup.record.city or address_lookup.record.state
        if address_lookup.status == "confirmed" else None
    )
    resolution = resolve_market_and_area(address_area, market_label)

    with st.container(border=True):
        st.write(f"**{job.source.value}** · {job.ticket_number.value or 'No reference'} · {stream_name}")
        if market_label:
            st.caption(f"Market label: {market_label}")
        st.write(f"Canonical area: {resolution.canonical_area or 'Unresolved'}")
        st.caption(f"Routing reason: {resolution.reason}")
        st.write(f"Service: {job.service_type.value or 'Not specified'}")
        st.caption(f"Received {shared.humanize_timestamp(item['received_at'] or item['fetched_at'])}")

        missing = []
        if not job.ticket_number.value:
            missing.append("ticket/reference")
        if not job.phone.value:
            missing.append("phone")
        if not job.address.value:
            missing.append("address")
        if missing:
            st.warning(f"Missing: {', '.join(missing)}")

        if resolution.status == "resolved" and resolution.canonical_area:
            eligible = storage.list_eligible_technicians_for(conn, resolution.canonical_area, job.service_type.value)
            if eligible:
                candidates = ", ".join(
                    f"{t['name']} ({t['dispatch_group'] or 'WhatsApp destination not configured'})" for t in eligible
                )
                st.write(f"Eligible candidates: {candidates}")
            else:
                st.warning("No eligible technician configured for this area/service — Needs Review.")
        else:
            st.warning(f"Needs Review — {resolution.reason}")

        if shared.shows_job_transformation(classify_result, job):
            result = shared.cached_full_preview(item["raw_text"], conn)
            st.code(result.luxor_message)
            st.caption("Prepared preview — not sent")
        else:
            st.caption("Not a complete job message — no Luxor preview.")

conn.close()
