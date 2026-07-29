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
        st.caption("Operations Desk -- live dispatcher workspace (real data, Quo outbound disabled)")
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
    """The full real Job Workspace for one selected QueueEntry, built
    around the dispatcher's actual workflow, not engineering mechanics:
    header -> (HUMAN REVIEW NEEDED gate, if required data is missing) ->
    acknowledgment -> the already-prepared Luxor message -> one combined
    WhatsApp dispatch action -> secondary actions -> "View details" for
    everything else (raw message, extracted fields, routing internals).
    Every write here calls the exact same storage.save_audit_entry()/
    mark_quo_pending_message_actioned()/dispatch_tracking path the
    original Speed Dispatch Console uses -- no second persistence layer.
    """
    import streamlit as st

    import luxor_desk_read_model as rm
    from dispatcher import outbound, storage
    from dispatcher.policy import get_policy
    from dispatcher.rules.sla_config import format_age

    job, classify_result, result, tracking = entry.job, entry.classify_result, entry.result, entry.tracking
    item = entry.item
    mid = item["message_id"]

    resolution, eligible, market_label = rm.resolve_job_routing(conn, job, item["raw_text"])
    area_label = resolution.canonical_area or "Area unresolved"
    stage = _lifecycle_stage(tracking)

    # Header
    st.markdown(
        f'<div class="desk-card-top">'
        f'{source_chip_html(job.source.value)}'
        f'<span class="desk-ticket">{job.ticket_number.value or "No reference"}</span>'
        f'<span class="desk-age">{format_age(entry.age_seconds)} ago</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="desk-meta">{area_label} · {job.service_type.value or "Not specified"}{" (" + market_label + ")" if market_label else ""}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div><span class="desk-chip-outline">{stage}</span> '
        f'<span class="desk-chip-outline">{eligible[0]["name"] if len(eligible) == 1 else (str(len(eligible)) + " eligible" if eligible else "No technician configured")}</span></div>',
        unsafe_allow_html=True,
    )

    needs_review = bool(result.decision.missing_information) or bool(classify_result.conflicting_fields)

    if needs_review:
        problems = list(result.decision.missing_information) + [f"conflicting {f}" for f in classify_result.conflicting_fields]
        st.warning(f"HUMAN REVIEW NEEDED — {', '.join(problems)}")
        with st.expander("View details"):
            _render_details(conn, item, job, area_label, market_label, eligible, resolution)
        with st.expander("Handle manually / pass"):
            _render_secondary_actions(conn, mid, job, classify_result, result)
        return

    stream_key = rm.policy_stream_key(item["group_label"], job.source.value)
    policy = get_policy(stream_key, classify_result.message_type.value)

    if item["group_label"] == "CLOSINGS":
        st.caption(
            "Closings & Replies stream -- no new-job acknowledgment applies. Recording a "
            "confirmed closing still uses the existing Closings & Payments tool for now."
        )
    elif policy.acknowledgment_required:
        mode = outbound.current_mode()
        if mode in (outbound.OutboundMode.APPROVAL, outbound.OutboundMode.LIVE):
            if not tracking["acknowledged_at"]:
                if st.button("SEND K", key=f"desk_k_{mid}", type="primary", width="stretch"):
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
        elif not tracking["acknowledged_at"]:
            k_cols = st.columns(2)
            with k_cols[0]:
                st.code("K")
            with k_cols[1]:
                if st.button("MARK K SENT MANUALLY", key=f"desk_k_{mid}", width="stretch"):
                    storage.mark_acknowledged(conn, mid)
                    st.rerun()
        else:
            st.success("✅ K acknowledged")
    else:
        st.caption("NO ACKNOWLEDGMENT REQUIRED")

    # The already-prepared Luxor technician message -- visible
    # immediately, never behind an "Extracted fields"/"Copy Luxor
    # message" step first.
    st.code(result.luxor_message)

    destination = eligible[0]["dispatch_group"] if eligible else None
    dispatched = tracking["dispatched_manually_at"] is not None

    if dispatched:
        st.success("✅ Dispatched")
    else:
        if not destination:
            st.caption("WhatsApp destination not configured")
        if st.button("DISPATCH IN WHATSAPP", key=f"desk_wa_{mid}", type="primary", width="stretch", disabled=not destination):
            storage.mark_copied(conn, mid)
            storage.mark_whatsapp_opened(conn, mid)
            st.rerun()
        if tracking["whatsapp_opened_at"] and not tracking["dispatched_manually_at"]:
            if st.button("CONFIRM DISPATCHED", key=f"desk_confirm_dispatch_{mid}", type="primary", width="stretch"):
                storage.save_audit_entry(
                    conn, job, classify_result, decision=result.decision,
                    suggested_quo_reply=result.decision.quo_reply_suggestion,
                    luxor_message=result.luxor_message, final_action="Approve", channel="manual",
                )
                storage.mark_quo_pending_message_actioned(conn, mid)
                storage.mark_dispatched_manually(conn, mid)
                st.success("Marked dispatched.")
                st.rerun()

    with st.expander("View details"):
        _render_details(conn, item, job, area_label, market_label, eligible, resolution)

    with st.expander("Handle manually / pass"):
        _render_secondary_actions(conn, mid, job, classify_result, result)


def _render_details(conn, item, job, area_label, market_label, eligible, resolution) -> None:
    """Everything that supports the dispatcher's decision but isn't the
    decision itself: raw message, extracted fields, routing internals --
    all one optional panel, per the standing "no accordions before a
    normal job can be acted on" requirement."""
    import streamlit as st

    st.markdown("**Original company message**")
    st.code(shared_mask(item["raw_text"]))

    st.markdown("**Extracted fields**")
    st.markdown(f"Customer: {job.customer_name.value or '—'}")
    phone_line = job.phone.value or "—"
    if job.extension.value:
        phone_line += f" ext {job.extension.value}"
    st.markdown(f"Phone: {phone_line}")
    st.markdown(f"Address: {job.address.value or '—'}, {area_label}")
    if job.vehicle.value:
        st.markdown(f"Vehicle: {job.vehicle.value}")
    if job.notes.value:
        st.markdown(f"Notes: {job.notes.value}")

    st.markdown("**Routing**")
    st.markdown(f"Canonical area: {area_label}")
    if market_label:
        st.markdown(f"Market label: {market_label}")
    if eligible:
        for t in eligible:
            st.caption(f"{t['name']} — {t['dispatch_group'] or 'WhatsApp destination not configured'}")
    else:
        st.caption("No eligible technician configured for this area/service.")
    if not resolution.canonical_area:
        st.caption(f"Routing: {resolution.reason}")


def _render_secondary_actions(conn, mid, job, classify_result, result) -> None:
    import streamlit as st
    from dispatcher import storage

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


# ------------------------------------------------------------------------
# Active Jobs / Closings / Completed-History -- all real, all wired to the
# purpose-built storage.* operational methods (job_id + validated input
# only). None of these reconstruct an ExtractedJob; every write goes
# through storage.record_technician_update()/mark_company_update_*()/
# pass_or_cancel_job()/record_job_closing()/mark_company_closing_*()/
# complete_job(), the same single set of methods the focused tests cover.

UPDATE_TYPES = (
    "Going", "ETA", "No Answer", "No Show", "Not Available",
    "Customer Cancelled", "Completed", "Custom",
)


def render_job_row(job: dict, identity: str, selected_id: str, key_prefix: str, subtitle: str) -> None:
    """One compact, real, clickable list row for Active Jobs/Closings/
    Completed-History -- built from a plain committed-job dict (the
    luxor_ops_shared.fetch_jobs() shape), not the New Jobs QueueEntry
    shape render_queue_row() expects. Clicking selects this job as the
    workspace target; nothing else about it mutates."""
    import streamlit as st

    label = f"{job['source']} · {job['ticket_number'] or 'No ref'} — {subtitle}"
    button_type = "primary" if identity == selected_id else "secondary"
    if st.button(label, key=f"desk_queue_row_{key_prefix}_{identity}", width="stretch", type=button_type):
        st.session_state[f"desk_{key_prefix}_selected"] = identity


def _service_area_and_description(fields: dict) -> tuple[str, str]:
    city, state = fields.get("city"), fields.get("state")
    area = ", ".join(p for p in (city, state) if p) or "Area unresolved"
    description = " -- ".join(p for p in (fields.get("service_type"), fields.get("vehicle")) if p) or "No description"
    return area, description


def active_job_next_action_label(job: dict) -> str:
    """One clear next action for a compact Active Jobs row -- never
    reads anything not already computed by
    luxor_desk_read_model.get_active_jobs_detailed()."""
    latest = job.get("latest_technician_update")
    if latest is None:
        return "Needs tech update"
    if latest["update_type"] == "Completed":
        return "Ready to close"
    if latest["update_type"] == "Custom":
        return "Needs review"
    return "In progress"


def render_active_job_workspace(conn, job_id: int) -> None:
    """The real Active Job workspace for one selected, already-committed
    job -- record a technician update, prepare/copy/mark-sent the
    company-facing reply, pass/cancel with a reason, or move on to
    Closing once the technician reports Completed. Always re-reads the
    canonical job via storage.get_job_by_id() (never the masked,
    display-oriented fetch_jobs() row) -- the closing/update templates
    need the real, unmasked field values."""
    import streamlit as st

    import luxor_desk_read_model as rm
    import luxor_ops_shared as shared
    from dispatcher import storage, updates
    from dispatcher.storage import InvalidJobActionError

    job = storage.get_job_by_id(conn, job_id)
    if job is None:
        st.error("This job no longer exists.")
        return
    area, description = _service_area_and_description(job["fields"])
    latest = rm.get_latest_technician_update(conn, job_id)

    st.markdown(
        f'<div class="desk-card-top">{source_chip_html(job["source"])}'
        f'<span class="desk-ticket">{job["ticket_number"] or "No reference"}</span>'
        f'<span class="desk-age">{job["status"]}</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="desk-meta">{area} · {description}</div>'
        f'<div class="desk-meta">Technician: <strong>{job["assigned_technician_name"] or "Unassigned"}</strong>'
        f' · WhatsApp: <strong>{job["assigned_technician_dispatch_group"] or "Not configured"}</strong></div>',
        unsafe_allow_html=True,
    )
    if latest:
        eta_note = f" ({latest['eta_minutes']} min)" if latest.get("eta_minutes") else ""
        st.markdown(
            f'<div class="desk-meta">Latest technician update: <strong>{latest["update_type"]}{eta_note}</strong>'
            f' · {shared.humanize_timestamp(latest["created_at"])}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption("No technician update recorded yet.")

    with st.expander("RECORD TECH UPDATE", expanded=not latest):
        update_type = st.selectbox("Update type", UPDATE_TYPES, key=f"active_update_type_{job_id}")
        eta_minutes = None
        if update_type == "ETA":
            eta_minutes = st.number_input("ETA minutes", min_value=1, max_value=999, step=1, value=15, key=f"active_eta_{job_id}")
        raw_update = st.text_area("Technician's actual reply (paste exactly)", key=f"active_raw_update_{job_id}")
        if st.button("Save technician update", key=f"active_save_update_{job_id}", width="stretch"):
            if not raw_update.strip():
                st.error("Paste the technician's actual reply first.")
            else:
                storage.record_technician_update(
                    conn, job_id, update_type, raw_update,
                    eta_minutes=int(eta_minutes) if eta_minutes else None,
                )
                st.rerun()

    if latest:
        reply = updates.reply_for_update_type(latest["update_type"], latest.get("eta_minutes"))
        if reply.confirmed:
            prepared_text = reply.reply
        else:
            st.warning(f"HUMAN REVIEW NEEDED — {reply.reason}")
            prepared_text = st.text_input("Company-facing update (typed manually)", key=f"active_manual_update_{job_id}")

        if prepared_text:
            st.code(prepared_text)
            action_cols = st.columns(2)
            with action_cols[0]:
                if st.button("PREPARE COMPANY UPDATE", key=f"active_prepare_update_{job_id}", width="stretch"):
                    try:
                        storage.mark_company_update_prepared(conn, job_id, prepared_text)
                        st.rerun()
                    except InvalidJobActionError as exc:
                        st.error(str(exc))
            with action_cols[1]:
                if st.button("MARK UPDATE SENT MANUALLY", key=f"active_sent_update_{job_id}", width="stretch"):
                    try:
                        storage.mark_company_update_sent_manually(conn, job_id, prepared_text)
                        st.success("Recorded as sent.")
                        st.rerun()
                    except InvalidJobActionError as exc:
                        st.error(str(exc))

    if latest and latest["update_type"] == "Completed":
        # st.page_link() requires a real st.navigation() page-registry
        # context to resolve its target -- it raises KeyError when this
        # workspace is exercised standalone (as every other Active Jobs/
        # Closings/Technicians/Activity test in this suite already does).
        # A plain caption avoids that fragility entirely; the persistent
        # top navigation already puts Closings one tap away.
        st.info("MOVE TO CLOSING — open the Closings tab to continue this job.")

    with st.expander("PASS / CANCEL"):
        reason = st.text_input("Reason (required)", key=f"active_pass_reason_{job_id}")
        if st.button("PASS / CANCEL JOB", key=f"active_pass_{job_id}", disabled=not reason.strip()):
            try:
                storage.pass_or_cancel_job(conn, job_id, "Cancel", reason)
                st.success("Job passed/cancelled.")
                st.rerun()
            except InvalidJobActionError as exc:
                st.error(str(exc))

    with st.expander("View details"):
        st.markdown(f"Phone: {job['fields'].get('phone') or '—'}")
        st.markdown(f"Address: {job['fields'].get('address') or '—'}")
        if job["fields"].get("notes"):
            st.markdown(f"Notes: {job['fields']['notes']}")


def render_closing_workspace(conn, job_id: int) -> None:
    """The real Closings workspace for one selected, already-committed
    job: enter the technician's real closing if none is on record yet,
    otherwise prepare/copy/mark-sent the company-specific closing and
    complete the job. Uses the existing company-specific closing
    formatter (closing.render_closing_from_persisted_fields) -- never a
    second closing template."""
    import streamlit as st

    from dispatcher import storage
    from dispatcher.closing import ClosingInput, render_closing_from_persisted_fields
    from dispatcher.storage import InvalidJobActionError

    job = storage.get_job_by_id(conn, job_id)
    if job is None:
        st.error("This job no longer exists.")
        return
    area, description = _service_area_and_description(job["fields"])

    st.markdown(
        f'<div class="desk-card-top">{source_chip_html(job["source"])}'
        f'<span class="desk-ticket">{job["ticket_number"] or "No reference"}</span>'
        f'<span class="desk-age">{job["status"]}</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="desk-meta">{area} · {description}</div>'
        f'<div class="desk-meta">Technician: <strong>{job["assigned_technician_name"] or "Unassigned"}</strong></div>',
        unsafe_allow_html=True,
    )

    if job["status"] != "Closed":
        st.caption("No closing recorded yet for this job.")
        with st.expander("Enter technician closing", expanded=True):
            raw_closing = st.text_area("Technician's actual closing (paste exactly)", key=f"closing_raw_{job_id}")
            amount = st.text_input("Final amount (verbatim, optional)", key=f"closing_amount_{job_id}")
            payment_method = st.text_input("Payment method (optional)", key=f"closing_payment_{job_id}")
            parts = st.text_input("Parts amount (optional)", key=f"closing_parts_{job_id}")
            company_share = st.text_input("Company share (optional)", key=f"closing_company_share_{job_id}")
            technician_share = st.text_input("Technician share (optional)", key=f"closing_tech_share_{job_id}")
            if st.button("RECORD CLOSING", key=f"closing_record_{job_id}", width="stretch"):
                if not raw_closing.strip():
                    st.error("Paste the technician's actual closing first.")
                else:
                    storage.record_job_closing(
                        conn, job_id, raw_closing,
                        final_amount=amount or None, payment_method=payment_method or None,
                        parts=parts or None, company_share=company_share or None,
                        technician_share=technician_share or None,
                    )
                    st.rerun()
        return

    details = storage.get_latest_closing_details(conn, job_id)
    if details:
        parts_bit = f" · Parts: {details['parts_amount']}" if details.get("parts_amount") else ""
        st.markdown(
            f'<div class="desk-meta">Amount: <strong>{details["amount"] or "—"}</strong> · '
            f'{details["payment_method"] or "Payment method not recorded"}{parts_bit}</div>',
            unsafe_allow_html=True,
        )
        if details.get("company_share"):
            st.caption(f"Company share: {details['company_share']}")
        if details.get("technician_share"):
            st.caption(f"Technician share: {details['technician_share']}")
    else:
        st.caption("No structured closing amount recorded yet for this job.")

    with st.expander("View details"):
        row = conn.execute(
            "SELECT raw_message FROM events WHERE job_id = ? AND message_type = ? "
            "ORDER BY id DESC LIMIT 1",
            (job_id, storage.MessageType.CLOSING.value),
        ).fetchone()
        if row and row[0]:
            st.markdown("**Raw technician closing**")
            st.code(shared_mask(row[0]))

    missing = []
    if not details or not details.get("amount"):
        missing.append("amount")
    if not details or not details.get("payment_method"):
        missing.append("payment method")
    if missing:
        st.warning(f"HUMAN REVIEW NEEDED — missing: {', '.join(missing)}")
        return

    closing_input = ClosingInput(
        amount=details["amount"], payment_method=details["payment_method"],
        parts_amount=details.get("parts_amount"),
    )
    result = render_closing_from_persisted_fields(job["source"], job["ticket_number"], job["fields"], closing_input)
    if not result.supported:
        st.warning(f"HUMAN REVIEW NEEDED — {result.reason}")
        return

    st.code(result.text)
    action_cols = st.columns(2)
    with action_cols[0]:
        if st.button("PREPARE COMPANY CLOSING", key=f"closing_prepare_{job_id}", width="stretch"):
            try:
                storage.mark_company_closing_prepared(conn, job_id, result.text)
                st.rerun()
            except InvalidJobActionError as exc:
                st.error(str(exc))
    with action_cols[1]:
        if st.button("MARK CLOSING SENT MANUALLY", key=f"closing_sent_{job_id}", width="stretch"):
            try:
                storage.mark_company_closing_sent_manually(conn, job_id, result.text)
                st.success("Recorded as sent.")
                st.rerun()
            except InvalidJobActionError as exc:
                st.error(str(exc))

    sent_confirmed = conn.execute(
        "SELECT 1 FROM events WHERE job_id = ? AND message_type = ? AND final_action = 'Company Closing Sent Manually' LIMIT 1",
        (job_id, storage.MessageType.OPERATIONAL_NOTE.value),
    ).fetchone()
    if sent_confirmed:
        if st.button("COMPLETE JOB", key=f"closing_complete_{job_id}", type="primary", width="stretch"):
            try:
                storage.complete_job(conn, job_id)
                st.success("Job completed.")
                st.rerun()
            except InvalidJobActionError as exc:
                st.error(str(exc))
    else:
        st.caption("COMPLETE JOB is available once the company closing has been marked sent.")


def _history_event_label(event: dict) -> str:
    """History-page-only event labels, layered in front of
    luxor_ops_shared.humanize_event_label() for the new event
    shapes (OPERATIONAL_NOTE, dispatcher-recorded STATUS updates,
    dispatcher PASS/CANCEL) -- every existing event type still falls
    through to that same shared function, unchanged."""
    import luxor_ops_shared as shared

    mtype = event.get("message_type")
    action = event.get("final_action")
    if mtype == "OPERATIONAL_NOTE":
        return action or "Operational note"
    if mtype == "STATUS" and event.get("update_type"):
        eta = f" ({event['eta_minutes']} min)" if event.get("eta_minutes") else ""
        return f"Technician update: {event['update_type']}{eta}"
    if mtype == "CANCELLATION" and action in ("Pass", "Cancel"):
        return f"{action} (dispatcher)"
    return shared.humanize_event_label(event)


def render_completed_job_card(conn, job: dict) -> None:
    """One Completed/History card -- final summary plus one optional
    "View details" panel with the full chronological audit history
    (dispatcher.storage.get_audit_timeline_by_job_id via the existing
    luxor_ops_shared.fetch_timeline() masking). No parser internals
    (confidence scores, extraction reasoning) shown here -- only
    workflow-level event labels."""
    import streamlit as st

    import luxor_ops_shared as shared
    from dispatcher import storage

    details = storage.get_latest_closing_details(conn, job["id"])
    st.markdown(
        f'<div class="desk-card-top">{source_chip_html(job["source"])}'
        f'<span class="desk-ticket">{job["ticket_number"] or "No reference"}</span>'
        f'<span class="desk-age">{job["status"]}</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="desk-meta">{job["service_area"]} · {job["description"]}</div>'
        f'<div class="desk-meta">Technician: <strong>{job["assigned_technician_name"] or "Unassigned"}</strong></div>',
        unsafe_allow_html=True,
    )
    if details:
        st.markdown(
            f'<div class="desk-meta">Amount: <strong>{details["amount"] or "—"}</strong> · '
            f'{details["payment_method"] or "Payment method not recorded"}</div>',
            unsafe_allow_html=True,
        )
    st.caption(f"Completed {shared.humanize_timestamp(job.get('last_event_at') or job.get('updated_at'))}")

    with st.expander("View details"):
        timeline = shared.fetch_timeline(conn, job["id"])
        if not timeline:
            st.caption("No audit history recorded for this job.")
        for event in timeline:
            when = shared.humanize_timestamp(event.get("created_at"))
            st.markdown(f"**{when}** — {_history_event_label(event)}")
            if event.get("dispatcher_edits"):
                st.caption(event["dispatcher_edits"])

    st.markdown('<div class="desk-divider"></div>', unsafe_allow_html=True)
