"""Local fake Quo inbox: scripted, ordered conversation scenarios that drive
the exact same pipeline (`pipeline.process_raw_message`) and the exact same
Approve/Edit/Reject/Handle Manually review screen a real pasted message
would. No real Quo message is ever sent -- this only exercises the local
audit log so the full conversation lifecycle (not just single messages) can
be tested before real Quo API integration.

Two known, intentional gaps are included as named scenarios rather than
hidden: a reminder that repeats a ticket's info in different wording (not
byte-identical, so `storage.classify_incoming` won't classify it as
DUPLICATE) and a cancellation that never says the word "cancel" (so it won't
be classified as CANCELLATION). These document real limitations found during
manual testing 2026-07-27 -- not bugs fixed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

import streamlit as st

from . import storage, updates
from .closing import ClosingInput, render_closing
from .pipeline import process_raw_message

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "simulator" / "scenarios"


@dataclass(frozen=True)
class Step:
    kind: str  # "message" | "status_phrase" | "closing_input"
    filename: str
    label: str


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    steps: List[Step]


SCENARIOS: List[Scenario] = [
    Scenario(
        "new_job_main_leads", "New job -- Main Leads",
        "A single brand-new Main Leads job.",
        [Step("message", "step_1.txt", "New Main Leads job arrives")],
    ),
    Scenario(
        "new_job_agn", "New job -- AGN",
        "A single brand-new AGN job.",
        [Step("message", "step_1.txt", "New AGN job arrives")],
    ),
    Scenario(
        "new_job_lucky", "New job -- Lucky",
        "A single brand-new Lucky job.",
        [Step("message", "step_1.txt", "New Lucky job arrives")],
    ),
    Scenario(
        "reminder_duplicate_agn", "Reminder / duplicate -- AGN (byte-identical)",
        "The same AGN job arrives twice, word for word -- expect the second to classify as DUPLICATE.",
        [
            Step("message", "step_1.txt", "Original AGN job arrives"),
            Step("message", "step_2.txt", "Identical reminder arrives (expect DUPLICATE)"),
        ],
    ),
    Scenario(
        "reminder_worded_differently", "Reminder -- worded differently (known gap)",
        'A real-shaped reminder for the same ticket, reworded rather than byte-identical -- '
        "documents that classify_incoming currently has no dedicated reminder detection for this case.",
        [
            Step("message", "step_1.txt", "Original AGN job arrives"),
            Step("message", "step_2.txt", "Reworded reminder for the same ticket arrives"),
        ],
    ),
    Scenario(
        "corrected_address_lucky", "Corrected job information -- Lucky",
        "A Lucky job arrives, then a corrected address for the same ticket -- expect CORRECTION.",
        [
            Step("message", "step_1.txt", "Original Lucky job arrives"),
            Step("message", "step_2.txt", "Corrected address arrives for the same ticket (expect CORRECTION)"),
        ],
    ),
    Scenario(
        "cancellation_explicit", 'Customer cancellation -- says "cancel"',
        'A Main Leads job arrives, then the customer cancels using the word "cancel" -- expect CANCELLATION.',
        [
            Step("message", "step_1.txt", "Original Main Leads job arrives"),
            Step("message", "step_2.txt", 'Customer cancels, message contains "cancel" (expect CANCELLATION)'),
        ],
    ),
    Scenario(
        "cancellation_worded_differently", "Customer cancellation -- worded differently (known gap)",
        'A real-shaped cancellation that never says the word "cancel" -- documents that today\'s '
        "substring-based CANCELLATION detection would miss it.",
        [
            Step("message", "step_1.txt", "Original Main Leads job arrives"),
            Step("message", "step_2.txt", 'Customer cancels without saying "cancel"'),
        ],
    ),
    Scenario(
        "status_update_agn", "Technician status update -- AGN",
        "An AGN job is dispatched, then the technician reports status three times.",
        [
            Step("message", "step_1_job.txt", "AGN job dispatched"),
            Step("status_phrase", "step_2_status.txt", 'Technician says "Going"'),
            Step("status_phrase", "step_3_status.txt", 'Technician says "ETA 15"'),
            Step("status_phrase", "step_4_status.txt", 'Technician says "Called"'),
        ],
    ),
    Scenario(
        "closing_agn", "Closing information -- AGN",
        "An AGN job is dispatched, then the technician reports the closing amount/payment.",
        [
            Step("message", "step_1_job.txt", "AGN job dispatched"),
            Step("closing_input", "step_2_closing.json", "Technician reports closing amount/payment"),
        ],
    ),
    Scenario(
        "closing_unsupported_main_leads", "Closing information -- Main Leads (expect unsupported)",
        "A Main Leads job is dispatched, then a closing is attempted -- Main Leads has no closing "
        "template, so this should be correctly rejected, not silently produce a message.",
        [
            Step("message", "step_1_job.txt", "Main Leads job dispatched"),
            Step("closing_input", "step_2_closing.json", "Technician reports a closing amount (expect: unsupported)"),
        ],
    ),
]

_SCENARIOS_BY_KEY = {s.key: s for s in SCENARIOS}


def _load_step_content(scenario_key: str, step: Step) -> Any:
    path = SCENARIOS_DIR / scenario_key / step.filename
    text = path.read_text(encoding="utf-8")
    if step.kind == "closing_input":
        return json.loads(text)
    return text


def apply_pending_delivery(conn, vehicle_rules, service_areas) -> None:
    """Call once near the top of app.py, before any tab renders its widgets.

    A "deliver next message" click can't set widget-backed session_state keys
    (raw_text_input, status_text_input, closing_amount, ...) directly -- by
    the time that button's handler runs, tab_process's widgets further up the
    script have already been instantiated this run, and Streamlit forbids
    setting a widget's session_state key after it's been instantiated in the
    same run. So the button handler only stashes what to deliver and calls
    st.rerun(); this function applies it on the *next* run, before those
    widgets exist yet.
    """
    delivery = st.session_state.pop("sim_pending_delivery", None)
    if delivery is None:
        return
    kind, content = delivery["kind"], delivery["content"]

    if kind == "message":
        st.session_state.raw_text_input = content
        result = process_raw_message(content, vehicle_rules, service_areas, conn, st.session_state.daily_rules)
        st.session_state.current_job = result.job
        st.session_state.current_classify = result.classify_result
        st.session_state.current_profile = result.profile
        st.session_state.current_decision = result.decision
        st.session_state.current_luxor_message = result.luxor_message
        st.session_state.edit_message = result.luxor_message
        # Who produced this delivery -- defaults to "simulator" since that's
        # this module's own delivery path; quo_ingest tags its own real,
        # read-only deliveries with "shadow_mode_quo_live" explicitly.
        st.session_state.current_channel = delivery.get("channel", "simulator")
        # Links this review back to its durable quo_pending_messages row
        # (None for simulator/manual deliveries) so _finalize() in app.py
        # can mark that row "actioned" once the audit save succeeds.
        st.session_state.current_quo_pending_message_id = delivery.get("quo_message_id")

    elif kind == "status_phrase":
        st.session_state.status_text_input = content
        st.session_state.status_reply = updates.map_status_to_reply(content)

    elif kind == "closing_input":
        st.session_state.closing_amount = content.get("amount") or ""
        st.session_state.closing_payment = content.get("payment_method") or ""
        st.session_state.closing_parts = content.get("parts_amount") or ""
        if st.session_state.current_job is not None:
            closing_input = ClosingInput(
                amount=content.get("amount") or "",
                payment_method=content.get("payment_method") or "",
                parts_amount=content.get("parts_amount") or None,
            )
            st.session_state.last_closing_result = render_closing(st.session_state.current_job, closing_input)


def render_fake_inbox_tab(conn, vehicle_rules, service_areas) -> None:
    st.subheader("Fake Quo inbox (local simulator)")
    st.caption(
        "Scripted conversations that exercise the same pipeline and the same "
        "Approve/Edit/Reject/Handle Manually buttons as a real pasted message -- "
        "nothing here is ever sent to the real Quo."
    )

    if "sim_scenario_key" not in st.session_state:
        st.session_state.sim_scenario_key = SCENARIOS[0].key
    if "sim_step_index" not in st.session_state:
        st.session_state.sim_step_index = 0

    selected_key = st.selectbox(
        "Scenario",
        options=list(_SCENARIOS_BY_KEY.keys()),
        format_func=lambda k: _SCENARIOS_BY_KEY[k].title,
        key="sim_scenario_key",
    )
    if st.session_state.get("sim_last_scenario_key") != selected_key:
        st.session_state.sim_step_index = 0
        st.session_state.sim_last_scenario_key = selected_key

    scenario = _SCENARIOS_BY_KEY[selected_key]
    st.write(scenario.description)

    step_index = st.session_state.sim_step_index
    total = len(scenario.steps)

    if step_index >= total:
        st.success(f"All {total} step(s) of this scenario have been delivered. Reset to run it again.")
    else:
        next_step = scenario.steps[step_index]
        st.write(f"**Next -- step {step_index + 1} of {total}: {next_step.label}**")
        with st.expander("Preview what will be delivered"):
            content = _load_step_content(scenario.key, next_step)
            if next_step.kind == "closing_input":
                st.json(content)
            else:
                st.code(content)

    col1, col2 = st.columns(2)
    if col1.button("Deliver next message", disabled=step_index >= total, type="primary"):
        step = scenario.steps[step_index]
        st.session_state.sim_pending_delivery = {
            "kind": step.kind, "content": _load_step_content(scenario.key, step),
        }
        st.session_state.sim_step_index = step_index + 1
        st.rerun()
    if col2.button("Reset scenario"):
        st.session_state.sim_step_index = 0
