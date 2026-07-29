"""Shared styling/auth helpers for LUXOR OPERATIONS DESK. Static CSS
only -- this file contains no inline script block, no click-handler
attribute, and no JavaScript of any kind, anywhere, by design (a prior
candidate's custom clipboard button rendered its own JS source as
visible page text; this prototype avoids that entire class of bug by
never emitting JS at all, relying only on st.code()'s native copy
control). Never imports dispatcher.storage, never opens the real
database, never calls Quo.

Dark navy/charcoal visual system (deliberately not Streamlit's default
dark theme, which reads as flat black/gray) -- launched with
--theme.base dark plus explicit --theme.* colors matching the constants
below, then refined here."""

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
