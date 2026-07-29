"""Shared styling/auth/rendering helpers for LUXOR OPERATIONS DESK. No
inline script block, no click-handler attribute, and no JavaScript of
any kind, anywhere, by design (a prior candidate's custom clipboard
button rendered its own JS source as visible page text; this app avoids
that entire class of bug by never emitting JS at all, relying only on
st.code()'s native copy control).

Dark navy/charcoal visual system (deliberately not Streamlit's default
dark theme, which reads as flat black/gray) -- launched with
--theme.base dark plus explicit --theme.* colors matching the constants
below, then refined here.

render_queue_row()/render_job_workspace() below DO read the real
database (via lazily-imported dispatcher.* modules, matching this
file's existing lazy-import convention in require_login()) -- this
module is the real application's shared rendering layer, not a mock.
Every field they show comes from luxor_desk_read_model, which itself
only reads existing real tables through existing real functions."""

from __future__ import annotations

import os

PASSWORD_ENV_VAR = "LUXOR_DEMO_PASSWORD"
AUTH_SESSION_KEY = "luxor_desk_authenticated"

BG = "#0B1220"
SURFACE = "#131B2C"
TEXT = "#F1F5F9"
MUTED = "#8FA0B8"
BORDER = "rgba(255,255,255,0.09)"

SOURCE_COLORS = {
    "AGN": "#3B82F6",
    "Lucky": "#14B8A6",
    "Main Leads": "#F59E0B",
}

URGENCY_COLORS = {
    "urgent": "#F87171",
    "warning": "#F59E0B",
    "normal": "#3B4A63",
}

STAGE_COLORS = {
    "New": "#3B82F6",
    "Active": "#A78BFA",
    "Closing": "#F59E0B",
    "Done": "#34D399",
}

DESK_CSS = f"""
<style>
* {{ box-sizing: border-box; }}
html, body {{ overflow-x: hidden !important; }}
#MainMenu, footer, header[data-testid="stHeader"] {{ visibility: hidden; height: 0; }}
.stApp {{ background: {BG} !important; color: {TEXT} !important; }}
.block-container {{ padding: 0.65rem 0.65rem 2rem 0.65rem; max-width: 720px; overflow-x: hidden; }}
h1, h2, h3, h4, p, div, span, label {{ color: {TEXT}; }}

.desk-header {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.35rem 0 0.6rem 0; border-bottom: 1px solid {BORDER}; margin-bottom: 0.5rem; gap: 0.5rem;
}}
.desk-title {{ font-size: 1rem; font-weight: 700; color: {TEXT}; white-space: nowrap; }}
.desk-status-line {{ font-size: 0.72rem; color: {MUTED}; text-align: right; line-height: 1.3; }}
.desk-live-dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:#34D399; margin-right: 0.3rem; }}

.desk-section-title {{
    font-size: 0.7rem; font-weight: 700; color: {MUTED}; text-transform: uppercase;
    letter-spacing: 0.06em; margin: 0.75rem 0 0.35rem 0;
}}

/* Focused-job card -- a real st.container(key="card_<urgency>_<ticket>"),
   styled directly (not a manually-opened-and-closed raw HTML div, which
   is fragile across multiple separate st.* calls). The urgency strip
   color is selected by matching the key's urgency segment. */
[class*="st-key-card_"] {{
    background: {SURFACE} !important; border: 1px solid {BORDER} !important; border-radius: 14px !important;
    padding: 0.75rem 0.85rem 0.75rem 1rem !important; margin-bottom: 0.6rem !important;
    position: relative !important;
}}
[class*="st-key-card_"]::before {{
    content: ""; position: absolute; left: 0.35rem; top: 0.55rem; bottom: 0.55rem;
    width: 4px; border-radius: 3px; background: {URGENCY_COLORS['normal']};
}}
[class*="st-key-card_urgent_"]::before {{ background: {URGENCY_COLORS['urgent']}; }}
[class*="st-key-card_warning_"]::before {{ background: {URGENCY_COLORS['warning']}; }}
.desk-card-top {{ display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.2rem; }}
.desk-dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--dot, {MUTED}); flex-shrink: 0; }}
.desk-ticket {{ font-weight: 700; color: {TEXT}; font-size: 0.95rem; }}
.desk-age {{ color: {MUTED}; font-size: 0.72rem; margin-left: auto; white-space: nowrap; }}
.desk-meta {{ color: {MUTED}; font-size: 0.82rem; margin: 0.1rem 0; }}
.desk-meta strong {{ color: {TEXT}; font-weight: 600; }}

.desk-chip {{
    display: inline-block; padding: 0.06rem 0.5rem; border-radius: 999px;
    font-size: 0.65rem; font-weight: 700; color: #061018; letter-spacing: 0.01em;
}}
.desk-chip-outline {{
    display: inline-block; padding: 0.03rem 0.45rem; border-radius: 999px;
    font-size: 0.68rem; font-weight: 600; border: 1px solid {BORDER}; color: {MUTED};
}}
.desk-divider {{ border-top: 1px solid {BORDER}; margin: 0.5rem 0; }}
.desk-empty {{ color: {MUTED}; font-size: 0.85rem; padding: 0.5rem 0; }}

/* Two-panel layout. Mobile (default): single stacked column, focused
   workspace on top, queue below it. Desktop (>=900px): a genuine
   two-column workspace -- queue on the left, focused job on the right --
   via CSS grid-template-areas, so the *same* two containers just get
   reflowed, never duplicated markup. Scoped through the outer
   "desk_layout" container key; the `> div > [data-testid="stVerticalBlock"]`
   chain targets only the immediate child block (Streamlit's own
   key-wrapper -> border-wrapper -> vertical-block nesting), not any
   vertical block nested deeper inside the workspace/queue panels
   themselves -- those keep their own normal top-down layout untouched. */
[class*="st-key-desk_layout"] > div > [data-testid="stVerticalBlock"] {{
    display: grid !important;
    grid-template-areas: "workspace" "queue" !important;
    gap: 0.9rem !important;
    align-items: start;
}}
[class*="st-key-desk_layout"] > div > [data-testid="stVerticalBlock"] > [class*="st-key-desk_area_workspace"] {{
    grid-area: workspace;
    min-width: 0;
}}
[class*="st-key-desk_layout"] > div > [data-testid="stVerticalBlock"] > [class*="st-key-desk_area_queue"] {{
    grid-area: queue;
    min-width: 0;
}}
@media (min-width: 900px) {{
    .block-container {{ max-width: 1080px; }}
    [class*="st-key-desk_layout"] > div > [data-testid="stVerticalBlock"] {{
        grid-template-columns: 300px 1fr !important;
        grid-template-areas: "queue workspace" !important;
    }}
}}

/* Primary full-width "Copy ..." action -- a real st.expander, styled to
   read as one solid primary button. Scoped by container-key prefix so
   one rule covers every ticket without per-card CSS. */
[class*="st-key-primary_copy_"] [data-testid="stExpander"] {{
    border: none !important; background: transparent !important;
}}
[class*="st-key-primary_copy_"] [data-testid="stExpander"] summary {{
    background: #2563EB !important; color: #FFFFFF !important;
    border-radius: 12px !important; padding: 0.7rem 1rem !important;
    font-weight: 700 !important; font-size: 0.85rem !important;
    justify-content: center !important; text-align: center;
}}
[class*="st-key-primary_copy_"] [data-testid="stExpanderDetails"] {{
    background: {SURFACE} !important; border: 1px solid {BORDER} !important;
    border-radius: 10px !important; margin-top: 0.4rem !important;
}}

/* Secondary two-column action rows -- forced flex row, never stacks,
   never overflows (buttons shrink + ellipsis instead of clipping). */
[class*="st-key-actions2_"] [data-testid="stHorizontalBlock"] {{
    display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; gap: 0.45rem !important;
}}
[class*="st-key-actions2_"] [data-testid="stColumn"] {{
    flex: 1 1 0 !important; min-width: 0 !important; width: auto !important;
}}
[class*="st-key-actions2_"] button {{
    width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-size: 0.72rem !important; padding: 0.45rem 0.3rem !important; min-height: 40px !important;
    border-radius: 10px !important;
}}

/* Compact queue rows -- one real st.button per row, left-aligned. */
[class*="st-key-queue_"] button {{
    width: 100%; text-align: left !important; justify-content: flex-start !important;
    background: rgba(255,255,255,0.03) !important; border: 1px solid {BORDER} !important;
    color: {TEXT} !important; font-size: 0.78rem !important; padding: 0.5rem 0.7rem !important;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-radius: 10px !important;
}}

button[kind="secondary"], button[kind="primary"] {{ border-radius: 10px !important; }}
[data-testid="stExpander"] {{ border-radius: 10px !important; }}
</style>
"""


def is_authenticated() -> bool:
    import streamlit as st
    return bool(st.session_state.get(AUTH_SESSION_KEY))


def require_login() -> None:
    import hmac

    import streamlit as st

    password = os.environ.get(PASSWORD_ENV_VAR)
    if not password:
        st.error(f"This application is not configured ({PASSWORD_ENV_VAR} is not set). Access refused.")
        st.stop()

    if AUTH_SESSION_KEY not in st.session_state:
        st.session_state[AUTH_SESSION_KEY] = False

    if not st.session_state[AUTH_SESSION_KEY]:
        st.title("LUXOR LOCKSMITH")
        st.caption("Operations Desk -- Visual Prototype (fabricated data only)")
        entered = st.text_input("Password", type="password", key="luxor_desk_password_input")
        if st.button("Enter", type="primary"):
            if hmac.compare_digest(entered, password):
                st.session_state[AUTH_SESSION_KEY] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()


def urgency_dot_color(urgency: str) -> str:
    return URGENCY_COLORS.get(urgency, MUTED)


def source_color(source: str) -> str:
    return SOURCE_COLORS.get(source, MUTED)


def source_chip_html(source: str) -> str:
    return f'<span class="desk-chip" style="background:{source_color(source)}">{source}</span>'


def stage_chip_html(stage: str) -> str:
    color = STAGE_COLORS.get(stage, MUTED)
    return f'<span class="desk-chip" style="background:{color}">{stage.upper()}</span>'


def _lifecycle_stage(tracking: dict) -> str:
    if tracking["dispatched_manually_at"]:
        return "Dispatched"
    if tracking["acknowledged_at"]:
        return "Acknowledged"
    return "New"


def render_queue_row(entry, identity: str, selected_id: str) -> None:
    """One compact, real, clickable queue row -- every field comes
    straight from the QueueEntry the caller built via
    luxor_desk_read_model.get_live_dispatch_queue(). Clicking selects
    this job as the workspace target; nothing else about it mutates."""
    import streamlit as st

    from dispatcher.rules.sla_config import format_age

    job = entry.job
    ack = "Ack" if entry.tracking["acknowledged_at"] else ("Ack req." if entry.classify_result else "")
    dispatch = "Dispatched" if entry.tracking["dispatched_manually_at"] else "Not dispatched"
    area = ", ".join(p for p in (job.city.value, job.state.value) if p) or "Area unresolved"
    extra = f" (+{entry.extra_count})" if entry.extra_count else ""
    urgency_prefix = {"urgent": "🔴 ", "warning": "🟠 "}.get(entry.urgency, "")
    label = (
        f"{urgency_prefix}{job.source.value} · {job.ticket_number.value or 'No ref'}{extra} — "
        f"{area} · {job.service_type.value or 'Not specified'} — {format_age(entry.age_seconds)} — {dispatch}"
    )
    button_type = "primary" if identity == selected_id else "secondary"
    if st.button(label, key=f"desk_queue_row_{identity}", width="stretch", type=button_type):
        st.session_state.desk_dispatch_selected = identity
        st.rerun()


def render_job_workspace(conn, entry) -> None:
    """The full real Job Workspace for one selected QueueEntry, in the
    exact operational order: header, source message, extraction/
    validation, source-company action, Luxor message, routing, then
    stage-appropriate operating actions. Every write here calls the
    exact same storage.save_audit_entry()/mark_quo_pending_message_actioned()/
    dispatch_tracking path the original Speed Dispatch Console uses --
    no second persistence layer."""
    import streamlit as st

    import luxor_desk_read_model as rm
    from dispatcher import outbound, storage
    from dispatcher.policy import get_policy

    job, classify_result, result, tracking = entry.job, entry.classify_result, entry.result, entry.tracking
    item = entry.item
    mid = item["message_id"]

    resolution, eligible, market_label = rm.resolve_job_routing(conn, job, item["raw_text"])

    # A. Job header
    stage = _lifecycle_stage(tracking)
    from dispatcher.rules.sla_config import format_age
    st.markdown(
        f'<div class="desk-card-top">'
        f'{source_chip_html(job.source.value)}'
        f'<span class="desk-ticket">{job.ticket_number.value or "No reference"}</span>'
        f'<span class="desk-age">{format_age(entry.age_seconds)} ago</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    area_label = resolution.canonical_area or "Area unresolved"
    st.markdown(f'<div class="desk-meta">{area_label} · {job.service_type.value or "Not specified"}{" (" + market_label + ")" if market_label else ""}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div><span class="desk-chip-outline">{stage}</span> '
        f'<span class="desk-chip-outline">{eligible[0]["name"] if len(eligible) == 1 else (str(len(eligible)) + " eligible" if eligible else "No technician configured")}</span></div>',
        unsafe_allow_html=True,
    )

    # B. Source message
    with st.expander("Original company message"):
        st.code(shared_mask(item["raw_text"]))

    # C. Extraction and validation
    with st.expander("Extracted fields"):
        st.markdown(f"**Customer:** {job.customer_name.value or '—'}")
        phone_line = job.phone.value or "—"
        if job.extension.value:
            phone_line += f" ext {job.extension.value}"
        st.markdown(f"**Phone:** {phone_line}")
        st.markdown(f"**Address:** {job.address.value or '—'}, {area_label}")
        if job.vehicle.value:
            st.markdown(f"**Vehicle:** {job.vehicle.value}")
        if job.notes.value:
            st.markdown(f"**Notes:** {job.notes.value}")
        if result.decision.missing_information:
            st.warning(f"Missing: {', '.join(result.decision.missing_information)}")
        if classify_result.conflicting_fields:
            st.warning(f"Conflicting: {', '.join(classify_result.conflicting_fields)}")

    # D. Source-company action
    stream_key = rm.policy_stream_key(item["group_label"], job.source.value)
    policy = get_policy(stream_key, classify_result.message_type.value)
    if item["group_label"] == "CLOSINGS":
        st.caption(
            "Closings & Replies stream -- no new-job acknowledgment applies. "
            "Recording a confirmed closing amount/payment method is not yet available on this "
            "screen; use the existing Closings & Payments tool for that until this workspace "
            "grows a closing-entry action."
        )
    elif policy.acknowledgment_required:
        mode = outbound.current_mode()
        if mode in (outbound.OutboundMode.APPROVAL, outbound.OutboundMode.LIVE):
            if not tracking["acknowledged_at"]:
                if st.button("Send K", key=f"desk_k_{mid}", type="primary", width="stretch"):
                    storage.mark_acknowledged(conn, mid)
                    req = outbound.build_request(
                        source=job.source.value, job_id=classify_result.existing_job_id,
                        quo_contact_id=item["group_label"], text="K",
                    )
                    out_result = outbound.propose_send(conn, req, approved_by="dispatcher-tap")
                    st.caption(f"Outbound: {out_result.outcome} — {out_result.reason}")
                    st.rerun()
            else:
                st.success("✅ K acknowledged")
        else:
            if not tracking["acknowledged_at"]:
                st.caption("K ready — sending disabled")
                if st.button("Mark K sent manually", key=f"desk_k_{mid}", width="stretch"):
                    storage.mark_acknowledged(conn, mid)
                    st.rerun()
            else:
                st.success("✅ K acknowledged")
    else:
        st.caption("No acknowledgment required")

    # E. Luxor technician message
    st.markdown('<div class="desk-section-title" style="margin-top:0.6rem;">Luxor technician message</div>', unsafe_allow_html=True)
    if result.decision.missing_information:
        st.warning("HUMAN_REVIEW_NEEDED — required information is missing; do not treat this as ready to send.")
    with st.expander("Copy Luxor message", expanded=False):
        st.code(result.luxor_message)
    if st.button("Mark copied", key=f"desk_copy_{mid}", width="stretch"):
        storage.mark_copied(conn, mid)
        st.rerun()
    if tracking["copied_at"]:
        st.caption("✅ Copied")

    # F. Routing
    with st.expander("Routing"):
        st.markdown(f"**Canonical area:** {area_label}")
        if market_label:
            st.markdown(f"**Market label:** {market_label}")
        if eligible:
            st.markdown("**Eligible technicians:**")
            for t in eligible:
                st.caption(f"{t['name']} — {t['dispatch_group'] or 'WhatsApp destination not configured'}")
        else:
            st.caption("No eligible technician configured for this area/service.")
        if not resolution.canonical_area:
            st.caption(f"Routing: {resolution.reason}")

    # G. Operating actions
    destination = eligible[0]["dispatch_group"] if eligible else None
    if destination:
        if st.button("Open WhatsApp", key=f"desk_wa_{mid}", width="stretch"):
            storage.mark_whatsapp_opened(conn, mid)
            st.rerun()
    else:
        st.caption("WhatsApp destination not configured")

    can_mark_dispatched = bool(destination) and tracking["copied_at"] is not None
    if can_mark_dispatched:
        if st.button("Mark Dispatched", key=f"desk_dispatch_{mid}", type="primary", width="stretch"):
            storage.save_audit_entry(
                conn, job, classify_result, decision=result.decision,
                suggested_quo_reply=result.decision.quo_reply_suggestion,
                luxor_message=result.luxor_message, final_action="Approve", channel="manual",
            )
            storage.mark_quo_pending_message_actioned(conn, mid)
            storage.mark_dispatched_manually(conn, mid)
            st.success("Marked dispatched.")
            st.rerun()
    else:
        st.caption("Mark Dispatched requires a configured WhatsApp destination and a copied message.")

    with st.expander("More"):
        if st.button("Handle Manually", key=f"desk_hm_{mid}"):
            storage.save_audit_entry(
                conn, job, classify_result, decision=result.decision,
                luxor_message=result.luxor_message, final_action="Handle Manually", channel="manual",
            )
            storage.mark_quo_pending_message_actioned(conn, mid)
            st.rerun()
        reason = st.text_input("Reason (required to pass/cancel)", key=f"desk_pass_reason_{mid}")
        if st.button("Pass / Cancel Job", key=f"desk_reject_{mid}", disabled=not reason.strip()):
            storage.save_audit_entry(
                conn, job, classify_result, decision=result.decision,
                luxor_message=result.luxor_message, final_action="Reject", channel="manual",
                dispatcher_edits=f"Reason: {reason.strip()}",
            )
            storage.mark_quo_pending_message_actioned(conn, mid)
            st.rerun()


def shared_mask(raw_text: str) -> str:
    import luxor_ops_shared as shared
    return shared.mask_sensitive(raw_text) or ""
