"""Dispatch Queue -- the default, primary screen. Actionable items only,
deterministically ordered. A raw message never becomes a dispatch card
until it passes the existing job-transformation gate; acks/fragments
land in a collapsed Other Messages section instead.

Real write actions (K/Handle Manually/Pass/Mark Dispatched) call the
exact same storage.save_audit_entry()/mark_quo_pending_message_actioned()
path app.py's own buttons already use. K/Copy/WhatsApp before that point
only write to the additive dispatch_tracking table -- a job isn't
recorded as a committed CRM job until Mark Dispatched (or Handle
Manually/Pass) is tapped.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
from dispatcher import outbound, quo_client, storage
from dispatcher.policy import LUCKY_CLOSINGS_STREAM_KEY, get_policy
from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError
from dispatcher.quo_ingest import poll_for_new_messages
from dispatcher.rules.market_aliases import resolve_market_and_area
from dispatcher.rules.service_areas import find_service_area
from dispatcher.rules.sla_config import format_age, urgency_level
from dispatcher.storage import MessageType

if not st.session_state.get("luxor_console_authenticated"):
    st.stop()


def get_db():
    return storage.connect()


conn = get_db()

# ------------------------------------------------------------ Refresh ----
if "luxor_console_refresh_message" not in st.session_state:
    st.session_state.luxor_console_refresh_message = None

refresh_col, alert_col = st.columns([2, 2])
with refresh_col:
    if st.button("Refresh", key="luxor_console_refresh", width="stretch"):
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            st.session_state.luxor_console_refresh_message = ("error", "Quo is not configured in this environment.")
        else:
            try:
                client = quo_client.QuoClient(api_key=api_key)
                outcome = poll_for_new_messages(conn, client)
                st.session_state.luxor_console_refresh_message = (
                    "success", f"Refreshed -- {len(outcome.queued)} new message(s).",
                )
            except QuoConfigError as exc:
                storage.record_poll_error(conn, str(exc))
                st.session_state.luxor_console_refresh_message = ("error", "Quo is not configured.")
            except QuoAPIError as exc:
                storage.record_poll_error(conn, str(exc))
                st.session_state.luxor_console_refresh_message = ("error", "Quo temporarily unavailable.")
            shared.clear_pending_caches()
        st.rerun()

if st.session_state.luxor_console_refresh_message:
    _kind, _msg = st.session_state.luxor_console_refresh_message
    getattr(st, _kind)(_msg)
    st.session_state.luxor_console_refresh_message = None

# --------------------------------------------------------- Queue build ----
QUEUE_BUCKETS = ("Urgent", "New Jobs", "Corrections & Cancellations", "Status Requests", "Closings")
_CORRECTION_CANCEL_TYPES = {MessageType.CORRECTION.value, MessageType.CANCELLATION.value}
_STATUS_TYPES = {MessageType.STATUS.value, MessageType.UPDATE_REQUEST.value, MessageType.DUPLICATE.value, MessageType.ASSIGNMENT.value}


def policy_stream_key(group_label: str, source_value: str) -> str:
    return LUCKY_CLOSINGS_STREAM_KEY if group_label == "CLOSINGS" else source_value


def queue_bucket(message_type: str, urgency: str) -> str:
    if urgency == "urgent":
        return "Urgent"
    if message_type == MessageType.NEW.value:
        return "New Jobs"
    if message_type in _CORRECTION_CANCEL_TYPES or message_type == MessageType.UNRECOGNIZED.value or message_type == MessageType.CLOSING_WITHOUT_DISPATCH.value:
        return "Corrections & Cancellations"
    if message_type in _STATUS_TYPES:
        return "Status Requests"
    if message_type == MessageType.CLOSING.value:
        return "Closings"
    return "Corrections & Cancellations"


pending = shared.fetch_pending_messages(conn)
queued: dict = {name: [] for name in QUEUE_BUCKETS}
other_messages = []
urgent_count = 0

for item in pending:
    job, classify_result = shared.cached_extract_and_classify(item["raw_text"], conn)
    if not shared.shows_job_transformation(classify_result, job):
        other_messages.append(item)
        continue

    storage.mark_first_seen(conn, item["message_id"])
    tracking = storage.get_dispatch_tracking(conn, item["message_id"])
    acknowledged = tracking["acknowledged_at"] is not None
    dispatched = tracking["dispatched_manually_at"] is not None
    if dispatched:
        continue  # already handled -- no longer in the actionable queue

    result = shared.cached_full_preview(item["raw_text"], conn)
    storage.mark_luxor_prepared(conn, item["message_id"])

    received = item["received_at"] or item["fetched_at"] or tracking["first_seen_at"]
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat((received or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        age_seconds = 0

    urgency = urgency_level(age_seconds, acknowledged, dispatched)
    if urgency == "urgent":
        urgent_count += 1

    bucket = queue_bucket(classify_result.message_type.value, urgency)
    queued[bucket].append((item, job, classify_result, result, tracking, urgency, age_seconds))

for bucket in QUEUE_BUCKETS:
    queued[bucket].sort(key=lambda e: e[0]["received_at"] or e[0]["fetched_at"] or "")  # oldest first

all_entries = [entry for bucket in QUEUE_BUCKETS for entry in queued[bucket]]

# ------------------------------------------------------------ Alerts -----
alert_placeholder_count = sum(len(queued[b]) for b in QUEUE_BUCKETS)
with alert_col:
    st.metric("Waiting", alert_placeholder_count, delta=f"{urgent_count} urgent" if urgent_count else None)

if all_entries:
    oldest_age = all_entries[0][6]
    st.caption(f"Oldest unhandled: {format_age(oldest_age)}")

if "luxor_console_sound_enabled" not in st.session_state:
    st.session_state.luxor_console_sound_enabled = False
sound_col, notif_col = st.columns(2)
with sound_col:
    st.session_state.luxor_console_sound_enabled = st.checkbox(
        "🔔 Audible alert", value=st.session_state.luxor_console_sound_enabled, key="luxor_console_sound_toggle",
    )
with notif_col:
    st.caption("Browser notifications: tap to enable")

_notify_js = ""
if urgent_count > 0:
    if st.session_state.luxor_console_sound_enabled:
        _notify_js += """
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            osc.frequency.value = 880; osc.connect(ctx.destination); osc.start();
            setTimeout(() => osc.stop(), 200);
        } catch (e) {}
        """
    _notify_js += f"""
    if (window.Notification && Notification.permission === 'granted') {{
        new Notification('LUXOR Dispatch', {{ body: '{urgent_count} urgent job(s) waiting' }});
    }} else if (window.Notification && Notification.permission !== 'denied') {{
        Notification.requestPermission();
    }}
    """
if _notify_js:
    st.markdown(f"<script>{_notify_js}</script>", unsafe_allow_html=True)

st.divider()

# ------------------------------------------------------- Job card + actions
service_areas = shared.get_service_areas()


def urgency_badge(urgency: str) -> str:
    return {"urgent": "🔴 URGENT", "warning": "🟠 Attention", "attention": "🟢 New"}.get(urgency, urgency)


def render_job_card(entry) -> None:
    item, job, classify_result, result, tracking, urgency, age_seconds = entry
    stream = next((s for s in shared.STREAMS if s.group_label == item["group_label"]), None)
    stream_name = stream.display_name if stream else item["group_label"]
    market_label = shared.extract_market_label(item["raw_text"]) if job.source.value == "Main Leads" else None
    area_lookup = find_service_area(job.city.value, job.state.value, service_areas)
    address_area = (area_lookup.record.city or area_lookup.record.state) if area_lookup.status == "confirmed" else None
    resolution = resolve_market_and_area(address_area, market_label)

    with st.container(border=True):
        st.markdown(f"**{urgency_badge(urgency)} · {format_age(age_seconds)}**")
        header = f"**{job.source.value}**"
        if market_label:
            header += f" ({market_label})"
        header += f" · {job.ticket_number.value or 'No reference'}"
        st.write(header)
        st.write(f"{resolution.canonical_area or 'Area unresolved'} · {job.service_type.value or 'Not specified'}")
        if job.customer_name.value:
            st.caption(f"Customer: {shared.mask_customer_name(job.customer_name.value)}")

        missing = result.decision.missing_information
        if missing:
            st.warning(f"Missing: {', '.join(missing)}")
        if classify_result.conflicting_fields:
            st.warning(f"Conflicting: {', '.join(classify_result.conflicting_fields)}")

        if resolution.canonical_area:
            eligible = storage.list_eligible_technicians_for(conn, resolution.canonical_area, job.service_type.value)
            if eligible:
                st.write("Candidates: " + ", ".join(
                    f"{t['name']} ({t['dispatch_group'] or 'WhatsApp destination not configured'})" for t in eligible
                ))
            else:
                st.caption("No eligible technician configured.")
        else:
            st.caption(f"Routing: {resolution.reason}")

        ack_state = "Acknowledged" if tracking["acknowledged_at"] else "Not acknowledged"
        dispatch_state = "Dispatched" if tracking["dispatched_manually_at"] else "Not dispatched"
        st.caption(f"{ack_state} · {dispatch_state}")

        st.markdown("**LUXOR LOCKSMITH — Ready to Copy**")
        st.code(result.luxor_message)
        st.caption("Prepared preview — not sent")

        stream_key = policy_stream_key(item["group_label"], job.source.value)
        policy = get_policy(stream_key, classify_result.message_type.value)
        mid = item["message_id"]

        if policy.acknowledgment_required:
            mode = outbound.current_mode()
            label = "Send K" if mode in (outbound.OutboundMode.APPROVAL, outbound.OutboundMode.LIVE) else "K Prepared"
            if not tracking["acknowledged_at"]:
                if st.button(label, key=f"luxor_console_k_{mid}", type="primary", width="stretch"):
                    storage.mark_acknowledged(conn, mid)
                    if mode in (outbound.OutboundMode.APPROVAL, outbound.OutboundMode.LIVE):
                        req = outbound.build_request(
                            source=job.source.value, job_id=classify_result.existing_job_id,
                            quo_contact_id=stream.group_label if stream else item["group_label"], text="K",
                        )
                        out_result = outbound.propose_send(conn, req, approved_by="dispatcher-tap")
                        st.caption(f"Outbound: {out_result.outcome} — {out_result.reason}")
                    st.rerun()
            else:
                st.success("✅ K acknowledged")

        if st.button("📋 Mark Luxor Message Copied", key=f"luxor_console_copy_{mid}", width="stretch"):
            storage.mark_copied(conn, mid)
            st.rerun()
        if tracking["copied_at"]:
            st.caption("✅ Copied")

        eligible_for_wa = storage.list_eligible_technicians_for(conn, resolution.canonical_area, job.service_type.value) if resolution.canonical_area else []
        destination = eligible_for_wa[0]["dispatch_group"] if eligible_for_wa else None
        if destination:
            if st.button("Open WhatsApp", key=f"luxor_console_wa_{mid}", width="stretch"):
                storage.mark_whatsapp_opened(conn, mid)
                st.rerun()
        else:
            st.caption("WhatsApp destination not configured")

        can_mark_dispatched = bool(destination) and tracking["copied_at"] is not None
        if can_mark_dispatched:
            if st.button("Mark Dispatched", key=f"luxor_console_dispatch_{mid}", type="primary", width="stretch"):
                storage.save_audit_entry(
                    conn, job, classify_result, decision=result.decision,
                    suggested_quo_reply=result.decision.quo_reply_suggestion,
                    luxor_message=result.luxor_message, final_action="Approve", channel="manual",
                )
                storage.mark_quo_pending_message_actioned(conn, mid)
                storage.mark_dispatched_manually(conn, mid)
                shared.clear_pending_caches()
                shared.cached_extract_and_classify.clear()
                shared.cached_full_preview.clear()
                st.success("Marked dispatched.")
                st.rerun()
        else:
            st.caption("Mark Dispatched requires a configured WhatsApp destination and a copied message.")

        with st.expander("More"):
            if st.button("View Original", key=f"luxor_console_orig_{mid}"):
                st.session_state[f"show_orig_{mid}"] = not st.session_state.get(f"show_orig_{mid}", False)
            if st.session_state.get(f"show_orig_{mid}"):
                st.code(shared.mask_sensitive(item["raw_text"]) or "")

            if st.button("View Decision Reasons", key=f"luxor_console_reasons_{mid}"):
                st.session_state[f"show_reasons_{mid}"] = not st.session_state.get(f"show_reasons_{mid}", False)
            if st.session_state.get(f"show_reasons_{mid}"):
                for reason in result.decision.reasons:
                    st.write(f"- {reason}")

            if st.button("Handle Manually", key=f"luxor_console_hm_{mid}"):
                storage.save_audit_entry(
                    conn, job, classify_result, decision=result.decision,
                    luxor_message=result.luxor_message, final_action="Handle Manually", channel="manual",
                )
                storage.mark_quo_pending_message_actioned(conn, mid)
                shared.clear_pending_caches()
                st.rerun()

            if st.button("Pass / Reject", key=f"luxor_console_reject_{mid}"):
                storage.save_audit_entry(
                    conn, job, classify_result, decision=result.decision,
                    luxor_message=result.luxor_message, final_action="Reject", channel="manual",
                )
                storage.mark_quo_pending_message_actioned(conn, mid)
                shared.clear_pending_caches()
                st.rerun()


if not all_entries:
    st.success("No jobs need your attention right now.")
else:
    for bucket in QUEUE_BUCKETS:
        entries = queued[bucket]
        if not entries:
            continue
        st.markdown(f"### {bucket} ({len(entries)})")
        for entry in entries:
            render_job_card(entry)

with st.expander(f"Other Messages ({len(other_messages)})"):
    for item in other_messages:
        st.caption(
            f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — "
            f"{shared.mask_sensitive(item['raw_text'])[:80]}"
        )

conn.close()
