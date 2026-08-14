"""WhatsApp dispatch CANDIDATES -- understanding, not canonical state.

WHAT THIS SOLVES
Passive capture already understands WhatsApp traffic, but that
understanding never reached the operator's board. A job posted in a
company intake group was interpreted, correlated and explained in the
Live Operations feed and then went nowhere useful.

WHAT A CANDIDATE IS
A read-model row saying: "a job arrived on WhatsApp, here is what it is,
here is where the existing routing rules say it should go, and here is
the message you would send." It is a PROJECTION over observations that
already exist.

WHAT A CANDIDATE IS NOT
It is not a job. Nothing here writes to `jobs`, `events` or any other
canonical table, and a candidate never acquires a `jobs.status`. The
board shows it as a candidate, clearly separated from committed work,
until a human acts. Fabricating a canonical job just to populate a
column would put invented business truth into the system of record --
the one thing this project has consistently refused to do.

REUSE, NOT REINVENTION
Parsing is `parser.extract_job`. Routing is
`luxor_desk_read_model.resolve_job_routing` -- the same resolution the
Quo dispatch queue uses. The proposed message is
`formatter.format_technician_message`. This module contributes no
parsing, no routing table and no message template of its own; it decides
only whether the evidence is good enough to recommend, and says why.

PURITY
No connection, no SQL, no I/O. The caller resolves the facts; this
module shapes and judges them.
"""
from __future__ import annotations

from typing import List, Optional

CONFIDENCE_EXACT = "EXACT"
CONFIDENCE_STRONG = "STRONG"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"
CONFIDENCE_UNMATCHED = "UNMATCHED"

STATUS_WAITING_HUMAN = "WAITING FOR HUMAN DISPATCH"
STATUS_ALREADY_DISPATCHED = "ALREADY DISPATCHED"
STATUS_CLOSING_OBSERVED = "CLOSING OBSERVED"
STATUS_HUMAN_REVIEW = "HUMAN_REVIEW_NEEDED"
STATUS_MAPPING_REQUIRED = "GROUP MAPPING REQUIRED"

# Routing verification for work that was ALREADY dispatched by a human.
ROUTING_MATCH = "MATCH"
ROUTING_MISMATCH = "MISMATCH"
ROUTING_UNVERIFIED = "UNVERIFIED"


def build_candidate(
    *,
    message_id: str,
    received_at: Optional[str],
    group_name: Optional[str],
    group_confirmed: bool,
    company: Optional[str],
    ticket: Optional[str],
    service: Optional[str],
    city: Optional[str],
    state: Optional[str],
    canonical_area: Optional[str],
    eligible: List[dict],
    missing_fields: Optional[List[str]] = None,
    proposed_message: Optional[str] = None,
    correlated_job_id: Optional[int] = None,
    dispatched_to: Optional[List[dict]] = None,
    closing_seen_in: Optional[List[dict]] = None,
) -> dict:
    """One interpreted WhatsApp intake message -> one dispatch candidate.

    `eligible` is whatever resolve_job_routing() returned: the
    technicians the EXISTING routing rules consider eligible for this
    area/service. This function does not pick a technician by any other
    means, and never invents one.
    """
    missing = list(missing_fields or [])
    why: List[str] = []
    recommended = None
    confidence = CONFIDENCE_UNMATCHED
    status = STATUS_HUMAN_REVIEW

    # 1. An unconfirmed group can never route a job. Naming a technician
    #    from an unconfirmed mapping is exactly how a job gets silently
    #    sent to the wrong person.
    if not group_confirmed:
        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected="Job posted in an unconfirmed group",
            recommended=None, confidence=CONFIDENCE_UNMATCHED,
            why=["This group's mapping is not owner-confirmed",
                 "A job is never routed from an unconfirmed group"],
            status=STATUS_MAPPING_REQUIRED, missing=missing,
            proposed_message=None, correlated_job_id=correlated_job_id)

    # 2. No ticket means nothing can be tracked or correlated later.
    if not ticket:
        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected="Message with no ticket number",
            recommended=None, confidence=CONFIDENCE_AMBIGUOUS,
            why=["No ticket/reference was found, so this cannot be tracked as a job"],
            status=STATUS_HUMAN_REVIEW, missing=missing,
            proposed_message=None, correlated_job_id=correlated_job_id)

    if company:
        why.append(f"Company: {company}")
    if canonical_area:
        why.append(f"Area: {canonical_area}")
    elif city or state:
        why.append(f"Address area: {', '.join(x for x in (city, state) if x)} (not resolved to a service area)")
    if service:
        why.append(f"Service: {service}")

    # 3. Routing. Exactly one eligible technician is a recommendation;
    #    anything else is a human decision, never a coin flip.
    if not canonical_area:
        why.append("No canonical service area resolved -- routing cannot be applied")
        confidence = CONFIDENCE_UNMATCHED
    elif len(eligible) == 1:
        recommended = eligible[0].get("name")
        why.append(f"Routing rule matched exactly one eligible technician for {canonical_area}")
        confidence = CONFIDENCE_EXACT
        status = STATUS_WAITING_HUMAN
    elif len(eligible) > 1:
        names = ", ".join(sorted(t.get("name", "?") for t in eligible))
        why.append(f"{len(eligible)} technicians are eligible for {canonical_area} ({names})")
        why.append("More than one match is a human decision, not a guess")
        confidence = CONFIDENCE_AMBIGUOUS
    else:
        why.append(f"No technician is currently eligible for {canonical_area}"
                   + (f" / {service}" if service else ""))
        confidence = CONFIDENCE_UNMATCHED

    if missing:
        why.append("Missing: " + ", ".join(missing) + " (never invented)")
        # Missing facts do not block a recommendation, but they do stop
        # it being called exact.
        if confidence == CONFIDENCE_EXACT:
            confidence = CONFIDENCE_STRONG

    if correlated_job_id:
        why.append(f"Correlates to existing job #{correlated_job_id} by exact ticket")

    # 4. HAS A HUMAN ALREADY SENT THIS?
    #
    # The intake message is only half the story. If the same ticket later
    # appeared in a confirmed technician DISPATCH group, the dispatcher
    # already forwarded it -- typically within seconds. Offering it again
    # as "waiting for human dispatch" would invite a duplicate send of
    # work that is already on its way, which is worse than showing
    # nothing at all.
    #
    # Observing it downstream is still valuable: it VERIFIES the routing.
    # The job becomes evidence that the rules agree (or disagree) with
    # what the human actually did -- never a request to send it again.
    dispatched_to = dispatched_to or []
    closing_seen_in = closing_seen_in or []
    detected = f"New job {ticket}" + (f" -- {service}" if service else "")

    if dispatched_to or closing_seen_in:
        destinations = dispatched_to or closing_seen_in
        actual = [d.get("technician") or d.get("group_name") for d in destinations]
        actual_names = ", ".join(str(a) for a in actual if a)
        stage = STATUS_CLOSING_OBSERVED if (closing_seen_in and not dispatched_to) else STATUS_ALREADY_DISPATCHED

        routing_match = ROUTING_UNVERIFIED
        if recommended and any(d.get("technician") == recommended for d in destinations):
            routing_match = ROUTING_MATCH
            why.append(f"Already sent to {actual_names} -- matches the routing rule")
        elif recommended and any(d.get("technician") for d in destinations):
            routing_match = ROUTING_MISMATCH
            why.append(f"Already sent to {actual_names}, but the routing rule expected {recommended}")
        else:
            why.append(f"Already seen in {actual_names or 'a technician group'}")

        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected=detected, recommended=recommended, confidence=confidence, why=why,
            status=stage, missing=missing,
            # No prepared message: nothing here is waiting to be sent.
            proposed_message=None, correlated_job_id=correlated_job_id,
            actual_destination=actual_names or None, routing_match=routing_match)

    return _candidate(
        message_id, received_at, group_name, company, ticket, service, city, state,
        detected=detected, recommended=recommended, confidence=confidence, why=why,
        status=status, missing=missing,
        proposed_message=proposed_message if recommended else None,
        correlated_job_id=correlated_job_id)


def _candidate(message_id, received_at, group_name, company, ticket, service, city, state,
               *, detected, recommended, confidence, why, status, missing,
               proposed_message, correlated_job_id,
               actual_destination=None, routing_match=None) -> dict:
    return {
        "message_id": message_id,
        "received_at": received_at,
        "detected": detected,
        "source": company or group_name,
        "group_name": group_name,
        "ticket": ticket,
        "service": service,
        "city": city,
        "state": state,
        "recommended": recommended,
        "why": why,
        "confidence": confidence,
        "proposed_message": proposed_message,
        "status": status,
        "missing_fields": missing,
        "correlated_job_id": correlated_job_id,
        # Where the job ACTUALLY went, when a human already sent it, and
        # whether that agrees with the routing rules.
        "actual_destination": actual_destination,
        "routing_match": routing_match,
        # Stated on every payload. A candidate is understanding, not a
        # job, and no consumer may treat it as committed business state.
        "is_candidate": True,
        "canonical_job": False,
    }
