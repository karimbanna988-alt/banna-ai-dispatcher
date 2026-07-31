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
    display: flex; justify-content: space-between; align-items: flex-start;
    padding: 0.35rem 0 0.3rem 0; margin-bottom: 0.2rem; gap: 0.5rem; flex-wrap: wrap;
}}
.desk-title {{ font-size: 0.78rem; font-weight: 700; color: {MUTED}; letter-spacing: 0.04em; text-transform: uppercase; white-space: nowrap; }}
.desk-page-title {{ font-size: 1.25rem; font-weight: 700; color: {TEXT}; white-space: nowrap; line-height: 1.25; }}
.desk-status-line {{ font-size: 0.72rem; color: {MUTED}; text-align: right; line-height: 1.3; white-space: nowrap; }}
.desk-live-dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:#34D399; margin-right: 0.3rem; }}
.desk-header-actions [data-testid="stHorizontalBlock"] {{ gap: 0.4rem !important; }}
.desk-header-actions button {{ font-size: 0.72rem !important; padding: 0.35rem 0.6rem !important; min-height: 34px !important; }}
.desk-warning-chip {{
    display: inline-block; padding: 0.06rem 0.5rem; border-radius: 999px;
    font-size: 0.68rem; font-weight: 700; color: #FFFFFF; background: {URGENCY_COLORS['warning']};
}}

.desk-section-title {{
    font-size: 0.7rem; font-weight: 700; color: {MUTED}; text-transform: uppercase;
    letter-spacing: 0.06em; margin: 0.75rem 0 0.35rem 0;
}}

.desk-next-banner {{
    background: rgba(37,99,235,0.14); border: 1px solid #2563EB; border-radius: 10px;
    padding: 0.55rem 0.75rem; margin: 0.3rem 0 0.6rem 0; font-size: 0.88rem; font-weight: 600; color: {TEXT};
}}
.desk-checked-line {{
    color: #34D399; font-size: 0.82rem; margin: 0.15rem 0; font-weight: 600;
}}

/* New Jobs' compact header only -- one short row (LUXOR + connection +
   small Sync + small "..." menu), never a full-width Sync/Log out
   button. Scoped to the desk_compact_header container so no other
   page's render_header() is affected. */
[class*="st-key-desk_compact_header"] {{ margin-bottom: 0.15rem; }}
.desk-compact-header-title {{ font-size: 0.85rem; font-weight: 700; color: {TEXT}; line-height: 2.2; white-space: nowrap; }}
.desk-compact-header-status {{ font-size: 0.68rem; font-weight: 400; color: {MUTED}; margin-left: 0.3rem; }}
[class*="st-key-desk_compact_header"] button {{
    padding: 0.3rem 0.7rem !important; font-size: 0.72rem !important; min-height: 44px !important;
}}
[class*="st-key-desk_compact_header"] [data-testid="stPopoverBody"] button {{ width: 100%; }}

.desk-workflow-strip {{
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem;
    margin: 0.2rem 0 0.7rem 0; font-size: 0.72rem;
}}
.desk-strip-chip {{
    color: {MUTED}; padding: 0.15rem 0.55rem; border-radius: 999px;
    border: 1px solid {BORDER}; white-space: nowrap;
}}
.desk-strip-current {{
    color: {TEXT}; background: rgba(37,99,235,0.2); border-color: #2563EB; font-weight: 700;
}}
.desk-strip-sep {{ color: {MUTED}; }}

/* Tap targets -- every real Streamlit button at least ~44px tall,
   regardless of which page/section renders it. */
.stButton button, .stFormSubmitButton button {{ min-height: 44px !important; }}

/* Mobile: any st.columns()-based action row (K-copy, commercial
   range low/high, active-job action pairs, closing action pairs)
   stacks to one column instead of relying on Streamlit's own
   column-wrap threshold. The desk_layout two-panel CSS grid is
   unaffected (it's a grid, not a stHorizontalBlock) and keeps its own
   900px breakpoint. */
@media (max-width: 480px) {{
    [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap !important; }}
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
        min-width: 100% !important; flex: 1 1 100% !important;
    }}
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


def require_current_schema(conn) -> bool:
    """Guards a write-capable workspace against a not-yet-migrated
    database. Rendering must never trigger a migration (see
    storage.connect()'s own docstring) -- this only *checks*
    (storage.check_schema_status() is read-only) and, if the schema is
    behind, shows one concise message plus the exact local command to
    run and returns False so the caller can skip rendering write
    actions for this workspace. Read-only pages (Technicians, Activity)
    never call this -- they work correctly regardless of schema state."""
    import streamlit as st

    from dispatcher import storage

    status = storage.check_schema_status(conn)
    if status.up_to_date:
        return True
    st.warning(
        "Database upgrade required — this workspace's write actions are disabled until the "
        "schema is migrated.\n\n"
        f"Run: `python scripts/manage_schema.py migrate --db {storage.DB_PATH}`"
    )
    return False


def render_header(conn, title: str, show_sync: bool = False) -> bool:
    """One shared header for every page: fixed app name, current page
    title, Quo connection state, last successful sync time, and a
    logout control -- never process IDs, schema revision, database
    paths, git hashes, or env var names (those belong only inside a
    page's own "Technical details" expander, if it has one). Returns
    True exactly once, on the render where the caller's own "Sync"
    button was clicked -- the actual poll/error-handling logic stays in
    the calling page (dispatch.py), this component only renders the
    trigger so New Jobs is the only page that shows it."""
    import os

    import streamlit as st

    import luxor_ops_shared as shared
    from dispatcher.quo_client import API_KEY_ENV_VAR

    left, right = st.columns([2, 1])
    with left:
        st.markdown(
            f'<div class="desk-title">LUXOR LOCKSMITH</div><div class="desk-page-title">{title}</div>',
            unsafe_allow_html=True,
        )
    sync_clicked = False
    with right:
        api_key_configured = bool(os.environ.get(API_KEY_ENV_VAR))
        _state, label = shared.connection_state(conn, api_key_configured)
        last_sync = shared.fetch_last_refresh(conn)
        st.markdown(
            f'<div class="desk-status-line">{label} · synced {shared.humanize_timestamp(last_sync)}</div>',
            unsafe_allow_html=True,
        )
        with st.container(key="desk_header_actions_wrapper"):
            st.markdown('<div class="desk-header-actions">', unsafe_allow_html=True)
            action_cols = st.columns(2) if show_sync else st.columns(1)
            if show_sync:
                with action_cols[0]:
                    sync_clicked = st.button("Sync", key="desk_header_sync", width="stretch")
            with action_cols[-1]:
                if st.button("Log out", key="desk_header_logout", width="stretch"):
                    st.session_state[AUTH_SESSION_KEY] = False
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div class="desk-divider"></div>', unsafe_allow_html=True)
    return sync_clicked


WORKFLOW_STAGES = ("New", "Active", "Closing", "History")


def render_workflow_strip(conn, current_stage: str) -> None:
    """Compact New -> Active -> Closing -> History strip shown on the
    four operational pages, highlighting the current stage with the
    same attention counts luxor_desk_read_model.get_operational_counts()
    already computes for every other count in the app -- no second
    counting logic. Plain markdown chips, never st.page_link(): that
    call requires a real st.navigation() page-registry context and
    raises KeyError when a workspace/page is exercised standalone (see
    render_active_job_workspace's Closing-readiness note for the same
    constraint) -- the persistent top navigation is still one tap away."""
    import streamlit as st

    import luxor_desk_read_model as rm

    counts = rm.get_operational_counts(conn)
    stage_counts = {
        "New": counts["new_jobs"], "Active": counts["active_jobs"],
        "Closing": counts["pending_closings"], "History": None,
    }
    parts = []
    for i, name in enumerate(WORKFLOW_STAGES):
        if i > 0:
            parts.append('<span class="desk-strip-sep">→</span>')
        count = stage_counts[name]
        count_bit = f" ({count})" if count is not None else ""
        css_class = "desk-strip-chip desk-strip-current" if name == current_stage else "desk-strip-chip"
        parts.append(f'<span class="{css_class}">{name}{count_bit}</span>')
    st.markdown(f'<div class="desk-workflow-strip">{"".join(parts)}</div>', unsafe_allow_html=True)


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


NEW_JOBS_LIVE_WINDOW_SECONDS = 3600  # 60 minutes -- presentation-only cutoff, see docs/operator_ui_blueprint.md


def _age_minutes_clamped(age_seconds: float) -> int:
    """Whole minutes only (never seconds), never negative (a handful of
    test fixtures deliberately seed slightly-future received_at
    timestamps for clock-skew tolerance -- this must never surface as
    "-5m")."""
    return max(0, int(age_seconds // 60))


def render_compact_new_jobs_header(conn) -> bool:
    """The one-row compact header for New Jobs only: LUXOR + connection
    dot + a small Sync button + a small "..." menu (st.popover, not a
    second full-width button) containing Log out. Every other page keeps
    render_header() unchanged -- this is deliberately New-Jobs-specific,
    not a shared redesign. Returns True exactly once, on the render
    where Sync was clicked (same contract as render_header())."""
    import os

    import streamlit as st

    import luxor_ops_shared as shared
    from dispatcher.quo_client import API_KEY_ENV_VAR

    with st.container(key="desk_compact_header"):
        left, right = st.columns([5, 3])
        with left:
            api_key_configured = bool(os.environ.get(API_KEY_ENV_VAR))
            _state, label = shared.connection_state(conn, api_key_configured)
            st.markdown(
                f'<div class="desk-compact-header-title">LUXOR <span class="desk-compact-header-status">{label}</span></div>',
                unsafe_allow_html=True,
            )
        sync_clicked = False
        with right:
            sync_col, menu_col = st.columns(2)
            with sync_col:
                sync_clicked = st.button("Sync", key="desk_compact_sync")
            with menu_col:
                with st.popover("⋮"):
                    if st.button("Log out", key="desk_compact_logout", width="stretch"):
                        st.session_state[AUTH_SESSION_KEY] = False
                        st.rerun()
    return sync_clicked


def render_queue_row(entry, identity: str, selected_id: str) -> None:
    """One compact, real, clickable queue row -- every field comes
    straight from the QueueEntry the caller built via
    luxor_desk_read_model.get_live_dispatch_queue(). One line: source,
    ticket, city, service, age in whole minutes -- never seconds, never
    a full sentence. Clicking selects this job as the workspace target;
    nothing else about it mutates."""
    import streamlit as st

    job = entry.job
    city = job.city.value or job.state.value or "Area unknown"
    age_minutes = _age_minutes_clamped(entry.age_seconds)
    extra = f" (+{entry.extra_count})" if entry.extra_count else ""
    marker = "🔴 " if entry.urgency == "urgent" else ""
    label = (
        f"{marker}{job.source.value} • {job.ticket_number.value or 'No ref'}{extra} • "
        f"{city} • {job.service_type.value or 'Service unclear'} • {age_minutes}m"
    )
    button_type = "primary" if identity == selected_id else "secondary"
    if st.button(label, key=f"desk_queue_row_{identity}", width="stretch", type=button_type):
        st.session_state.desk_dispatch_selected = identity
        st.rerun()


def render_job_workspace(conn, entry) -> None:
    """The New Jobs action screen for one selected QueueEntry: a
    compact always-visible summary, then at most three action areas
    (company acknowledgment, technician dispatch, pass/handle manually
    collapsed), then one collapsed "Job details" panel for everything
    else. Every write here calls the exact same
    storage.save_audit_entry()/mark_quo_pending_message_actioned()/
    dispatch_tracking path the original Speed Dispatch Console uses --
    no second persistence layer. See docs/operator_ui_blueprint.md's
    "New Jobs mobile action screen" section for the exact state
    machine each action area implements."""
    import streamlit as st

    import luxor_desk_read_model as rm
    from dispatcher import outbound
    from dispatcher.application import DispatcherService
    from dispatcher.policy import get_policy

    job, classify_result, result, tracking = entry.job, entry.classify_result, entry.result, entry.tracking
    item = entry.item
    mid = item["message_id"]
    service = DispatcherService(conn)

    resolution, eligible, market_label = rm.resolve_job_routing(conn, job, item["raw_text"])
    area_label = resolution.canonical_area or "Area unresolved"
    age_minutes = _age_minutes_clamped(entry.age_seconds)

    # ---- Always-visible compact summary --------------------------------
    st.markdown(
        f'<div class="desk-card-top">'
        f'{source_chip_html(job.source.value)}'
        f'<span class="desk-ticket">{job.ticket_number.value or "No reference"}</span>'
        f'<span class="desk-age">{age_minutes}m</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    meta_line = f"{area_label} · {job.service_type.value or 'Service unclear'}"
    if market_label:
        meta_line += f" ({market_label})"
    st.markdown(f'<div class="desk-meta">{meta_line}</div>', unsafe_allow_html=True)

    if not require_current_schema(conn):
        with st.expander("Job details"):
            _render_job_details(conn, item, job, area_label, market_label, eligible, resolution, result)
        return

    needs_review = bool(result.decision.missing_information) or bool(classify_result.conflicting_fields)
    destination = eligible[0]["dispatch_group"] if eligible else None
    if len(eligible) == 1:
        tech_line = f"Technician: <strong>{eligible[0]['name']}</strong>"
    elif eligible:
        tech_line = f"{len(eligible)} technicians eligible"
    else:
        tech_line = "No technician configured"
    dest_line = (
        f"WhatsApp: <strong>{destination}</strong>" if destination
        else '<span class="desk-warning-chip">WhatsApp group not configured.</span>'
    )
    st.markdown(f'<div class="desk-meta">{tech_line} · {dest_line}</div>', unsafe_allow_html=True)

    if needs_review:
        problems = list(result.decision.missing_information) + [f"conflicting {f}" for f in classify_result.conflicting_fields]
        st.warning(f"Review before dispatching — {', '.join(problems)}.")
        with st.expander("Job details"):
            _render_job_details(conn, item, job, area_label, market_label, eligible, resolution, result)
        with st.expander("Pass or handle manually"):
            _render_secondary_actions(conn, mid, job, classify_result, result)
        return

    stream_key = rm.policy_stream_key(item["group_label"], job.source.value)
    policy = get_policy(stream_key, classify_result.message_type.value)
    is_closings_stream = item["group_label"] == "CLOSINGS"

    # ---- Action area 1: Company acknowledgment (AGN/Lucky K only) ------
    k_blocking = False
    if is_closings_stream:
        st.caption("Closings & Replies — use the Closings tool for this one.")
    elif policy.acknowledgment_required:
        k_blocking = not tracking["acknowledged_at"]
        mode = outbound.current_mode()
        if mode in (outbound.OutboundMode.APPROVAL, outbound.OutboundMode.LIVE):
            if not tracking["acknowledged_at"]:
                if st.button("Send K", key=f"desk_k_{mid}", type="primary", width="stretch"):
                    service.attempt_quo_acknowledgment(
                        mid, source=job.source.value, job_id=classify_result.existing_job_id,
                        quo_contact_id=item["group_label"], text="K",
                    )
                    st.rerun()
            else:
                st.markdown('<div class="desk-checked-line">✓ K sent</div>', unsafe_allow_html=True)
        elif not tracking["acknowledged_at"]:
            # One stateful button, two real taps: "Copy K" only reveals
            # the code (copying is never sending); "Confirm K sent in
            # Quo" is the one tap that actually records acknowledgment.
            # Never shown simultaneously.
            if st.session_state.get(f"njk_k_copy_{mid}"):
                st.code("K")
                if st.button("Confirm K sent in Quo", key=f"desk_k_confirm_{mid}", type="primary", width="stretch"):
                    service.acknowledge_job_manually(mid)
                    st.rerun()
            else:
                if st.button("Copy K", key=f"desk_k_{mid}", type="primary", width="stretch"):
                    st.session_state[f"njk_k_copy_{mid}"] = True
                    st.rerun()
        else:
            st.markdown('<div class="desk-checked-line">✓ K sent</div>', unsafe_allow_html=True)
    # Main Leads: no K text, no K placeholder, nothing rendered here.

    # ---- Action area 2: Technician dispatch -----------------------------
    dispatched = tracking["dispatched_manually_at"] is not None
    if not is_closings_stream and not k_blocking:
        if dispatched:
            st.markdown('<div class="desk-checked-line">✓ Dispatched</div>', unsafe_allow_html=True)
        elif not destination:
            # No defined destination -- never offer a normal dispatch
            # confirmation through an undefined channel. Copy-only.
            if st.button("Copy job", key=f"desk_wa_copy_only_{mid}", width="stretch"):
                st.session_state[f"njk_wa_preview_{mid}"] = True
            if st.session_state.get(f"njk_wa_preview_{mid}"):
                st.code(result.luxor_message)
        elif not tracking["whatsapp_opened_at"]:
            # One stateful button, two real taps -- same pattern as K.
            if st.button("Copy job for technician", key=f"desk_wa_{mid}", type="primary", width="stretch"):
                service.prepare_technician_dispatch(mid)
                st.rerun()
        else:
            st.code(result.luxor_message)
            if st.button("Confirm sent in WhatsApp", key=f"desk_confirm_dispatch_{mid}", type="primary", width="stretch"):
                service.confirm_whatsapp_dispatch(mid, job, classify_result, result.luxor_message)
                st.rerun()

    # ---- Action area 3: Pass or handle manually (collapsed) -------------
    with st.expander("Pass or handle manually"):
        _render_secondary_actions(conn, mid, job, classify_result, result)

    with st.expander("Job details"):
        _render_job_details(conn, item, job, area_label, market_label, eligible, resolution, result)


def _render_job_details(conn, item, job, area_label, market_label, eligible, resolution, result=None) -> None:
    """Everything that supports the dispatcher's decision but isn't the
    decision itself: contact info, the original message, the prepared
    technician message, and routing internals nested one level further
    under "Technical details" -- all one collapsed panel, opt-in, never
    shown before the selected job's compact summary."""
    import streamlit as st

    st.markdown("**Contact**")
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

    st.markdown("**Original message**")
    st.code(shared_mask(item["raw_text"]))

    if result is not None:
        st.markdown("**Prepared technician message**")
        st.code(result.luxor_message)

    with st.expander("Technical details"):
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

    from dispatcher.application import DispatcherService

    service = DispatcherService(conn)
    if st.button("Handle Manually", key=f"desk_hm_{mid}"):
        service.reject_pending_message(mid, job, classify_result, result.luxor_message)
        st.rerun()
    reason = st.text_input("Reason (required to pass/cancel)", key=f"desk_pass_reason_{mid}")
    if st.button("Pass / Cancel Job", key=f"desk_reject_{mid}", disabled=not reason.strip()):
        service.reject_pending_message(mid, job, classify_result, result.luxor_message, reason=reason.strip())
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

# Plain-English display labels for Commercial Update's internal enum
# values (dispatcher.commercial.QuoteType/CustomerResponseType) --
# translation only, via st.selectbox(format_func=...); the underlying
# option values passed to record_commercial_update() are always the
# real enum .value strings, unchanged.
COMMERCIAL_QUOTE_TYPE_LABELS = {
    "FIXED": "Fixed price",
    "STARTING": "Starting price",
    "RANGE": "Range",
    "ESTIMATE": "Estimate",
    "NO_AMOUNT": "No price given",
    "UNKNOWN": "Unclear",
}
COMMERCIAL_CUSTOMER_RESPONSE_LABELS = {
    "ACCEPTED": "Accepted",
    "DECLINED": "Declined",
    "WILL_CALL_BACK": "Will call back",
    "THINKING": "Thinking",
    "TOO_EXPENSIVE": "Price too high",
    "SHOPPING_AROUND": "Shopping around",
    "REQUESTED_DISCOUNT": "Requested discount",
    "NO_RESPONSE": "No response",
    "UNKNOWN": "Unclear",
}
COMMERCIAL_NEGOTIATION_LABELS = {
    "QUOTE_GIVEN": "Quote given",
    "WAITING_FOR_CUSTOMER": "Waiting for customer",
    "NEGOTIATING": "Negotiating",
    "ACCEPTED": "Accepted",
    "LOST_ON_PRICE": "Lost on price",
    "FOLLOW_UP_REQUIRED": "Follow-up required",
    "UNKNOWN": "Human review needed",
}


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


def render_next_job_button(identities: list, selected_id, session_key: str, button_key: str) -> None:
    """One manual "Next job" tap that advances the current view's
    selection to the next entry in `identities` (wrapping around) --
    never an automatic jump. Completing an action still only changes
    what's selected the next time the dispatcher acts (see the mobile
    addendum's "never automatically perform the next business action")."""
    import streamlit as st

    if len(identities) < 2:
        return
    if st.button("Next job →", key=button_key, width="stretch"):
        try:
            idx = identities.index(selected_id)
        except ValueError:
            idx = -1
        st.session_state[session_key] = identities[(idx + 1) % len(identities)]
        st.rerun()


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


def active_job_bucket(job: dict) -> str:
    """"needs_action" (no update yet, an ambiguous update, or ready to
    close) vs. "waiting" (a normal update is recorded and the dispatcher
    has nothing left to do until the technician's next contact) --
    derived only from active_job_next_action_label(), the same label
    already shown on every Active Jobs row."""
    return "waiting" if active_job_next_action_label(job) == "In progress" else "needs_action"


def closing_job_bucket(job: dict) -> str:
    """"needs_action" (a closing is already recorded -- send/complete
    it) vs. "waiting" (dispatched and In Progress, waiting on the
    technician's real closing) -- maps directly to
    luxor_desk_read_model.get_closing_candidates()'s own two branches,
    no new classification invented."""
    return "needs_action" if job["status"] == "Closed" else "waiting"


def _reply_already_sent_since(conn, job_id: int, since_iso: Optional[str]) -> bool:
    """Whether a Company-Update-Sent-Manually note exists for this job
    at or after `since_iso` -- reuses the exact event shape the Company
    response section's own "I sent this response manually" button
    already writes, so this never invents a second notion of "sent"."""
    from dispatcher import storage
    from dispatcher.action_names import ActionName

    row = conn.execute(
        "SELECT 1 FROM events WHERE job_id = ? AND message_type = ? AND final_action = ? "
        "AND (? IS NULL OR created_at >= ?) LIMIT 1",
        (job_id, storage.MessageType.OPERATIONAL_NOTE.value, ActionName.COMPANY_UPDATE_SENT_MANUALLY.value, since_iso, since_iso),
    ).fetchone()
    return row is not None


def active_job_next_step_sentence(conn, job_id: int, latest: Optional[dict]) -> str:
    """The one `Next: ...` sentence shown at the top of the Active Job
    workspace -- derived only from data already computed today
    (latest_technician_update + whether its reply has already been
    marked sent). Deliberately does not branch on the Commercial Update
    section's own state, to avoid a second, parallel next-action
    computation that could drift from that section's own logic (see the
    blueprint addendum)."""
    if latest is None:
        return "Next: Paste the technician's WhatsApp update."
    if latest["update_type"] == "Custom":
        return "Next: Review the technician's update before continuing."
    if latest["update_type"] == "Completed":
        return "Next: Record the technician's closing."
    if _reply_already_sent_since(conn, job_id, latest.get("created_at")):
        return "Next: Wait for the technician's next update."
    return "Next: Copy the prepared response and send it manually."


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
    from dispatcher import commercial, storage, updates
    from dispatcher.app_models import TechnicianUpdateCommand
    from dispatcher.application import DispatcherService

    service = DispatcherService(conn)
    job = service.get_job_workspace(job_id)
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
    st.markdown(f'<div class="desk-next-banner">{active_job_next_step_sentence(conn, job_id, latest)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="desk-meta">{area} · {description} · Elapsed: {shared.humanize_timestamp(job.get("created_at"))}</div>'
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

    if not require_current_schema(conn):
        return

    # ---- 2. Technician update -----------------------------------------
    st.markdown('<div class="desk-section-title">Technician update</div>', unsafe_allow_html=True)
    if latest:
        eta_note = f" ({latest['eta_minutes']} min)" if latest.get("eta_minutes") else ""
        st.markdown(f'<div class="desk-checked-line">✓ Technician update saved: {latest["update_type"]}{eta_note}</div>', unsafe_allow_html=True)
    else:
        st.caption("No technician update recorded yet.")
    with st.expander("Paste technician update", expanded=not latest):
        update_type = st.selectbox("Update type", UPDATE_TYPES, key=f"active_update_type_{job_id}")
        eta_minutes = None
        if update_type == "ETA":
            eta_minutes = st.number_input("ETA minutes", min_value=1, max_value=999, step=1, value=15, key=f"active_eta_{job_id}")
        raw_update = st.text_area("Technician's actual reply (paste exactly)", key=f"active_raw_update_{job_id}")
        if st.button("Save technician update", key=f"active_save_update_{job_id}", width="stretch"):
            if not raw_update.strip():
                st.error("Paste the technician's actual reply first.")
            else:
                command = TechnicianUpdateCommand(
                    job_id=job_id, update_type=update_type, raw_update=raw_update,
                    eta_minutes=int(eta_minutes) if eta_minutes else None,
                )
                service.record_technician_update(command)
                st.rerun()

    # ---- 3. Commercial update -------------------------------------------
    st.markdown('<div class="desk-section-title">Commercial update</div>', unsafe_allow_html=True)
    if not storage.shadow_schema_present(conn):
        st.caption(
            "This section requires a database upgrade that has not been applied yet -- "
            "it will appear once migrated. The rest of this workspace is unaffected."
        )
        commercial_episode = None
    else:
        commercial_episode = storage.get_shadow_episode_for_job(conn, job_id)
        has_saved_commercial_update = bool(commercial_episode and commercial_episode.get("commercial_update"))

        with st.expander("Paste technician price message", expanded=not has_saved_commercial_update):
            st.caption(
                "Observes and prepares only -- nothing here sends automatically. "
                "Paste the technician's exact price message; correct any field before saving."
            )
            dispatcher_actor = st.text_input(
                "Dispatcher (name/initials — used for internal records only)",
                value=st.session_state.get("desk_dispatcher_actor", ""), key=f"commercial_actor_{job_id}",
            ) or None
            if dispatcher_actor:
                st.session_state["desk_dispatcher_actor"] = dispatcher_actor

            raw_commercial_text = st.text_area(
                "Technician's exact WhatsApp price message (paste exactly)", key=f"commercial_raw_{job_id}",
            )
            preview = commercial.extract_commercial_update(raw_commercial_text) if raw_commercial_text.strip() else None

            if preview:
                quote_type_options = [t.value for t in commercial.QuoteType]
                chosen_quote_type = st.selectbox(
                    "Price type", quote_type_options, index=quote_type_options.index(preview.quote_type.value),
                    format_func=lambda v: COMMERCIAL_QUOTE_TYPE_LABELS.get(v, v),
                    key=f"commercial_quote_type_{job_id}",
                )
                amount, range_low, range_high = None, None, None
                if chosen_quote_type == commercial.QuoteType.RANGE.value:
                    rcol1, rcol2 = st.columns(2)
                    with rcol1:
                        range_low = st.text_input("Range low", value=preview.quote_range_low or "", key=f"commercial_low_{job_id}")
                    with rcol2:
                        range_high = st.text_input("Range high", value=preview.quote_range_high or "", key=f"commercial_high_{job_id}")
                elif chosen_quote_type in (commercial.QuoteType.FIXED.value, commercial.QuoteType.STARTING.value, commercial.QuoteType.ESTIMATE.value):
                    amount = st.text_input("Price or range", value=preview.quote_amount or "", key=f"commercial_amount_{job_id}")

                response_options = [r.value for r in commercial.CustomerResponseType]
                chosen_response = st.selectbox(
                    "Customer response", response_options, index=response_options.index(preview.customer_response.value),
                    format_func=lambda v: COMMERCIAL_CUSTOMER_RESPONSE_LABELS.get(v, v),
                    key=f"commercial_response_{job_id}",
                )

                if preview.human_review_required:
                    st.warning("Please review this update before saving.")
                    st.caption("The wording didn't clearly match a known price phrasing — check the fields above.")

                with st.expander("Technical details"):
                    st.caption(f"Confidence: {preview.confidence:.2f}")
                    if preview.validation_issues:
                        st.caption("Issues: " + "; ".join(preview.validation_issues))

                if st.button("Save verified commercial update", key=f"commercial_save_{job_id}", width="stretch"):
                    corrected_fields = {
                        "quote_type": commercial.QuoteType(chosen_quote_type),
                        "quote_amount": amount or None,
                        "quote_range_low": range_low or None,
                        "quote_range_high": range_high or None,
                        "customer_response": commercial.CustomerResponseType(chosen_response),
                    }
                    result = service.record_commercial_update(
                        job_id, raw_commercial_text, corrected_fields=corrected_fields, dispatcher_actor=dispatcher_actor,
                    )
                    if result.success:
                        st.rerun()
                    else:
                        st.error(result.user_message or "Could not save.")

        if has_saved_commercial_update:
            cu = commercial_episode["commercial_update"]
            status_label = COMMERCIAL_NEGOTIATION_LABELS.get(cu.get("negotiation_state"), "Human review needed")
            st.markdown(f'<div class="desk-checked-line">✓ Commercial update saved · {status_label}</div>', unsafe_allow_html=True)

            reply_text = commercial_episode.get("dispatcher_actual_reply")
            if reply_text:
                if commercial_episode.get("actual_send_confirmed_at"):
                    st.markdown(f'<div class="desk-checked-line">✓ Sent manually · {shared.humanize_timestamp(commercial_episode["actual_send_confirmed_at"])}</div>', unsafe_allow_html=True)
                else:
                    st.caption("Copy company response")
                    st.code(reply_text)
                    st.caption("Send manually in Quo — this business never uses WhatsApp for company replies.")
                    if st.button("I sent this response manually", key=f"commercial_sent_{job_id}", width="stretch"):
                        result = service.confirm_commercial_reply_sent(job_id, "Quo", dispatcher_actor=st.session_state.get("desk_dispatcher_actor"))
                        if result.success:
                            st.success(result.user_message)
                            st.rerun()
                        else:
                            st.error(result.user_message)
            elif cu.get("human_review_required"):
                st.warning("Please review this update before saving — no company response prepared yet.")

    # ---- 4. Company response (technician-status-driven reply) ----------
    if latest:
        st.markdown('<div class="desk-section-title">Company response</div>', unsafe_allow_html=True)
        reply_already_sent = _reply_already_sent_since(conn, job_id, latest.get("created_at"))
        if reply_already_sent:
            st.markdown('<div class="desk-checked-line">✓ Company response marked as sent</div>', unsafe_allow_html=True)
            reply = updates.reply_for_update_type(latest["update_type"], latest.get("eta_minutes"))
            prepared_text = reply.reply if reply.confirmed else None
        else:
            reply = updates.reply_for_update_type(latest["update_type"], latest.get("eta_minutes"))
            if reply.confirmed:
                prepared_text = reply.reply
            else:
                st.warning("Please review this update before saving.")
                st.caption(reply.reason)
                prepared_text = st.text_input("Company-facing update (typed manually)", key=f"active_manual_update_{job_id}")

        if not reply_already_sent and prepared_text:
            st.caption("Copy company response")
            st.code(prepared_text)
            action_cols = st.columns(2)
            with action_cols[0]:
                if st.button("Mark as prepared", key=f"active_prepare_update_{job_id}", width="stretch"):
                    manual_text = prepared_text if not reply.confirmed else None
                    result = service.prepare_company_update(job_id, latest["update_type"], latest.get("eta_minutes"), manual_text=manual_text)
                    if result.success:
                        st.rerun()
                    else:
                        st.error(result.user_message)
            with action_cols[1]:
                if st.button("I sent this response manually", key=f"active_sent_update_{job_id}", width="stretch"):
                    result = service.confirm_company_update_sent(job_id, prepared_text)
                    if result.success:
                        st.success(result.user_message)
                        st.rerun()
                    else:
                        st.error(result.user_message)

    # ---- 5. Pass / Cancel -------------------------------------------------
    with st.expander("Pass / Cancel"):
        reason = st.text_input("Reason (required)", key=f"active_pass_reason_{job_id}")
        if st.button("Pass or cancel job", key=f"active_pass_{job_id}", disabled=not reason.strip()):
            result = service.pass_or_cancel_job(job_id, "Cancel", reason)
            if result.success:
                st.success(result.user_message)
                st.rerun()
            else:
                st.error(result.user_message)

    # ---- 6. Closing readiness --------------------------------------------
    st.markdown('<div class="desk-section-title">Closing readiness</div>', unsafe_allow_html=True)
    if latest and latest["update_type"] == "Completed":
        # st.page_link() requires a real st.navigation() page-registry
        # context to resolve its target -- it raises KeyError when this
        # workspace is exercised standalone (as every other Active Jobs/
        # Closings/Technicians/Activity test in this suite already does).
        # A plain info box avoids that fragility entirely; the persistent
        # top navigation already puts Closings one tap away.
        st.info("Ready to close — open the Closings tab to continue this job.")
    else:
        st.caption("This job becomes ready to close once the technician reports the work is completed.")

    # ---- 7. Job timeline --------------------------------------------------
    st.markdown('<div class="desk-section-title">Job timeline</div>', unsafe_allow_html=True)
    timeline = shared.fetch_timeline(conn, job_id)
    if not timeline:
        st.caption("No activity recorded yet.")
    for event in timeline:
        when = shared.humanize_timestamp(event.get("created_at"))
        st.markdown(f"**{when}** — {history_event_label(event)}")

    with st.expander("Technical details"):
        st.markdown(f"Phone: {job['fields'].get('phone') or '—'}")
        st.markdown(f"Address: {job['fields'].get('address') or '—'}")
        if job["fields"].get("notes"):
            st.markdown(f"Notes: {job['fields']['notes']}")
        if commercial_episode:
            st.caption(f"Shadow episode ID: {commercial_episode['episode_id']}")


def render_closing_workspace(conn, job_id: int) -> None:
    """The real Closings workspace for one selected, already-committed
    job: enter the technician's real closing if none is on record yet,
    otherwise prepare/copy/mark-sent the company-specific closing and
    complete the job. Uses the existing company-specific closing
    formatter (closing.render_closing_from_persisted_fields) -- never a
    second closing template."""
    import streamlit as st

    from dispatcher import storage
    from dispatcher.action_names import ActionName
    from dispatcher.app_models import ClosingCommand
    from dispatcher.application import DispatcherService
    from dispatcher.closing import ClosingInput, render_closing_from_persisted_fields

    service = DispatcherService(conn)
    job = service.get_job_workspace(job_id)
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

    if not require_current_schema(conn):
        return

    quote = storage.get_latest_verified_commercial_update_for_job(conn, job_id) if storage.shadow_schema_present(conn) else None
    quote_amount = quote.get("quote_amount_text") or quote.get("quote_amount") if quote else None
    if quote_amount:
        st.caption(f"Quote given earlier: {quote_amount}")

    if job["status"] != "Closed":
        st.markdown('<div class="desk-section-title">Review technician closing</div>', unsafe_allow_html=True)
        st.caption("No closing recorded yet for this job.")
        with st.expander("Enter technician closing", expanded=True):
            raw_closing = st.text_area("Technician's actual closing (paste exactly)", key=f"closing_raw_{job_id}")
            amount = st.text_input("Final amount (verbatim, optional)", key=f"closing_amount_{job_id}")
            payment_method = st.text_input("Payment method (optional)", key=f"closing_payment_{job_id}")
            parts = st.text_input("Parts amount (optional)", key=f"closing_parts_{job_id}")
            company_share = st.text_input("Company share (optional)", key=f"closing_company_share_{job_id}")
            technician_share = st.text_input("Technician share (optional)", key=f"closing_tech_share_{job_id}")
            if st.button("Save closing details", key=f"closing_record_{job_id}", width="stretch"):
                command = ClosingCommand(
                    job_id=job_id, raw_closing=raw_closing,
                    final_amount=amount or None, payment_method=payment_method or None,
                    parts=parts or None, company_share=company_share or None,
                    technician_share=technician_share or None,
                )
                result = service.record_closing(command)
                if result.success:
                    st.rerun()
                else:
                    st.error(result.user_message or "Paste the technician's actual closing first.")
        return

    st.markdown('<div class="desk-section-title">Review technician closing</div>', unsafe_allow_html=True)
    details = storage.get_latest_closing_details(conn, job_id)

    st.markdown('<div class="desk-section-title">Verify final amount</div>', unsafe_allow_html=True)
    if details and details.get("amount"):
        st.markdown(f'<div class="desk-checked-line">✓ Final amount: {details["amount"]}</div>', unsafe_allow_html=True)
    else:
        st.caption("No final amount recorded yet.")

    st.markdown('<div class="desk-section-title">Verify payment method</div>', unsafe_allow_html=True)
    if details and details.get("payment_method"):
        st.markdown(f'<div class="desk-checked-line">✓ Payment method: {details["payment_method"]}</div>', unsafe_allow_html=True)
    else:
        st.caption("No payment method recorded yet.")

    if details:
        parts_bit = f" · Parts: {details['parts_amount']}" if details.get("parts_amount") else ""
        if parts_bit:
            st.caption(parts_bit.lstrip(" ·"))
        if details.get("company_share"):
            st.caption(f"Company share: {details['company_share']}")
        if details.get("technician_share"):
            st.caption(f"Technician share: {details['technician_share']}")

    with st.expander("Technical details"):
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
        st.warning("Please review this closing before sending.")
        st.caption(f"Missing: {', '.join(missing)}.")
        return

    closing_input = ClosingInput(
        amount=details["amount"], payment_method=details["payment_method"],
        parts_amount=details.get("parts_amount"),
    )
    result = render_closing_from_persisted_fields(job["source"], job["ticket_number"], job["fields"], closing_input)
    if not result.supported:
        st.warning("Please review this closing before sending.")
        st.caption(result.reason)
        return

    sent_confirmed = conn.execute(
        "SELECT 1 FROM events WHERE job_id = ? AND message_type = ? AND final_action = ? LIMIT 1",
        (job_id, storage.MessageType.OPERATIONAL_NOTE.value, ActionName.COMPANY_CLOSING_SENT_MANUALLY.value),
    ).fetchone()

    st.markdown('<div class="desk-section-title">Copy company closing</div>', unsafe_allow_html=True)
    if sent_confirmed:
        st.markdown('<div class="desk-checked-line">✓ Sent manually in Quo</div>', unsafe_allow_html=True)
    else:
        st.code(result.text)
        st.markdown('<div class="desk-section-title">Send manually</div>', unsafe_allow_html=True)
        st.caption("Send manually in Quo — this business never uses WhatsApp for company closings.")
        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button("Mark as prepared", key=f"closing_prepare_{job_id}", width="stretch"):
                prep_result = service.prepare_company_closing(job_id)
                if prep_result.success:
                    st.rerun()
                else:
                    st.error(prep_result.user_message)
        with action_cols[1]:
            if st.button("I sent this closing manually", key=f"closing_sent_{job_id}", width="stretch"):
                sent_result = service.confirm_company_closing_sent(job_id, result.text)
                if sent_result.success:
                    st.success(sent_result.user_message)
                    st.rerun()
                else:
                    st.error(sent_result.user_message)

    st.markdown('<div class="desk-section-title">Complete job</div>', unsafe_allow_html=True)
    if sent_confirmed:
        if st.button("Complete job", key=f"closing_complete_{job_id}", type="primary", width="stretch"):
            complete_result = service.complete_job(job_id)
            if complete_result.success:
                st.success(complete_result.user_message)
                st.rerun()
            else:
                st.error(complete_result.user_message)
    else:
        st.caption("Complete job is available once the company closing has been marked sent.")


def history_event_label(event: dict) -> str:
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

    with st.expander("Job details"):
        timeline = shared.fetch_timeline(conn, job["id"])
        if not timeline:
            st.caption("No audit history recorded for this job.")
        for event in timeline:
            when = shared.humanize_timestamp(event.get("created_at"))
            st.markdown(f"**{when}** — {history_event_label(event)}")
            if event.get("dispatcher_edits"):
                st.caption(event["dispatcher_edits"])

    st.markdown('<div class="desk-divider"></div>', unsafe_allow_html=True)
