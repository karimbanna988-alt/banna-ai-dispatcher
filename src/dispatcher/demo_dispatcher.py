"""Owner-facing demo: shows what the dispatcher WOULD do, and why.

PURPOSE
The production shadow system answers "can it classify messages?". This
answers the harder question the owner actually cares about: "can it
behave like a locksmith dispatcher?" -- read a job, decide a route,
explain the reasoning, recognize the technician's reply, and carry the
job through a lifecycle.

ISOLATION -- read this before changing anything here
This module is PURE. It holds no database connection, issues no SQL,
opens no file, and sends nothing. It takes already-fetched demo
observations and returns derived reasoning. It therefore CANNOT write a
real job, mutate a real technician, or contaminate business truth, no
matter how it is driven.

The demo lifecycle it computes is its own in-memory projection. It is
deliberately NOT the production `jobs.status` state machine
(storage.derive_job_status) and never calls it: production status
changes require a human action on a real job, and a demo must never be
able to manufacture one.

WHAT IS REAL vs DEMO
Real (reused, not reimplemented): parser.extract_job for field
extraction, and the same acknowledgement/decline vocabulary
technician_ack defines. So the parsing the owner sees is the parsing
production uses.
Demo (self-contained): the company, area and technician identities, and
the routing rule between them. These are fabricated on purpose so the
demo can never touch a real technician's configuration or imply a real
customer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from dispatcher import message_patterns, technician_ack
from dispatcher.parser import extract_job

# Fabricated identities. Deliberately NOT looked up in the technicians
# table -- a demo must not reference, or appear to commit, a real person.
DEMO_COMPANY = "Demo Company"
DEMO_AREA = "Demo Area"
DEMO_TECHNICIAN = "Demo Technician"

# Demo-only lifecycle. Mirrors the operational shape the owner described
# (new -> assigned -> en route -> completed) without touching the
# production status machine.
STATE_NEW = "NEW"
STATE_ASSIGNED = "ASSIGNED"
STATE_EN_ROUTE = "EN_ROUTE"
STATE_COMPLETED = "COMPLETED"
STATE_NEEDS_REVIEW = "HUMAN_REVIEW_NEEDED"

CONFIDENCE_EXACT = "EXACT"
CONFIDENCE_STRONG = "STRONG"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"

ROLE_COMPANY = "DEMO_COMPANY_CHAT"
ROLE_TECHNICIAN = "DEMO_TECHNICIAN_CHAT"


@dataclass
class DemoDecision:
    """One reasoning step, in the exact shape the owner asked to see."""
    detected: str
    decision: str
    why: List[str] = field(default_factory=list)
    confidence: Optional[str] = None
    proposed_action: Optional[str] = None
    proposed_message: Optional[str] = None
    state_before: Optional[str] = None
    state_after: Optional[str] = None
    ticket: Optional[str] = None
    missing_fields: List[str] = field(default_factory=list)
    # True whenever a human must decide before anything could proceed.
    needs_human: bool = False


@dataclass
class DemoJob:
    ticket: Optional[str] = None
    service_type: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    state: str = STATE_NEW
    technician: Optional[str] = None
    closing_amount: Optional[str] = None
    closing_payment_method: Optional[str] = None


def _company_intake(text: str, job: DemoJob, vehicle_rules) -> DemoDecision:
    parsed = extract_job(text, vehicle_rules)
    core = {
        "ticket": parsed.ticket_number.value,
        "phone": parsed.phone.value,
        "address": parsed.address.value,
        "service": parsed.service_type.value,
    }
    missing = [k for k, v in core.items() if not v]

    if not parsed.ticket_number.value:
        return DemoDecision(
            detected="Message in the company chat with no ticket number",
            decision="Cannot route this yet",
            why=["No ticket/reference was found, so this cannot be tracked as a job",
                 f"Missing: {', '.join(missing)}"],
            confidence=CONFIDENCE_AMBIGUOUS,
            proposed_action="HUMAN_REVIEW_NEEDED",
            state_before=job.state, state_after=job.state,
            missing_fields=missing, needs_human=True,
        )

    job.ticket = parsed.ticket_number.value
    job.service_type = parsed.service_type.value
    job.address = parsed.address.value
    job.phone = parsed.phone.value

    why = [
        f"Company = {DEMO_COMPANY} (this chat is the demo company's job source)",
        f"Area = {DEMO_AREA}",
        f"Service = {parsed.service_type.value or 'not stated'}",
    ]
    if missing:
        # Route anyway, but never hide what is unknown.
        why.append(f"Incomplete: {', '.join(missing)} not stated -- flagged, never guessed")
        confidence = CONFIDENCE_STRONG
    else:
        why.append("Routing rule matched exactly: demo company + demo area -> Demo Technician")
        confidence = CONFIDENCE_EXACT

    job.technician = DEMO_TECHNICIAN
    proposed = _technician_message(job)
    return DemoDecision(
        detected=f"New job {parsed.ticket_number.value}"
                 + (f" -- {parsed.service_type.value}" if parsed.service_type.value else ""),
        decision=f"Dispatch to {DEMO_TECHNICIAN}",
        why=why, confidence=confidence,
        proposed_action="SEND_TO_TECHNICIAN",
        proposed_message=proposed,
        state_before=job.state, state_after=job.state,
        ticket=job.ticket, missing_fields=missing,
        needs_human=bool(missing),
    )


def _technician_message(job: DemoJob) -> str:
    """The message a dispatcher would send to the technician. Only ever
    contains fields actually extracted -- a missing field is shown as
    'not stated', never invented."""
    lines = [f"Ticket: {job.ticket}"]
    lines.append(f"Service: {job.service_type or 'not stated'}")
    lines.append(f"Address: {job.address or 'not stated'}")
    lines.append(f"Phone: {job.phone or 'not stated'}")
    return "\n".join(lines)


def _technician_reply(text: str, job: DemoJob) -> DemoDecision:
    normalized = (text or "").strip().lower()
    before = job.state

    if job.ticket is None:
        return DemoDecision(
            detected="Technician replied, but no demo job is open",
            decision="Cannot correlate this reply",
            why=["No job has been dispatched in this demo yet, so there is nothing to attach it to"],
            confidence=CONFIDENCE_AMBIGUOUS,
            proposed_action="HUMAN_REVIEW_NEEDED",
            state_before=before, state_after=before, needs_human=True,
        )

    if normalized in technician_ack.ACKNOWLEDGEMENT_TEXTS:
        job.state = STATE_ASSIGNED
        return DemoDecision(
            detected="Technician accepted the job",
            decision="Mark accepted and tell the company it is assigned",
            why=[f"Reply {text.strip()!r} is an acknowledgement",
                 f"Exactly one demo job is open ({job.ticket}), so the match is unambiguous"],
            confidence=CONFIDENCE_EXACT,
            proposed_action="UPDATE_COMPANY",
            proposed_message=f"{job.ticket}: technician assigned and on the job.",
            state_before=before, state_after=job.state, ticket=job.ticket,
        )

    if normalized in technician_ack.DECLINE_TEXTS:
        job.state = STATE_NEEDS_REVIEW
        return DemoDecision(
            detected="Technician declined the job",
            decision="Reassign -- this needs a human",
            why=[f"Reply {text.strip()!r} is a decline",
                 "No second technician exists in this demo, so a human must choose"],
            confidence=CONFIDENCE_EXACT,
            proposed_action="HUMAN_REVIEW_NEEDED",
            state_before=before, state_after=job.state, ticket=job.ticket, needs_human=True,
        )

    amount, method = _closing_amount(text)
    if amount:
        job.closing_amount, job.closing_payment_method = amount, method
        job.state = STATE_COMPLETED
        return DemoDecision(
            detected=f"Closing report -- ${amount} {method}",
            decision="Record the closing and send it to the company",
            why=[f"Amount ${amount} and payment method {method!r} were stated explicitly",
                 "Nothing was inferred -- only what the message actually said"],
            confidence=CONFIDENCE_EXACT,
            proposed_action="SEND_COMPANY_CLOSING",
            proposed_message=f"{job.ticket}: completed. ${amount} {method}.",
            state_before=before, state_after=job.state, ticket=job.ticket,
        )

    if message_patterns.says_job_done(text):
        job.state = STATE_NEEDS_REVIEW
        return DemoDecision(
            detected="Technician says the job is done, but stated no amount",
            decision="Cannot close it -- the amount is missing",
            why=["A closing needs an amount and a payment method",
                 "Neither was stated, and neither may be invented"],
            confidence=CONFIDENCE_AMBIGUOUS,
            proposed_action="HUMAN_REVIEW_NEEDED",
            state_before=before, state_after=job.state, ticket=job.ticket,
            missing_fields=["amount", "payment_method"], needs_human=True,
        )

    if message_patterns.is_en_route(text) or message_patterns.stated_eta(text):
        job.state = STATE_EN_ROUTE
        eta = message_patterns.stated_eta(text)
        why = ["Technician reported travelling to the job"]
        if eta:
            why.append(f"Stated time: {eta}")
        return DemoDecision(
            detected="Technician is on the way" + (f" ({eta})" if eta else ""),
            decision="Update the company with the ETA",
            why=why, confidence=CONFIDENCE_EXACT,
            proposed_action="UPDATE_COMPANY",
            proposed_message=f"{job.ticket}: technician en route"
                             + (f", {eta}." if eta else "."),
            state_before=before, state_after=job.state, ticket=job.ticket,
        )

    return DemoDecision(
        detected="Technician update",
        decision="Pass it to a human -- no specific action is implied",
        why=["The message does not match an acceptance, decline, ETA, or closing"],
        confidence=CONFIDENCE_AMBIGUOUS,
        proposed_action="HUMAN_REVIEW_NEEDED",
        state_before=before, state_after=job.state, ticket=job.ticket, needs_human=True,
    )


def _closing_amount(text: str):
    """Delegates to the shared implementation -- the demo must recognize
    a closing exactly the way production does."""
    return message_patterns.extract_closing_amount(text)


def reason(role: str, text: str, job: DemoJob, vehicle_rules) -> DemoDecision:
    """One demo message in, one explained decision out. Mutates only the
    caller's DemoJob -- never any persistent store."""
    if role == ROLE_COMPANY:
        return _company_intake(text, job, vehicle_rules)
    if role == ROLE_TECHNICIAN:
        return _technician_reply(text, job)
    return DemoDecision(
        detected="Message from an unrecognized demo chat",
        decision="Ignore",
        why=["Only the two registered demo chats are interpreted"],
        proposed_action="HUMAN_REVIEW_NEEDED",
        state_before=job.state, state_after=job.state, needs_human=True,
    )


def run_sequence(messages: List[tuple], vehicle_rules) -> tuple:
    """Replays [(role, text), ...] and returns (decisions, final job).
    Used by both the live console and the tests."""
    job = DemoJob()
    decisions = [reason(role, text, job, vehicle_rules) for role, text in messages]
    return decisions, job
