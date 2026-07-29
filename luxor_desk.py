"""LUXOR OPERATIONS DESK -- a workflow-first visual prototype.

Fabricated data only (luxor_desk_data.py). This file never imports
dispatcher.storage, never opens the real SQLite database, never imports
dispatcher.quo_client or dispatcher.quo_ingest, and never starts the
background poller. Completely separate from luxor_console.py,
luxor_ops.py, and luxor_prototype.py.

One focused job at a time, not an endless page of expanded cards: a
compact stage navigator (New / Active / Closing, Done hidden when
empty) and a source dropdown narrow the working set; the newest
actionable job in that set is shown in full with a tiered action
hierarchy (one primary action, then small secondary rows); everything
else in the set renders as a one-line queue row that becomes the
focused job when tapped. No custom JavaScript anywhere -- the only
"copy" mechanism is st.code()'s native copy control, revealed inside a
collapsed-by-default expander.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import luxor_desk_shared as shared
from luxor_desk_data import DESK_JOBS

st.set_page_config(page_title="LUXOR OPERATIONS DESK", layout="centered")
st.markdown(shared.DESK_CSS, unsafe_allow_html=True)

shared.require_login()

# ------------------------------------------------------------ Header -----
header_left, header_right = st.columns([1, 1])
with header_left:
    st.markdown('<div class="desk-title">LUXOR LOCKSMITH</div>', unsafe_allow_html=True)
with header_right:
    st.markdown(
        '<div class="desk-status-line"><span class="desk-live-dot"></span>Live &nbsp;·&nbsp; '
        'Synced 12 sec ago</div>',
        unsafe_allow_html=True,
    )
sync_col = st.columns([1, 1, 1])[2]
with sync_col:
    st.button("Sync", key="desk_sync", width="stretch")
st.markdown('<div style="border-bottom:1px solid rgba(255,255,255,0.09); margin-bottom:0.5rem;"></div>', unsafe_allow_html=True)

# ---------------------------------------------------- Stage navigator ----
STAGE_ORDER = ("New", "Active", "Closing", "Done")
stage_counts = {stage: sum(1 for j in DESK_JOBS if j.stage == stage) for stage in STAGE_ORDER}
visible_stages = [s for s in STAGE_ORDER if stage_counts[s] > 0]

if "desk_stage" not in st.session_state or st.session_state.desk_stage not in visible_stages:
    st.session_state.desk_stage = visible_stages[0]

stage_filter = st.pills(
    "Stage", visible_stages, selection_mode="single", default=st.session_state.desk_stage,
    key="desk_stage_pills", label_visibility="collapsed",
    format_func=lambda s: f"{s} ({stage_counts[s]})",
) or visible_stages[0]
st.session_state.desk_stage = stage_filter

# ------------------------------------------------------- Source filter ---
SOURCE_OPTIONS = ("All Sources", "AGN", "Lucky", "Main Leads")
source_filter = st.selectbox("Source", SOURCE_OPTIONS, key="desk_source_select", label_visibility="collapsed")

# ---------------------------------------------------------- Filtering ----
urgency_rank = {"urgent": 0, "warning": 1, "normal": 2}
working_set = [
    j for j in DESK_JOBS
    if j.stage == stage_filter and (source_filter == "All Sources" or j.source == source_filter)
]
working_set.sort(key=lambda j: urgency_rank.get(j.urgency, 3))

if not working_set:
    st.markdown('<div class="desk-empty">No jobs in this view.</div>', unsafe_allow_html=True)
    st.stop()

working_tickets = [j.ticket for j in working_set]
focused_ticket = st.session_state.get("desk_focused_ticket")
if focused_ticket not in working_tickets:
    focused_ticket = working_tickets[0]
focused_job = next(j for j in working_set if j.ticket == focused_ticket)
queue_jobs = [j for j in working_set if j.ticket != focused_ticket][:3]


def summary_block(job) -> None:
    strip = shared.urgency_dot_color(job.urgency)
    st.markdown(
        f'<div class="desk-card-top">'
        f'<span class="desk-dot" style="--dot:{strip}"></span>'
        f'{shared.source_chip_html(job.source)}'
        f'<span class="desk-ticket">{job.ticket}</span>'
        f'<span class="desk-age">{job.age_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="desk-meta">{job.city_state} · {job.service}</div>', unsafe_allow_html=True)
    ack_label = "Acknowledged" if job.acknowledged else ("Ack required" if job.acknowledgment_required else "No ack needed")
    st.markdown(
        f'<div>'
        f'<span class="desk-chip-outline">{ack_label}</span> '
        f'<span class="desk-chip-outline">{job.technician or "Unassigned"}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def job_details_expander(job) -> None:
    with st.expander("Job details"):
        st.markdown(f"**Customer:** {job.customer}")
        st.markdown(f"**Phone:** {job.phone}")
        st.markdown(f"**Address:** {job.address}")
        if job.vehicle:
            st.markdown(f"**Vehicle:** {job.vehicle}")
        st.markdown(f"**Notes:** {job.notes}")
        if job.missing:
            st.warning(f"Missing: {', '.join(job.missing)}")
        if job.conflicting:
            st.warning(f"Conflicting: {', '.join(job.conflicting)}")
        st.markdown("**Original company message**")
        st.code(job.original_message)
        st.markdown("**LUXOR-formatted message**")
        st.code(job.luxor_message)


def primary_copy_action(ticket: str, label: str, code_text: str) -> None:
    with st.container(key=f"primary_copy_{ticket}"):
        with st.expander(label):
            st.code(code_text)


def pressed_chip_or_button(label: str, key: str) -> None:
    pressed_key = f"pressed_{key}"
    if st.session_state.get(pressed_key):
        st.markdown(f'<span class="desk-chip-outline">✓ {label}</span>', unsafe_allow_html=True)
    elif st.button(label, key=key, width="stretch"):
        st.session_state[pressed_key] = True
        st.rerun()


def queue_exception_label(job) -> str:
    """One short current-status/exception fragment shown on the queue
    row, matching the approved static composition (e.g. "Missing notes",
    "ETA ready") -- never invents anything not already on the job."""
    if job.missing:
        return f"Missing {job.missing[0]}"
    if job.conflicting:
        return "Conflicting info"
    if job.stage == "Active" and job.tech_status:
        return f"{job.tech_status}"
    return ""


# Two-panel layout: on mobile this renders as one stacked column (focused
# workspace, then the queue below it); on desktop (>=900px, see
# shared.DESK_CSS) the same two areas become a left queue / right
# workspace split via CSS grid-template-areas -- no duplicate markup, no
# JS, same underlying content either way.
with st.container(key="desk_layout"):
    with st.container(key="desk_area_workspace"):
        st.markdown('<div class="desk-section-title">Focused job</div>', unsafe_allow_html=True)

        ticket = focused_job.ticket
        card_key = f"card_{focused_job.urgency}_{ticket}"
        with st.container(key=card_key):
            summary_block(focused_job)
            job_details_expander(focused_job)

            if focused_job.stage == "New":
                primary_copy_action(ticket, "COPY LUXOR JOB", focused_job.luxor_message)
                if focused_job.acknowledgment_required:
                    with st.container(key=f"actions2_ack_{ticket}"):
                        c1, c2 = st.columns(2)
                        with c1:
                            pressed_chip_or_button("K READY", f"k_ready_{ticket}")
                        with c2:
                            pressed_chip_or_button("OPEN WHATSAPP", f"open_wa_{ticket}")
                else:
                    pressed_chip_or_button("OPEN WHATSAPP", f"open_wa_{ticket}")
                with st.container(key=f"actions2_final_{ticket}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        pressed_chip_or_button("MARK DISPATCHED", f"mark_dispatched_{ticket}")
                    with c2:
                        pressed_chip_or_button("NEEDS REVIEW", f"needs_review_{ticket}")

            elif focused_job.stage == "Active":
                status = focused_job.tech_status or "Not reported"
                st.markdown(f'<div class="desk-meta">Technician status: <strong>{status}</strong></div>', unsafe_allow_html=True)
                update_text = focused_job.company_update_message or (
                    f"LUXOR LOCKSMITH\nTECHNICIAN UPDATE\n\nTicket: {focused_job.ticket}\nStatus: {status}"
                )
                primary_copy_action(ticket, "COPY COMPANY UPDATE", update_text)
                with st.container(key=f"actions2_active_{ticket}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        pressed_chip_or_button("MARK UPDATE SENT", f"update_sent_{ticket}")
                    with c2:
                        pressed_chip_or_button("CANCEL / PASS", f"cancel_pass_{ticket}")

            elif focused_job.stage == "Closing":
                st.markdown(
                    f'<div class="desk-meta">Amount: <strong>{focused_job.closing_amount}</strong> · '
                    f'{focused_job.payment_method} · {focused_job.technician}</div>',
                    unsafe_allow_html=True,
                )
                closing_text = focused_job.company_closing_message or f"LUXOR LOCKSMITH\nCLOSING\n\nTicket: {focused_job.ticket}"
                primary_copy_action(ticket, "COPY COMPANY CLOSING", closing_text)
                with st.container(key=f"actions2_closing_{ticket}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        pressed_chip_or_button("MARK CLOSING SENT", f"closing_sent_{ticket}")
                    with c2:
                        pressed_chip_or_button("NEEDS REVIEW", f"needs_review_{ticket}")

    with st.container(key="desk_area_queue"):
        if queue_jobs:
            st.markdown(f'<div class="desk-section-title">Also in {stage_filter.lower()} ({len(queue_jobs)})</div>', unsafe_allow_html=True)
            for job in queue_jobs:
                exception = queue_exception_label(job)
                row_label = f"{job.source} · {job.ticket} — {job.city_state} · {job.service} — {job.age_label}"
                if exception:
                    row_label += f" — {exception}"
                if st.button(row_label, key=f"queue_{job.ticket}", width="stretch"):
                    st.session_state.desk_focused_ticket = job.ticket
                    st.rerun()
