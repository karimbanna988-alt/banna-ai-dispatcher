"""Dispatch Queue -- the default, primary screen. Actionable items only,
deterministically ordered. A raw message never becomes a dispatch card
until it passes the existing job-transformation gate; acks/fragments
land in a collapsed Other Messages section instead.

Live-queue boundary (corrective fix, 2026-07-29): quo_pending_messages
accumulates every message ever fetched, including historical
development/shadow-mode backlog from long before this console existed.
Only messages received at or after the durable console_activation_state
boundary (storage.get_live_queue_started_at / ensure_live_queue_activated)
are eligible to appear as live, urgent, waiting-for-dispatch work.
Everything older is preserved unchanged for audit but rendered only in
the collapsed Previous Backlog section -- never urgent, never counted as
Waiting, never included in oldest-live-job timing, never a notification
trigger. See storage.reset_live_queue_boundary() for the one explicit,
audited, human-confirmed way to move that boundary.

Real write actions (K/Handle Manually/Pass/Mark Dispatched) call the
exact same storage.save_audit_entry()/mark_quo_pending_message_actioned()
path app.py's own buttons already use. K/Copy/WhatsApp before that point
only write to the additive dispatch_tracking table -- a job isn't
recorded as a committed CRM job until Mark Dispatched (or Handle
Manually/Pass) is tapped.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import luxor_ops_shared as shared
import luxor_desk_read_model as read_model
from dispatcher import outbound, quo_client, storage
from dispatcher.policy import get_policy
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

# ------------------------------------------------- Live-queue boundary ----
# Seeded once, ever, the first time this corrected console runs against
# this database -- never re-seeded on a later restart (see
# storage.ensure_live_queue_activated's own docstring for why).
live_queue_started_at = storage.ensure_live_queue_activated(conn)


# _parse_ts / is_live are shared with luxor_desk via luxor_desk_read_model
# (see that module's docstring) -- kept as thin local aliases so the rest
# of this file's calls don't need to change.
_parse_ts = read_model.parse_ts
_boundary_dt = _parse_ts(live_queue_started_at)


def is_live(item: dict) -> bool:
    return read_model.is_live(item, _boundary_dt)


with st.expander(f"⚙️ Live queue boundary: {shared.humanize_timestamp(live_queue_started_at)}"):
    st.caption(
        "Messages received before this moment are Previous Backlog -- preserved for audit, "
        "never urgent, never counted as Waiting. Resetting is a deliberate action: it moves "
        "every currently-live, not-yet-dispatched job into backlog too."
    )
    if not st.session_state.get("luxor_console_confirm_reset_boundary"):
        if st.button("Reset live queue boundary to now", key="luxor_console_reset_boundary_start"):
            st.session_state.luxor_console_confirm_reset_boundary = True
            st.rerun()
    else:
        st.warning("This moves ALL current live jobs into Previous Backlog. Confirm?")
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("Yes, reset now", key="luxor_console_reset_boundary_confirm", type="primary"):
                storage.reset_live_queue_boundary(conn, note="Manual reset from Dispatch Queue")
                st.session_state.luxor_console_confirm_reset_boundary = False
                shared.clear_pending_caches()
                st.rerun()
        with cancel_col:
            if st.button("Cancel", key="luxor_console_reset_boundary_cancel"):
                st.session_state.luxor_console_confirm_reset_boundary = False
                st.rerun()

# ------------------------------------------------------------ Sync -------
if "luxor_console_refresh_message" not in st.session_state:
    st.session_state.luxor_console_refresh_message = None

sync_col, conn_col = st.columns([1, 2])
with sync_col:
    if st.button("Sync", key="luxor_console_refresh", width="stretch"):
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            st.session_state.luxor_console_refresh_message = ("error", "Quo is not configured in this environment.")
        else:
            try:
                client = quo_client.QuoClient(api_key=api_key)
                outcome = poll_for_new_messages(conn, client)
                st.session_state.luxor_console_refresh_message = (
                    "success", f"Synced -- {len(outcome.queued)} new message(s).",
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

# ------------------------------------------------- Connection indicator ---
_connected = bool(os.environ.get(API_KEY_ENV_VAR))
_state, _ = shared.connection_state(conn, _connected)
_live_label = {"connected": "🟢 Live"}.get(_state, "🟡 Delayed" if _state == "delayed" else "🔴 Disconnected")
_last_poll = shared.fetch_last_refresh(conn)
_min_interval = int(os.environ.get("QUO_POLL_MIN_INTERVAL_SECONDS", 300))
_poller_state = "⚪ not detected recently -- start scripts/background_poller.py --loop"
if _last_poll:
    _dt = _parse_ts(_last_poll)
    if _dt is not None and (datetime.now(timezone.utc) - _dt).total_seconds() < (_min_interval * 2):
        _poller_state = "🟢 likely running"
with conn_col:
    st.caption(f"{_live_label} · last poll: {shared.humanize_timestamp(_last_poll)} · background poller: {_poller_state}")

# ------------------------------------------------------- Auto-refresh ----
# Hidden, single, fixed-key button clicked by client-side JS every 4s.
# A widget click is Streamlit's own trigger for a full script rerun, so
# this refreshes the LOCAL view from sqlite only -- it never calls Quo
# (Quo is only ever called from the Sync button above). Inert in
# AppTest (no real browser/JS there), so it cannot affect the test suite.
st.markdown(
    """
    <style>.st-key-luxor_console_autorefresh_trigger { display: none !important; }</style>
    <script>
    setTimeout(function() {
        const btn = window.parent.document.querySelector(
            '.st-key-luxor_console_autorefresh_trigger button'
        ) || document.querySelector('.st-key-luxor_console_autorefresh_trigger button');
        if (btn) { btn.click(); }
    }, 4000);
    </script>
    """,
    unsafe_allow_html=True,
)
st.button("autorefresh", key="luxor_console_autorefresh_trigger")

# --------------------------------------------------------- Queue build ----
# QUEUE_BUCKETS / policy_stream_key / queue_bucket / canonicalize are
# shared with luxor_desk via luxor_desk_read_model (see that module's
# docstring) -- one tested implementation, not two independent forks.
QUEUE_BUCKETS = read_model.QUEUE_BUCKETS
_UPDATES_CLOSINGS_TYPES = read_model.UPDATES_CLOSINGS_TYPES
policy_stream_key = read_model.policy_stream_key
queue_bucket = read_model.queue_bucket
canonicalize = read_model.canonicalize


pending = shared.fetch_pending_messages(conn)
live_pending = [item for item in pending if is_live(item)]
backlog_pending = [item for item in pending if not is_live(item)]

other_messages = []
live_actionable = []  # flat list of (item, job, classify_result, result, tracking, urgency, age_seconds)

for item in live_pending:
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
    dt = _parse_ts(received)
    age_seconds = (datetime.now(timezone.utc) - dt).total_seconds() if dt else 0

    urgency = urgency_level(age_seconds, acknowledged, dispatched)
    live_actionable.append((item, job, classify_result, result, tracking, urgency, age_seconds))


# ------------------------------------------- Canonical action dedup (Phase 3)
canonical_live = canonicalize(live_actionable)

# ----------------------------------------------------- Company filters ----
FILTER_OPTIONS = ("All", "AGN", "Lucky", "Main Leads", "Updates / Closings", "Needs Review")


def entry_source(entry) -> str:
    _, job, *_ = entry
    return job.source.value


def entry_matches_filter(entry, filter_name: str) -> bool:
    if filter_name == "All":
        return True
    if filter_name in ("AGN", "Lucky", "Main Leads"):
        return entry_source(entry) == filter_name
    _item, _job, classify_result, result, *_ = entry
    if filter_name == "Updates / Closings":
        return (
            _item["group_label"] == "CLOSINGS"
            or classify_result.message_type.value in _UPDATES_CLOSINGS_TYPES
        )
    if filter_name == "Needs Review":
        return (
            classify_result.message_type.value in shared.ATTENTION_MESSAGE_TYPES
            or bool(result.decision.missing_information)
            or bool(classify_result.conflicting_fields)
        )
    return True


filter_counts = {name: sum(1 for e, _ in canonical_live if entry_matches_filter(e, name)) for name in FILTER_OPTIONS}
active_filter = st.pills(
    "Filter by source", FILTER_OPTIONS,
    selection_mode="single", default="All", key="luxor_console_filter_pills",
    format_func=lambda name: f"{name} ({filter_counts[name]})",
    label_visibility="collapsed",
) or "All"

filtered_canonical = [(e, extra) for e, extra in canonical_live if entry_matches_filter(e, active_filter)]

queued: dict = {name: [] for name in QUEUE_BUCKETS}
for entry, extra in filtered_canonical:
    _item, _job, classify_result, _result, _tracking, urgency, _age = entry
    bucket = queue_bucket(classify_result.message_type.value, urgency)
    queued[bucket].append((entry, extra))

for bucket in QUEUE_BUCKETS:
    queued[bucket].sort(key=lambda pair: pair[0][0]["received_at"] or pair[0][0]["fetched_at"] or "")  # oldest first

all_filtered_entries = [pair for bucket in QUEUE_BUCKETS for pair in queued[bucket]]

# ------------------------------------------------------------ Counters ----
new_jobs_count = len(queued["New Jobs"])
needs_attention_count = len(queued["Urgent"])
waiting_for_dispatch_count = new_jobs_count + needs_attention_count + len(queued["Corrections & Cancellations"]) + len(queued["Status Requests"])
oldest_live_age = all_filtered_entries[0][0][6] if all_filtered_entries else None

metric_cols = st.columns(4)
metric_cols[0].metric("New jobs", new_jobs_count)
metric_cols[1].metric("Needs attention", needs_attention_count)
metric_cols[2].metric("Waiting for dispatch", waiting_for_dispatch_count)
metric_cols[3].metric("Oldest live job", format_age(oldest_live_age) if oldest_live_age is not None else "--")
st.caption(f"Previous backlog: {len(backlog_pending)} (not urgent, not counted above)")

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
if needs_attention_count > 0:
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
        new Notification('LUXOR Dispatch', {{ body: '{needs_attention_count} job(s) need attention' }});
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


def render_job_card(entry, extra_count: int) -> None:
    item, job, classify_result, result, tracking, urgency, age_seconds = entry
    stream = next((s for s in shared.STREAMS if s.group_label == item["group_label"]), None)
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
        if extra_count:
            st.caption(f"+{extra_count} additional message(s) for this ticket -- folded into this card")
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
            if mode in (outbound.OutboundMode.APPROVAL, outbound.OutboundMode.LIVE):
                if not tracking["acknowledged_at"]:
                    if st.button("Send K", key=f"luxor_console_k_{mid}", type="primary", width="stretch"):
                        storage.mark_acknowledged(conn, mid)
                        req = outbound.build_request(
                            source=job.source.value, job_id=classify_result.existing_job_id,
                            quo_contact_id=stream.group_label if stream else item["group_label"], text="K",
                        )
                        out_result = outbound.propose_send(conn, req, approved_by="dispatcher-tap")
                        st.caption(f"Outbound: {out_result.outcome} — {out_result.reason}")
                        st.rerun()
                else:
                    st.success("✅ K acknowledged")
            else:
                if not tracking["acknowledged_at"]:
                    st.caption("K ready — sending disabled")
                    if st.button("Mark K sent", key=f"luxor_console_k_{mid}", width="stretch"):
                        storage.mark_acknowledged(conn, mid)
                        st.rerun()
                else:
                    st.success("✅ K acknowledged")
        else:
            st.caption("No acknowledgment required")

        copy_payload = json.dumps(result.luxor_message)
        st.markdown(
            f"""
            <button
                onclick="
                    navigator.clipboard.writeText({copy_payload}).then(function() {{
                        var s = document.getElementById('luxor-copy-status-{mid}');
                        s.innerText = '✅ Copied to clipboard';
                        s.style.color = '#1a7f37';
                    }}).catch(function() {{
                        var s = document.getElementById('luxor-copy-status-{mid}');
                        s.innerText = '⚠️ Copy failed — select the text above manually';
                        s.style.color = '#b42318';
                    }});
                "
                style="width:100%; padding:0.6rem; border-radius:0.5rem; border:1px solid #999;
                       background:#fff; color:#111; font-size:1rem; min-height:44px; cursor:pointer;"
            >📋 Copy Luxor Message</button>
            <div id="luxor-copy-status-{mid}" style="margin-top:0.25rem; font-size:0.85rem;"></div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("✓ Confirm copied", key=f"luxor_console_copy_confirm_{mid}", width="stretch"):
            storage.mark_copied(conn, mid)
            st.rerun()
        st.caption('Tap "Confirm copied" only after "Copied to clipboard" appears above.')
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


if not all_filtered_entries:
    st.success("No jobs need your attention right now.")
else:
    for bucket in QUEUE_BUCKETS:
        entries = queued[bucket]
        if not entries:
            continue
        st.markdown(f"### {bucket} ({len(entries)})")
        for entry, extra_count in entries:
            render_job_card(entry, extra_count)

with st.expander(f"Other Messages ({len(other_messages)})"):
    for item in other_messages:
        st.caption(
            f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — "
            f"{shared.mask_sensitive(item['raw_text'])[:80]}"
        )

with st.expander(f"Previous Backlog ({len(backlog_pending)})"):
    st.caption("Historical development/shadow-mode messages received before the live queue boundary above. Preserved for audit; never urgent.")
    for item in backlog_pending:
        st.caption(
            f"{shared.humanize_timestamp(item['received_at'] or item['fetched_at'])} — "
            f"{item['group_label']} · {shared.mask_sensitive(item['raw_text'])[:80]}"
        )

conn.close()
