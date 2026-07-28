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

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import streamlit as st

from dispatcher import quo_ingest, simulator, storage, updates
from dispatcher.closing import ClosingInput, render_closing
from dispatcher.models import ExtractionStatus, Source
from dispatcher.parser import load_vehicle_rules
from dispatcher.pipeline import process_raw_message
from dispatcher.rules.daily import parse_daily_rules
from dispatcher.rules.hours import check_business_hours
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
    ("current_channel", "manual"),
    ("current_quo_pending_message_id", None),
):
    if key not in st.session_state:
        st.session_state[key] = default

conn = get_db()
vehicle_rules = get_vehicle_rules()
service_areas = get_service_areas()

# Must run before any tab below instantiates its widgets -- see the
# docstring on apply_pending_delivery for why.
simulator.apply_pending_delivery(conn, vehicle_rules, service_areas)

st.title("Luxor Locksmith — AI Dispatcher (Phase 1)")
st.caption(
    "Nothing on this screen is ever sent to Quo, WhatsApp, or a technician automatically. "
    "Every suggestion requires your approval, and even then you copy the text yourself."
)

_hours = check_business_hours()
_hours_label = f"Business hours: {'OPEN' if _hours.within_hours else 'CLOSED'} — {_hours.local_time.strftime('%A %H:%M %Z')}"
(st.success if _hours.within_hours else st.error)(_hours_label)

tab_process, tab_daily, tab_status, tab_closing, tab_audit, tab_simulator, tab_quo_live, tab_jobs, tab_technicians = st.tabs(
    ["Process Job", "Daily Rules", "Status Update", "Closing", "Audit Timeline",
     "Fake Inbox (Simulator)", "Quo Inbox (Live)", "Jobs", "Technicians"]
)


def _render_event_timeline(timeline) -> None:
    """Shared by the Audit Timeline tab and the new Jobs tab -- both show
    the exact same full, unmasked event detail from the same events table;
    only how a job/ticket is looked up differs between the two."""
    if not timeline:
        st.info("No events found.")
    for event in timeline:
        channel = event.get("channel") or "manual"
        badge = "\U0001F512 SHADOW MODE — NOT SENT — " if channel == quo_ingest.SHADOW_MODE_CHANNEL else ""
        with st.expander(f"{badge}{event['created_at']} — {event['message_type']} — {event['final_action']}"):
            st.json(event)

# ---------------------------------------------------------------- Process --
with tab_process:
    st.subheader("1. Paste the raw Quo message")
    raw_text = st.text_area("Raw message", height=220, key="raw_text_input")

    if st.button("Process", type="primary"):
        result = process_raw_message(raw_text, vehicle_rules, service_areas, conn, st.session_state.daily_rules)

        st.session_state.current_job = result.job
        st.session_state.current_classify = result.classify_result
        st.session_state.current_profile = result.profile
        st.session_state.current_decision = result.decision
        st.session_state.current_luxor_message = result.luxor_message
        # A message typed/pasted directly here is always "manual" -- only
        # simulator/Quo-live deliveries (via apply_pending_delivery) tag a
        # different channel.
        st.session_state.current_channel = "manual"
        # Not linked to any durable Quo queue row -- clears any stale
        # pointer left over from a previously reviewed Quo-live message.
        st.session_state.current_quo_pending_message_id = None
        # The "edit_message" text_area below is keyed, so Streamlit ignores
        # its value= on rerun once session_state already holds that key --
        # without this, processing a second job leaves the box showing the
        # previous job's message. Overwrite it explicitly here.
        st.session_state.edit_message = result.luxor_message

    job = st.session_state.current_job
    if job is not None:
        classify_result = st.session_state.current_classify
        decision = st.session_state.current_decision
        luxor_message = st.session_state.current_luxor_message

        if st.session_state.current_channel == quo_ingest.SHADOW_MODE_CHANNEL:
            st.info(
                "🔒 **SHADOW MODE** — this message was polled read-only from the real Quo account. "
                "Nothing has been or will be sent, replied to, acknowledged, or modified in Quo. "
                "Approve/Edit/Handle Manually below only record a local comparison record."
            )

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
        st.write(f"Service area: {decision.service_area or '(not confirmed)'}")
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
            channel = st.session_state.current_channel
            storage.save_audit_entry(
                conn, job, classify_result, decision=decision,
                suggested_quo_reply=decision.quo_reply_suggestion,
                luxor_message=luxor_message,
                dispatcher_edits=edits or (edit_notes or None),
                final_action=action,
                channel=channel,
            )
            # Only reached if save_audit_entry() succeeded above -- a durable
            # queue row is marked actioned only once a real audit event
            # exists for it, never before.
            pending_message_id = st.session_state.current_quo_pending_message_id
            if pending_message_id:
                storage.mark_quo_pending_message_actioned(conn, pending_message_id)
                st.session_state.current_quo_pending_message_id = None
            if channel == quo_ingest.SHADOW_MODE_CHANNEL:
                st.success(f"Recorded locally as SHADOW MODE — {action}. Nothing was sent to Quo.")
            else:
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
    st.subheader("Today's random exceptions (optional)")
    st.caption(
        "Areas are active by default and recurring weekly days off are already configured "
        "(rules/service_areas.yaml) — you don't need to enter those here. Only use this for "
        "things that vary randomly today: a technician taking an unplanned day off or being "
        "busy, a one-off area being temporarily unavailable, or a temporary vehicle/service "
        "restriction (e.g. \"no BMW today\"). Leave this empty and jobs still process normally."
    )
    daily_text = st.text_area("Today's exceptions", height=200, key="daily_text_input")
    shift_hours = st.number_input("Applies for how many hours", min_value=1, max_value=24, value=12)

    if st.button("Parse today's exceptions"):
        st.session_state.daily_rules = parse_daily_rules(daily_text, shift_hours=int(shift_hours))

    daily = st.session_state.daily_rules
    if daily is not None:
        st.text(daily.summary())
        if daily.needs_confirmation:
            st.warning("Some lines could not be parsed — confirm these manually before relying on today's rules.")
        st.caption("These exceptions apply to every job processed in the Process Job tab until they expire.")

# ------------------------------------------------------------ Status update --
with tab_status:
    st.subheader("Technician status update")
    st.caption('Also used to answer company questions like "Any update?" or "Did anyone call?"')
    st.caption("Uses the ticket from the job most recently processed in the Process Job tab.")
    status_text = st.text_input("Technician's status (paste exactly, confirmed by you)", key="status_text_input")

    if st.button("Map status"):
        st.session_state.status_reply = updates.map_status_to_reply(status_text)

    reply = st.session_state.status_reply
    if reply is not None:
        if reply.confirmed:
            st.success(f"Suggested reply: {reply.reply}")
            if st.session_state.current_job is None:
                st.error("Process a job in the Process Job tab first -- a status update needs a ticket to attach to.")
            elif st.button("Approve status reply"):
                job = st.session_state.current_job
                row = conn.execute(
                    "SELECT id FROM jobs WHERE ticket_number = ? AND source = ?",
                    (job.ticket_number.value, job.source.value),
                ).fetchone()
                classify_result = ClassifyResult(
                    MessageType.STATUS, existing_job_id=row[0] if row else None,
                    reason="dispatcher-confirmed technician status update",
                )
                storage.save_audit_entry(
                    conn, job, classify_result,
                    luxor_message=reply.reply, dispatcher_edits=status_text, final_action="Approve",
                    channel=st.session_state.current_channel,
                )
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
                    "SELECT id FROM jobs WHERE ticket_number = ? AND source = ?",
                    (job.ticket_number.value, job.source.value),
                ).fetchone()
                classify_result = ClassifyResult(
                    MessageType.CLOSING, existing_job_id=row[0] if row else None,
                    reason="dispatcher-entered closing",
                )
                storage.save_audit_entry(
                    conn, job, classify_result, luxor_message=result.text, final_action="Approve",
                    channel=st.session_state.current_channel,
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
        _render_event_timeline(timeline)

# --------------------------------------------------------- Fake inbox (sim) --
with tab_simulator:
    simulator.render_fake_inbox_tab(conn, vehicle_rules, service_areas)

# ------------------------------------------------------- Quo inbox (live) --
with tab_quo_live:
    quo_ingest.render_quo_live_tab(conn)

# ------------------------------------------------------------------- Jobs --
with tab_jobs:
    st.subheader("Jobs")
    st.caption(
        "One row per source + ticket number, derived from the same append-only events table as the "
        "Audit Timeline tab. Full message content, phone numbers, and addresses are never shown in "
        "this list -- select a job below to see its full timeline in the same authorized review view."
    )

    fcol1, fcol2, fcol3, fcol4 = st.columns(4)
    jobs_ticket_search = fcol1.text_input("Ticket search", key="jobs_ticket_search")
    jobs_source_filter = fcol2.selectbox(
        "Source", options=["(all)"] + [s.value for s in Source], key="jobs_source_filter",
    )
    jobs_status_filter = fcol3.selectbox(
        "Status", options=["(all)"] + sorted(storage.VALID_JOB_STATUSES), key="jobs_status_filter",
    )
    jobs_since_date = fcol4.date_input("Active since", value=None, key="jobs_since_date")

    jobs = storage.list_jobs(
        conn,
        ticket_search=jobs_ticket_search or None,
        source=None if jobs_source_filter == "(all)" else jobs_source_filter,
        status=None if jobs_status_filter == "(all)" else jobs_status_filter,
        since=jobs_since_date.isoformat() if jobs_since_date else None,
    )
    st.write(f"**{len(jobs)} job(s).**")
    st.table(
        [
            {
                "Source": j["source"],
                "Ticket": j["ticket_number"],
                "Status": j["status"],
                "Created": j["created_at"],
                "Last activity": j["last_event_at"] or j["updated_at"],
                "Latest action": j["latest_action"] or "(none)",
                "Assigned": j["assigned_technician_name"] or "(unassigned)",
            }
            for j in jobs
        ]
    )

    if jobs:
        job_options = {f"{j['source']} | {j['ticket_number']} ({j['status']})": j["id"] for j in jobs}
        selected_job_label = st.selectbox(
            "Select a job to view its full timeline", options=list(job_options.keys()), key="jobs_selected_job",
        )
        selected_job_id = job_options[selected_job_label]
        selected_job_row = next((j for j in jobs if j["id"] == selected_job_id), None)

        if st.button("Show job timeline", key="jobs_show_timeline"):
            timeline = storage.get_audit_timeline_by_job_id(conn, selected_job_id)
            _render_event_timeline(timeline)

        st.subheader("Assign technician")
        st.caption(
            "Never auto-assigns and never invents availability, skill match, or acceptance -- "
            "eligibility is re-checked fresh every time against the technician's active status, "
            "configured coverage areas (re-derived via the same service-area lookup rules.decide() "
            "already uses), and any explicit skills/vehicle restrictions. Ineligible or ambiguous "
            "combinations are recorded as Handle Manually, never silently allowed."
        )
        current_assignment = selected_job_row["assigned_technician_name"] if selected_job_row else None
        st.write(f"Current assignment: **{current_assignment or '(unassigned)'}**")

        active_technicians = storage.list_technicians(conn, active_only=True)
        assign_options = ["(remove assignment)"] + [t["name"] for t in active_technicians]
        chosen_technician = st.selectbox("Technician", options=assign_options, key="jobs_assign_technician_select")

        if st.button("Apply assignment", key="jobs_apply_assignment"):
            if chosen_technician == "(remove assignment)":
                assignment_result = storage.assign_technician(conn, selected_job_id, None, service_areas)
            else:
                chosen_id = next(t["id"] for t in active_technicians if t["name"] == chosen_technician)
                assignment_result = storage.assign_technician(conn, selected_job_id, chosen_id, service_areas)
            if assignment_result.outcome == "handle_manually":
                st.error(f"Could not assign -- Handle Manually: {assignment_result.reason}")
            else:
                st.success(f"{assignment_result.outcome.capitalize()}: {assignment_result.reason}")
                if assignment_result.dispatch_group or assignment_result.closing_group:
                    st.info(
                        f"Use dispatch group: {assignment_result.dispatch_group or '(not set)'} | "
                        f"closing group: {assignment_result.closing_group or '(not set)'}"
                    )

# ------------------------------------------------------------ Technicians --
with tab_technicians:
    st.subheader("Technicians")
    st.caption(
        "A minimal roster: name, active/inactive, the service areas they cover (by area name, e.g. "
        "\"Charlotte\" -- matching rules/service_areas.yaml, never a WhatsApp group name), and optional "
        "explicit skills/vehicle restrictions. Dispatch group and closing group are opaque destination "
        "strings for this one technician (e.g. their WhatsApp group name) -- nothing in this app parses "
        "or reasons about their wording; they're only ever shown back to you once this technician is "
        "selected for a job."
    )

    tech_status_message = st.session_state.pop("tech_status_message", None)
    if tech_status_message:
        st.success(tech_status_message)

    st.write("Add a technician")
    new_tech_name = st.text_input("Name", key="tech_new_name")
    new_tech_areas = st.text_input("Service areas covered (comma-separated area names)", key="tech_new_areas")
    new_tech_skills = st.text_input("Skills (optional, comma-separated)", key="tech_new_skills")
    new_tech_vehicle_restrictions = st.text_input(
        "Vehicle restrictions (optional, comma-separated)", key="tech_new_vehicle_restrictions",
    )
    new_tech_dispatch_group = st.text_input(
        "Dispatch group (opaque destination string, e.g. a WhatsApp group name)", key="tech_new_dispatch_group",
    )
    new_tech_closing_group = st.text_input(
        "Closing group (opaque destination string, optional)", key="tech_new_closing_group",
    )
    if st.button("Add technician", key="tech_add_button"):
        if not new_tech_name.strip():
            st.error("Name is required.")
        else:
            areas = [a.strip() for a in new_tech_areas.split(",") if a.strip()]
            skills = [s.strip() for s in new_tech_skills.split(",") if s.strip()]
            vehicle_restrictions = [v.strip() for v in new_tech_vehicle_restrictions.split(",") if v.strip()]
            try:
                storage.add_technician(
                    conn, new_tech_name.strip(), areas, skills or None, vehicle_restrictions or None,
                    dispatch_group=new_tech_dispatch_group.strip() or None,
                    closing_group=new_tech_closing_group.strip() or None,
                )
                st.success(f"Added {new_tech_name.strip()}.")
            except Exception as exc:
                st.error(f"Could not add technician (name may already exist): {exc}")

    technicians = storage.list_technicians(conn)
    st.write(f"**{len(technicians)} technician(s).**")
    st.table(
        [
            {
                "Name": t["name"],
                "Active": "Yes" if t["active"] else "No",
                "Service areas": ", ".join(t["areas"]) or "(none configured)",
                "Skills": ", ".join(t["skills"]) or "(none -- no restriction beyond global rules)",
                "Vehicle restrictions": ", ".join(t["vehicle_restrictions"]) or "(none)",
                "Dispatch group": t["dispatch_group"] or "(not set)",
                "Closing group": t["closing_group"] or "(not set)",
            }
            for t in technicians
        ]
    )

    if technicians:
        tech_toggle_name = st.selectbox(
            "Toggle active status for", options=[t["name"] for t in technicians], key="tech_toggle_select",
        )
        if st.button("Toggle active status", key="tech_toggle_button"):
            selected_tech = next(t for t in technicians if t["name"] == tech_toggle_name)
            storage.set_technician_active(conn, selected_tech["id"], not selected_tech["active"])
            new_state = "active" if not selected_tech["active"] else "inactive"
            # The roster table above was already rendered earlier in this
            # same script pass (with the pre-toggle data) -- st.rerun() is
            # needed so the next pass re-fetches technicians and shows the
            # updated state immediately, same pattern as
            # simulator.py's sim_pending_delivery. st.success() itself
            # wouldn't survive the rerun, so the message is stashed and
            # shown at the top of this tab instead.
            st.session_state.tech_status_message = f"{tech_toggle_name} is now {new_state}."
            st.rerun()
