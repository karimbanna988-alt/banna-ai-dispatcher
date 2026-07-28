"""LUXOR LOCKSMITH -- Operator Console.

Fast, mobile-first job handling for the dispatcher. Unlike luxor_ops.py
(the read-only Management CRM), this is a real, write-capable tool: its
Acknowledge/Handle Manually/Reject actions call the exact same
storage.save_audit_entry() + storage.mark_quo_pending_message_actioned()
app.py's own Approve/Edit/Reject/Handle Manually buttons already use, in
the same order app.py's _finalize() already does. No second persistence
layer, no second decision engine.

Acknowledgment text shown here is a PREVIEW only, sourced from
dispatcher.policy.get_policy() -- this screen never sends anything to
Quo. Real outbound stays behind the separate, disabled-by-default
luxor_outbound.py module (Phase 4).

The job-transformation gate (luxor_ops_shared.shows_job_transformation)
still applies: a message with no ticket and no correlation to an
existing job never reaches the Luxor formatter and is never actionable
here beyond viewing -- it only ever appears in Other Messages.
"""

from __future__ import annotations

import hmac
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
load_dotenv(Path(__file__).resolve().parent / ".env")

import luxor_ops_shared as shared
from dispatcher import quo_client, storage
from dispatcher.policy import LUCKY_CLOSINGS_STREAM_KEY, get_policy
from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError
from dispatcher.quo_ingest import poll_for_new_messages
from dispatcher.storage import ClassifyResult, MessageType

AUTH_SESSION_KEY = "luxor_console_authenticated"

st.set_page_config(page_title="LUXOR LOCKSMITH -- Operator Console", layout="centered")
st.markdown(shared.MOBILE_CSS, unsafe_allow_html=True)


@st.cache_resource
def get_db() -> sqlite3.Connection:
    return storage.connect()


# ------------------------------------------------------------------ Auth --
_password = os.environ.get(shared.PASSWORD_ENV_VAR)
if not _password:
    st.error(f"This application is not configured ({shared.PASSWORD_ENV_VAR} is not set). Access refused.")
    st.stop()

if AUTH_SESSION_KEY not in st.session_state:
    st.session_state[AUTH_SESSION_KEY] = False

if not st.session_state[AUTH_SESSION_KEY]:
    st.title("LUXOR LOCKSMITH")
    st.caption("Operator Console")
    entered = st.text_input("Password", type="password", key="luxor_console_password_input")
    if st.button("Enter", type="primary"):
        if hmac.compare_digest(entered, _password):
            st.session_state[AUTH_SESSION_KEY] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

conn = get_db()

# ---------------------------------------------------------------- Header --
_connected = bool(os.environ.get(API_KEY_ENV_VAR))
header_col, status_col = st.columns([3, 2])
with header_col:
    st.markdown("### LUXOR LOCKSMITH")
    st.caption("Operator Console")
with status_col:
    label = "🟢 Connected" if _connected else "🔴 Not configured"
    st.caption(label)
last_refresh = shared.fetch_last_refresh(conn)
st.caption(f"Last synced: {shared.humanize_timestamp(last_refresh) if last_refresh else 'Never'}")
st.divider()

# -------------------------------------------------------------- Refresh --
if "luxor_console_refresh_message" not in st.session_state:
    st.session_state.luxor_console_refresh_message = None

if st.button("Refresh", type="secondary", key="luxor_console_refresh", width="stretch"):
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        st.session_state.luxor_console_refresh_message = ("error", "Quo is not configured in this environment.")
    else:
        try:
            client = quo_client.QuoClient(api_key=api_key)
            outcome = poll_for_new_messages(conn, client)
            st.session_state.luxor_console_refresh_message = (
                "success", f"Refreshed -- {len(outcome.queued)} new message(s) found.",
            )
        except QuoConfigError:
            st.session_state.luxor_console_refresh_message = ("error", "Quo is not configured in this environment.")
        except QuoAPIError:
            st.session_state.luxor_console_refresh_message = ("error", "Quo is temporarily unavailable. Please try again shortly.")
        shared.clear_pending_caches()
    st.rerun()

if st.session_state.luxor_console_refresh_message:
    _kind, _message = st.session_state.luxor_console_refresh_message
    getattr(st, _kind)(_message)

# ------------------------------------------------------------- The queue --
QUEUE_BUCKETS = ("Urgent / Needs Review", "New Valid Jobs", "Updates and Corrections", "Cancellations", "Closings")
_UPDATE_TYPES = {
    MessageType.DUPLICATE.value, MessageType.UPDATE_REQUEST.value,
    MessageType.STATUS.value, MessageType.CORRECTION.value, MessageType.ASSIGNMENT.value,
}


def queue_bucket(message_type: str, verdict: str) -> str:
    if message_type in (MessageType.UNRECOGNIZED.value, MessageType.CLOSING_WITHOUT_DISPATCH.value):
        return "Urgent / Needs Review"
    if message_type == MessageType.CANCELLATION.value:
        return "Cancellations"
    if message_type == MessageType.CLOSING.value:
        return "Closings"
    if verdict in ("NEEDS_REVIEW", "HANDLE_MANUALLY"):
        return "Urgent / Needs Review"
    if message_type == MessageType.NEW.value:
        return "New Valid Jobs"
    if message_type in _UPDATE_TYPES:
        return "Updates and Corrections"
    return "Urgent / Needs Review"


def policy_stream_key(group_label: str, source_value: str) -> str:
    return LUCKY_CLOSINGS_STREAM_KEY if group_label == "CLOSINGS" else source_value


pending = shared.fetch_pending_messages(conn)
queued: dict[str, list] = {name: [] for name in QUEUE_BUCKETS}
other_messages = []

for item in pending:
    job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
    if not shared.shows_job_transformation(classify_result, job):
        other_messages.append(item)
        continue
    result = shared.cached_full_preview(item["raw_text"], conn)
    bucket = queue_bucket(classify_result.message_type.value, result.decision.verdict.value)
    queued[bucket].append((item, job, classify_result, result))

for bucket in QUEUE_BUCKETS:
    queued[bucket].sort(key=lambda e: e[0]["received_at"] or e[0]["fetched_at"] or "", reverse=True)

priority_order = [entry for bucket in QUEUE_BUCKETS for entry in queued[bucket]]

# --------------------------------------------------------- Focused item --
focused_id = st.session_state.get("luxor_console_focused_message_id")
focused_entry = next((e for e in priority_order if e[0]["message_id"] == focused_id), None)
if focused_entry is None and priority_order:
    focused_entry = priority_order[0]
    st.session_state["luxor_console_focused_message_id"] = focused_entry[0]["message_id"]

if focused_entry is None:
    st.success("No jobs need your attention right now.")
else:
    item, job, classify_result, result = focused_entry
    stream = next((s for s in shared.STREAMS if s.group_label == item["group_label"]), None)
    stream_name = stream.display_name if stream else item["group_label"]
    area = ", ".join(p for p in (job.city.value, job.state.value) if p) or "Area unknown"
    missing = result.decision.missing_information

    st.subheader("Next Up")
    with st.container(border=True):
        st.write(f"**{job.ticket_number.value or 'No reference'}** · {stream_name}")
        st.write(f"{job.service_type.value or 'Not specified'} · {area}")
        st.caption(f"Received {shared.humanize_timestamp(item['received_at'] or item['fetched_at'])}")
        if missing:
            st.warning(f"Missing: {', '.join(missing)}")
        st.write(f"Recommended: **{shared.verdict_label(result.decision.verdict.value)}**")

    if "luxor_console_finalize_message" not in st.session_state:
        st.session_state.luxor_console_finalize_message = None

    def _finalize(action: str, note: Optional[str] = None) -> None:
        storage.save_audit_entry(
            conn, job, classify_result, decision=result.decision,
            suggested_quo_reply=result.decision.quo_reply_suggestion,
            luxor_message=result.luxor_message, dispatcher_edits=note,
            final_action=action, channel="manual",
        )
        storage.mark_quo_pending_message_actioned(conn, item["message_id"])
        shared.clear_pending_caches()
        shared.cached_extract_and_classify.clear()
        shared.cached_full_preview.clear()
        st.session_state.luxor_console_focused_message_id = None
        st.session_state.luxor_console_finalize_message = ("success", f"Recorded: {action}.")

    stream_key = policy_stream_key(item["group_label"], job.source.value)
    policy = get_policy(stream_key, classify_result.message_type.value)

    if st.button("✅ Acknowledge", type="primary", key="luxor_console_acknowledge", width="stretch"):
        _finalize("Approve")
        st.rerun()
    if policy.acknowledgment_required and policy.acknowledgment_text:
        st.caption(f"Candidate acknowledgment (not sent): \"{policy.acknowledgment_text}\" -- outbound sending is currently disabled.")
    else:
        st.caption("No company acknowledgment for this message type/source.")

    st.code(result.luxor_message)
    st.caption("Prepared preview — not sent · use the copy icon above")

    existing_job_id = classify_result.existing_job_id
    technician_name = None
    dispatch_group = None
    if existing_job_id is not None:
        job_row = conn.execute(
            "SELECT assigned_technician_id FROM jobs WHERE id = ?", (existing_job_id,)
        ).fetchone()
        if job_row and job_row[0]:
            tech = storage.get_technician(conn, job_row[0])
            if tech:
                technician_name = tech["name"]
                dispatch_group = tech["dispatch_group"]

    if dispatch_group:
        st.write(f"WhatsApp destination: **{dispatch_group}** ({technician_name})")
        if st.button("Mark Sent Manually", key="luxor_console_mark_sent", width="stretch"):
            storage.record_whatsapp_handoff(conn, existing_job_id, job_row[0])
            st.session_state.luxor_console_finalize_message = ("success", "Marked as sent manually.")
            st.rerun()
    else:
        st.caption("WhatsApp destination: not available -- no technician assigned to this job yet.")

    if st.button("🔧 Handle Manually", key="luxor_console_handle_manually", width="stretch"):
        _finalize("Handle Manually")
        st.rerun()

    if st.session_state.luxor_console_finalize_message:
        _kind, _msg = st.session_state.luxor_console_finalize_message
        getattr(st, _kind)(_msg)
        st.session_state.luxor_console_finalize_message = None

    with st.expander("More actions"):
        if st.button("View Original", key="luxor_console_view_original"):
            st.session_state.luxor_console_show_original = not st.session_state.get("luxor_console_show_original", False)
        if st.session_state.get("luxor_console_show_original"):
            st.code(shared.mask_sensitive(item["raw_text"]) or "")

        if st.button("View Extracted Fields", key="luxor_console_view_fields"):
            st.session_state.luxor_console_show_fields = not st.session_state.get("luxor_console_show_fields", False)
        if st.session_state.get("luxor_console_show_fields"):
            phone = job.phone.value
            if phone and job.extension.value:
                phone = f"{phone} ext {job.extension.value}"
            for label, value in [
                ("Customer", shared.mask_customer_name(job.customer_name.value) or "Not provided"),
                ("Phone", shared.mask_sensitive(phone) or "Not provided"),
                ("Address", job.address.value or "Not provided"),
                ("Vehicle", job.vehicle.value or "Not applicable"),
                ("Confidence", str(job.overall_confidence)),
            ]:
                st.write(f"**{label}:** {value}")

        if st.button("View Rules", key="luxor_console_view_rules"):
            st.session_state.luxor_console_show_rules = not st.session_state.get("luxor_console_show_rules", False)
        if st.session_state.get("luxor_console_show_rules"):
            if result.decision.reasons:
                st.write("Reasons: " + "; ".join(result.decision.reasons))
            if result.decision.restrictions_triggered:
                st.write("Restrictions: " + "; ".join(result.decision.restrictions_triggered))
            if result.decision.service_area:
                st.write(f"Service area: {result.decision.service_area}")

        if st.button("Reject / Pass", key="luxor_console_reject"):
            _finalize("Reject")
            st.rerun()

        if existing_job_id is not None and st.button("Open Full Job", key="luxor_console_open_full_job"):
            st.session_state.luxor_console_show_timeline = not st.session_state.get("luxor_console_show_timeline", False)
        if st.session_state.get("luxor_console_show_timeline") and existing_job_id is not None:
            timeline = shared.fetch_timeline(conn, existing_job_id)
            for event in timeline:
                st.write(f"{shared.humanize_event_label(event)} — {shared.humanize_timestamp(event['created_at'])}")

st.divider()

# ------------------------------------------------------------------ Queue -
st.subheader("Queue")
for bucket in QUEUE_BUCKETS:
    entries = queued[bucket]
    st.markdown(f"**{bucket}** ({len(entries)})")
    for item, job, classify_result, result in entries:
        stream = next((s for s in shared.STREAMS if s.group_label == item["group_label"]), None)
        stream_name = stream.display_name if stream else item["group_label"]
        is_focused = item["message_id"] == st.session_state.get("luxor_console_focused_message_id")
        cols = st.columns([4, 1])
        cols[0].write(
            f"{'👉 ' if is_focused else ''}{job.ticket_number.value or 'No reference'} · {stream_name} · "
            f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])}"
        )
        if cols[1].button("Focus", key=f"luxor_console_focus_{item['message_id']}"):
            st.session_state["luxor_console_focused_message_id"] = item["message_id"]
            st.rerun()

with st.expander(f"Other Messages ({len(other_messages)})"):
    for item in other_messages:
        st.caption(
            f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — "
            f"{shared.mask_sensitive(item['raw_text'])[:80]}"
        )
