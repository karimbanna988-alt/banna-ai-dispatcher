"""Shadow Review -- read-only display of every Shadow Mode episode
(observation, shadow suggestion, dispatcher's actual action, and any
later outcome), plus the one write action this screen offers: marking
an episode's review status (VERIFIED/CORRECTED/EXCLUDE/SENSITIVE/
INCOMPLETE). That status only ever gates export eligibility -- it never
changes any job, technician, or commercial fact already recorded. See
docs/SHADOW_MODE.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import luxor_desk_shared as shared_desk
import luxor_ops_shared as shared
from dispatcher import storage
from dispatcher.application import DispatcherService
from dispatcher.shadow import ReviewStatus

if not shared_desk.is_authenticated():
    st.stop()

conn = storage.connect()
if not shared_desk.require_current_schema(conn):
    conn.close()
    st.stop()
if not storage.shadow_schema_present(conn):
    st.info(
        "Shadow Mode requires a database upgrade that has not been applied yet. "
        "This page will show episodes once migrated."
    )
    conn.close()
    st.stop()

service = DispatcherService(conn)
episodes = storage.list_shadow_episodes(conn, limit=50)

st.caption(
    "Passive observation only -- nothing here was sent automatically. "
    "Marking VERIFIED/CORRECTED is the only way an episode becomes export-eligible."
)

if not episodes:
    st.info("No shadow episodes recorded yet.")

REVIEW_OPTIONS = [s.value for s in ReviewStatus]

for episode in episodes:
    cu = episode.get("commercial_update") or {}
    with st.expander(
        f"{episode['source']} · {episode.get('ticket_or_pdl') or 'No reference'} · "
        f"{episode['review_status']} · {shared.humanize_timestamp(episode['updated_at'])}"
    ):
        st.markdown(f"**Channel:** {episode['channel']}")
        st.markdown(f"**Job ID:** {episode.get('job_id') or 'Not yet dispatched'}")
        st.markdown(f"**Shadow suggested reply:** {episode.get('shadow_suggested_reply') or '—'}")
        st.markdown(f"**Dispatcher actual reply:** {episode.get('dispatcher_actual_reply') or '—'}")
        if cu:
            st.markdown(
                f"**Commercial fields:** {cu.get('quote_type', '—')} "
                f"{cu.get('quote_amount') or ''}{cu.get('quote_range_low') or ''} "
                f"· customer: {cu.get('customer_response', '—')} · negotiation: {cu.get('negotiation_state', '—')}"
            )
        st.markdown(f"**Negotiation outcome:** {episode.get('negotiation_outcome') or 'Not yet known'}")
        st.markdown(f"**Final job outcome:** {episode.get('final_job_outcome') or 'Not yet known'}")
        if episode.get("human_review_required"):
            st.warning("HUMAN REVIEW NEEDED")

        with st.expander("Full chronological log"):
            for shadow_event in storage.get_shadow_episode_events(conn, episode["episode_id"]):
                st.markdown(
                    f'<div class="desk-meta">{shadow_event["event_type"]} · {shadow_event["channel"]} · '
                    f'{shared.humanize_timestamp(shadow_event["created_at"])}</div>',
                    unsafe_allow_html=True,
                )

        cols = st.columns([2, 1])
        with cols[0]:
            chosen_status = st.selectbox(
                "Review status", REVIEW_OPTIONS,
                index=REVIEW_OPTIONS.index(episode["review_status"]),
                key=f"shadow_review_status_{episode['episode_id']}",
            )
        with cols[1]:
            if st.button("Save", key=f"shadow_review_save_{episode['episode_id']}"):
                result = service.set_shadow_review(episode["episode_id"], chosen_status, reviewed_by=None)
                if result.success:
                    st.rerun()
                else:
                    st.error(result.user_message)

conn.close()
