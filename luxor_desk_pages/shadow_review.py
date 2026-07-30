"""Shadow Review -- read-only display of every Shadow Mode episode
(observation, shadow suggestion, dispatcher's actual action, and any
later outcome), plus the one write action this screen offers: marking
an episode's review status (VERIFIED/CORRECTED/EXCLUDE/SENSITIVE/
INCOMPLETE). That status only ever gates export eligibility -- it never
changes any job, technician, or commercial fact already recorded. See
docs/SHADOW_MODE.md.

Each episode renders in the fixed six-point order the blueprint calls
for: what was received, what the system suggested, what the dispatcher
confirmed or corrected, what was actually sent, what happened
afterward, and the review decision. Internal identifiers (episode ID,
confidence score, suggestion reasons) only ever appear inside the
"Technical details" expander.
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
shared_desk.render_header(conn, "Shadow Review")
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

st.caption("Shadow Review compares what the system suggested with what the dispatcher actually did.")
st.caption(
    "Passive observation only -- nothing here was sent automatically. "
    "Marking Verified/Corrected is the only way an episode becomes export-eligible."
)

if not episodes:
    st.info("No dispatcher examples have been recorded yet.")

REVIEW_LABELS = {
    "UNREVIEWED": "Not yet reviewed",
    "VERIFIED": "Verified",
    "CORRECTED": "Corrected",
    "EXCLUDE": "Exclude",
    "SENSITIVE": "Sensitive",
    "INCOMPLETE": "Incomplete",
}
REVIEW_OPTIONS = [s.value for s in ReviewStatus]

for episode in episodes:
    cu = episode.get("commercial_update") or {}
    with st.expander(
        f"{episode['source']} · {episode.get('ticket_or_pdl') or 'No reference'} · "
        f"{REVIEW_LABELS.get(episode['review_status'], episode['review_status'])} · "
        f"{shared.humanize_timestamp(episode['updated_at'])}"
    ):
        st.markdown('<div class="desk-section-title">What was received</div>', unsafe_allow_html=True)
        st.markdown(f"{episode.get('incoming_message_type') or 'Unknown message type'} — {episode.get('normalized_intent') or 'No intent recorded'}")

        st.markdown('<div class="desk-section-title">What the system suggested</div>', unsafe_allow_html=True)
        st.markdown(episode.get("shadow_suggested_reply") or "No suggestion recorded.")

        st.markdown('<div class="desk-section-title">What the dispatcher confirmed or corrected</div>', unsafe_allow_html=True)
        st.markdown(episode.get("dispatcher_actual_reply") or "No dispatcher action recorded yet.")
        if cu:
            st.caption(
                f"Commercial fields: {shared_desk.COMMERCIAL_QUOTE_TYPE_LABELS.get(cu.get('quote_type'), cu.get('quote_type') or '—')} "
                f"{cu.get('quote_amount') or cu.get('quote_range_low') or ''} · "
                f"customer: {shared_desk.COMMERCIAL_CUSTOMER_RESPONSE_LABELS.get(cu.get('customer_response'), cu.get('customer_response') or '—')} · "
                f"negotiation: {shared_desk.COMMERCIAL_NEGOTIATION_LABELS.get(cu.get('negotiation_state'), cu.get('negotiation_state') or '—')}"
            )

        st.markdown('<div class="desk-section-title">What was actually sent</div>', unsafe_allow_html=True)
        if episode.get("actual_send_channel"):
            st.markdown(f"Sent manually in {episode['actual_send_channel']} ({shared.humanize_timestamp(episode.get('actual_send_confirmed_at'))})")
        else:
            st.markdown("Not yet confirmed sent.")

        st.markdown('<div class="desk-section-title">What happened afterward</div>', unsafe_allow_html=True)
        st.markdown(f"Negotiation outcome: {episode.get('negotiation_outcome') or 'Not yet known'}")
        st.markdown(f"Final job outcome: {episode.get('final_job_outcome') or 'Not yet known'}")

        if episode.get("human_review_required"):
            st.warning("Please review this episode before marking it verified.")

        with st.expander("Technical details"):
            st.caption(f"Episode ID: {episode['episode_id']}")
            st.caption(f"Job ID: {episode.get('job_id') or 'Not yet dispatched'}")
            st.caption(f"Channel: {episode['channel']}")
            if episode.get("shadow_confidence") is not None:
                st.caption(f"Confidence: {episode['shadow_confidence']}")
            if episode.get("shadow_reasons"):
                st.caption(f"Suggestion reasons: {', '.join(episode['shadow_reasons'])}")
            st.markdown("**Full chronological log**")
            for shadow_event in storage.get_shadow_episode_events(conn, episode["episode_id"]):
                st.markdown(
                    f'<div class="desk-meta">{shadow_event["event_type"]} · {shadow_event["channel"]} · '
                    f'{shared.humanize_timestamp(shadow_event["created_at"])}</div>',
                    unsafe_allow_html=True,
                )

        st.markdown('<div class="desk-section-title">Review decision</div>', unsafe_allow_html=True)
        cols = st.columns([2, 1])
        with cols[0]:
            chosen_status = st.selectbox(
                "Review status", REVIEW_OPTIONS,
                index=REVIEW_OPTIONS.index(episode["review_status"]),
                format_func=lambda v: REVIEW_LABELS.get(v, v),
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
