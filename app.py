"""Luxor Locksmith AI Dispatcher -- Phase 1 Streamlit approval interface.

Pure orchestration: this file wires together parser -> storage.classify ->
companies -> rules.decide -> formatter -> storage.save_audit_entry. It
contains no business logic of its own. Nothing here ever sends anything to
Quo, WhatsApp, or a technician -- every action requires the dispatcher to
click Approve/Edit/Reject/Handle Manually, and even then the dispatcher
copies the approved text out manually.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st

from dispatcher import storage, updates
from dispatcher.closing import ClosingInput, render_closing
from dispatcher.companies import get_company_profile
from dispatcher.formatter import format_technician_message
from dispatcher.models import ExtractionStatus
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.rules import Verdict, decide, force_handle_manually
from dispatcher.rules.daily import parse_daily_rules
from dispatcher.rules.service_areas import load_service_areas
from dispatcher.storage import ClassifyResult, MessageType

st.set_page_config(page_title="Luxor Locksmith AI Dispatcher", layout="wide")


@st.cache_resource
def get_db():
    return storage.connect()


@st.cache_resource
def get_vehicle_rules():
    return load_vehicle_rules()


@st.cache_resource
def get_service_areas():
    return load_service_areas()


for key, default in (
    ("daily_rules", None),
    ("current_job", None),
    ("current_classify", None),
    ("current_decision", None),
    ("current_luxor_message", None),
    ("status_reply", None),
    ("last_closing_result", None),
):
    if key not in st.session_state:
        st.session_state[key] = default

conn = get_db()
vehicle_rules = get_vehicle_rules()
service_areas = get_service_areas()

st.title("Luxor Locksmith — AI Dispatcher (Phase 1)")
st.caption(
    "Nothing on this screen is ever sent to Quo, WhatsApp, or a technician automatically. "
    "Every suggestion requires your approval, and even then you copy the text yourself."
)

tab_process, tab_daily, tab_status, tab_closing, tab_audit = st.tabs(
    ["Process Job", "Daily Rules", "Status Update", "Closing", "Audit Timeline"]
)

# ---------------------------------------------------------------- Process --
with tab_process:
    st.subheader("1. Paste the raw Quo message")
    raw_text = st.text_area("Raw message", height=220, key="raw_text_input")

    if st.button("Process", type="primary"):
        job = extract_job(raw_text, vehicle_rules)
        classify_result = storage.classify_incoming(conn, job)
        profile = get_company_profile(job.source)
        decision = decide(job, profile, service_areas, st.session_state.daily_rules)
        if classify_result.message_type == MessageType.CLOSING_WITHOUT_DISPATCH:
            decision = force_handle_manually(decision, classify_result.reason)
        luxor_message = format_technician_message(job)

        st.session_state.current_job = job
        st.session_state.current_classify = classify_result
        st.session_state.current_profile = profile
        st.session_state.current_decision = decision
        st.session_state.current_luxor_message = luxor_message
        # The "edit_message" text_area below is keyed, so Streamlit ignores
        # its value= on rerun once session_state already holds that key --
        # without this, processing a second job leaves the box showing the
        # previous job's message. Overwrite it explicitly here.
        st.session_state.edit_message = luxor_message

    job = st.session_state.current_job
    if job is not None:
        classify_result = st.session_state.current_classify
        decision = st.session_state.current_decision
        luxor_message = st.session_state.current_luxor_message

        st.subheader("2. Detected source")
        st.write(f"**{job.source.value}** (confidence {job.source_confidence}) — {job.source_reason}")
        if classify_result.message_type != MessageType.NEW:
            st.info(f"Message type: {classify_result.message_type.value} — {classify_result.reason}")
        if classify_result.conflicting_fields:
            st.warning(f"Conflicting fields vs. the stored job: {', '.join(classify_result.conflicting_fields)}")

        st.subheader("3. Extracted fields")
        st.table(
            [
                {
                    "Field": name,
                    "Value": fv.value if fv.value is not None else "(missing)",
                    "Confidence": fv.confidence,
                    "Reason": fv.reason,
                }
                for name, fv in job.all_fields().items()
            ]
        )

        st.subheader("4. Overall confidence and status")
        st.write(f"Overall confidence: **{job.overall_confidence}**")
        if job.status == ExtractionStatus.HANDLE_MANUALLY:
            st.error("Extraction forced HANDLE MANUALLY:")
        for reason in job.status_reasons:
            st.write(f"- {reason}")

        st.subheader("5. Decision")
        verdict_style = {
            "ACCEPT": st.success,
            "PASS": st.warning,
            "NEEDS_REVIEW": st.warning,
            "HANDLE_MANUALLY": st.error,
        }
        verdict_style.get(decision.verdict.value, st.write)(f"Verdict: {decision.verdict.value}")
        for reason in decision.reasons:
            st.write(f"- {reason}")
        if decision.missing_information:
            st.write(f"Missing: {', '.join(decision.missing_information)}")
        if decision.conflicts:
            st.write(f"Conflicts: {', '.join(decision.conflicts)}")
        if decision.restrictions_triggered:
            st.write(f"Restrictions triggered: {', '.join(decision.restrictions_triggered)}")
        st.write(f"Dispatch group: {decision.dispatch_group or '(not confirmed)'}")
        st.write(f"Suggested Quo reply: {decision.quo_reply_suggestion or '(none)'}")

        st.subheader("6. Luxor Locksmith technician message")
        edited_message = st.text_area(
            "Technician message (editable before approving)", value=luxor_message, height=220, key="edit_message"
        )

        st.subheader("7. Your action")
        edit_notes = st.text_input("Edit notes (optional, saved to the audit log)", key="edit_notes")
        col1, col2, col3, col4 = st.columns(4)

        def _finalize(action: str) -> None:
            edits = edited_message if edited_message != luxor_message else None
            storage.save_audit_entry(
                conn, job, classify_result, decision=decision,
                suggested_quo_reply=decision.quo_reply_suggestion,
                luxor_message=luxor_message,
                dispatcher_edits=edits or (edit_notes or None),
                final_action=action,
            )
            st.success(f"Recorded action: {action}. Nothing was sent automatically — copy the approved text yourself.")

        if col1.button("Approve"):
            _finalize("Approve")
        if col2.button("Edit"):
            _finalize("Edit")
        if col3.button("Reject"):
            _finalize("Reject")
        if col4.button("Handle Manually"):
            _finalize("Handle Manually")

# ------------------------------------------------------------- Daily rules --
with tab_daily:
    st.subheader("Paste today's freeform shift-start message")
    daily_text = st.text_area("Daily rules", height=200, key="daily_text_input")
    shift_hours = st.number_input("Shift length (hours)", min_value=1, max_value=24, value=12)

    if st.button("Parse daily rules"):
        st.session_state.daily_rules = parse_daily_rules(daily_text, shift_hours=int(shift_hours))

    daily = st.session_state.daily_rules
    if daily is not None:
        st.text(daily.summary())
        if daily.needs_confirmation:
            st.warning("Some lines could not be parsed — confirm these manually before relying on today's rules.")
        st.caption("These rules apply to every job processed in the Process Job tab until they expire.")

# ------------------------------------------------------------ Status update --
with tab_status:
    st.subheader("Technician status update")
    st.caption('Also used to answer company questions like "Any update?" or "Did anyone call?"')
    status_text = st.text_input("Technician's status (paste exactly, confirmed by you)", key="status_text_input")

    if st.button("Map status"):
        st.session_state.status_reply = updates.map_status_to_reply(status_text)

    reply = st.session_state.status_reply
    if reply is not None:
        if reply.confirmed:
            st.success(f"Suggested reply: {reply.reply}")
            if st.button("Approve status reply"):
                st.success("Recorded. Copy this reply into Quo yourself.")
        else:
            st.error(reply.reason)

# ----------------------------------------------------------------- Closing --
with tab_closing:
    st.subheader("Closing")
    st.caption("Uses the job processed in the Process Job tab. Enter only what the technician actually reported.")
    amount = st.text_input("Final amount (verbatim from technician)", key="closing_amount")
    payment_method = st.text_input("Payment method (e.g. CC)", key="closing_payment")
    parts_amount = st.text_input("Parts amount (optional, verbatim)", key="closing_parts")

    if st.button("Render closing"):
        if st.session_state.current_job is None:
            st.error("Process a job in the Process Job tab first.")
        else:
            closing_input = ClosingInput(amount=amount, payment_method=payment_method, parts_amount=parts_amount or None)
            st.session_state.last_closing_result = render_closing(st.session_state.current_job, closing_input)

    result = st.session_state.last_closing_result
    if result is not None:
        if result.supported:
            st.text_area("Closing message", value=result.text, height=220, key="closing_message_display")
            if st.button("Record closing to audit log"):
                job = st.session_state.current_job
                row = conn.execute(
                    "SELECT id FROM jobs WHERE ticket_number = ?", (job.ticket_number.value,)
                ).fetchone()
                classify_result = ClassifyResult(
                    MessageType.CLOSING, existing_job_id=row[0] if row else None,
                    reason="dispatcher-entered closing",
                )
                storage.save_audit_entry(
                    conn, job, classify_result, luxor_message=result.text, final_action="Approve",
                )
                st.success("Closing recorded in the audit log.")
        else:
            st.error(result.reason)

# ------------------------------------------------------------ Audit timeline --
with tab_audit:
    st.subheader("Audit timeline")
    ticket_lookup = st.text_input("Ticket/PDL number to look up", key="audit_ticket_input")

    if st.button("Show timeline"):
        timeline = storage.get_audit_timeline(conn, ticket_lookup)
        if not timeline:
            st.info("No events found for this ticket.")
        for event in timeline:
            with st.expander(f"{event['created_at']} — {event['message_type']} — {event['final_action']}"):
                st.json(event)
