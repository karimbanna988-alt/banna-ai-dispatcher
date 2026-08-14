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

from datetime import datetime, timezone
from typing import List, Optional, Tuple

CONFIDENCE_EXACT = "EXACT"
CONFIDENCE_STRONG = "STRONG"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"
CONFIDENCE_UNMATCHED = "UNMATCHED"

STATUS_WAITING_HUMAN = "WAITING FOR HUMAN DISPATCH"
STATUS_ALREADY_DISPATCHED = "ALREADY DISPATCHED"
STATUS_REPOST_AFTER_DISPATCH = "REPOST AFTER PRIOR DISPATCH"
STATUS_CLOSING_OBSERVED = "CLOSING OBSERVED"
STATUS_HUMAN_REVIEW = "HUMAN_REVIEW_NEEDED"
STATUS_MAPPING_REQUIRED = "GROUP MAPPING REQUIRED"

# Routing verification for work that was ALREADY dispatched by a human.
ROUTING_MATCH = "MATCH"
ROUTING_MISMATCH = "MISMATCH"
ROUTING_UNVERIFIED = "UNVERIFIED"


def _parse_ts(value) -> Optional[datetime]:
    """A real parsed instant, or None when the value cannot be trusted.

    Lexical comparison is wrong across offsets: "2026-08-15T12:00:09+00:00"
    is genuinely later than "2026-08-15T13:00:00+01:00" but sorts earlier
    as a string. Naive values are read as UTC so they remain comparable.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _sequence(intake_at, occurrences: List[dict]) -> Tuple[str, List[dict]]:
    """Where these occurrences sit relative to the intake message.

    Returns one of "after" / "before" / "undetermined", plus the
    occurrences that justified it.

    Precedence is a safety decision:
      * any occurrence strictly AFTER the intake wins -- the job is
        demonstrably on its way, and suppressing it is correct;
      * otherwise any occurrence we cannot place (missing, malformed, or
        the very same instant) forces a human decision, because guessing
        here means guessing whether a job has already been sent;
      * only then does a strictly BEFORE occurrence mean a repost.
    """
    base = _parse_ts(intake_at)
    after, before, unknown = [], [], []
    for occurrence in occurrences:
        stamp = _parse_ts(occurrence.get("received_at")) if base else None
        if base is None or stamp is None or stamp == base:
            unknown.append(occurrence)
        elif stamp > base:
            after.append(occurrence)
        else:
            before.append(occurrence)
    if after:
        return "after", after
    if unknown:
        return "undetermined", unknown
    if before:
        return "before", before
    return "none", []


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
    ambiguous_source_matches: Optional[List[dict]] = None,
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

    # 4. HAS A HUMAN ALREADY SENT THIS -- AND WHEN?
    #
    # The intake message is only half the story. If the same scoped
    # ticket later appeared in a confirmed technician DISPATCH group, the
    # dispatcher already forwarded it, typically within seconds, and
    # offering it again would invite a duplicate send.
    #
    # But ORDER matters. A dispatch from yesterday says nothing about an
    # intake that arrived today: that is a repost, and a repost may mean
    # no answer, failed coverage, a reminder, or a genuine redispatch
    # request. Treating it as "already handled" silently drops real work,
    # so it goes to a human instead -- with nothing prepared to send,
    # because we do not know yet what it means.
    dispatched_to = dispatched_to or []
    closing_seen_in = closing_seen_in or []
    ambiguous_source_matches = ambiguous_source_matches or []
    detected = f"New job {ticket}" + (f" -- {service}" if service else "")

    def _verified(destinations):
        names = ", ".join(str(d.get("technician") or d.get("group_name"))
                          for d in destinations if (d.get("technician") or d.get("group_name")))
        if recommended and any(d.get("technician") == recommended for d in destinations):
            return names, ROUTING_MATCH
        if recommended and any(d.get("technician") for d in destinations):
            return names, ROUTING_MISMATCH
        return names, ROUTING_UNVERIFIED

    for occurrences, after_status in ((dispatched_to, STATUS_ALREADY_DISPATCHED),
                                      (closing_seen_in, STATUS_CLOSING_OBSERVED)):
        if not occurrences:
            continue
        order, relevant = _sequence(received_at, occurrences)
        names, routing_match = _verified(relevant or occurrences)

        if order == "after":
            why.append(f"Already sent to {names} after this message"
                       if routing_match != ROUTING_MISMATCH
                       else f"Already sent to {names}, but the routing rule expected {recommended}")
            return _candidate(
                message_id, received_at, group_name, company, ticket, service, city, state,
                detected=detected, recommended=recommended, confidence=confidence, why=why,
                status=after_status, missing=missing,
                proposed_message=None, correlated_job_id=correlated_job_id,
                actual_destination=names or None, routing_match=routing_match,
                needs_human_review=False)

        if order == "before":
            why.append(f"This ticket was already sent to {names} BEFORE this message arrived")
            why.append("A repost may mean no answer, failed coverage, a reminder or a "
                       "genuine redispatch request -- the earlier dispatch does not settle it")
            return _candidate(
                message_id, received_at, group_name, company, ticket, service, city, state,
                detected=detected, recommended=recommended, confidence=confidence, why=why,
                status=STATUS_REPOST_AFTER_DISPATCH, missing=missing,
                proposed_message=None, correlated_job_id=correlated_job_id,
                actual_destination=names or None, routing_match=routing_match,
                needs_human_review=True)

        why.append(f"This ticket also appears in {names or 'a technician group'}, but the "
                   "order of events cannot be established from the timestamps")
        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected=detected, recommended=recommended, confidence=confidence, why=why,
            status=STATUS_HUMAN_REVIEW, missing=missing,
            proposed_message=None, correlated_job_id=correlated_job_id,
            actual_destination=names or None, routing_match=ROUTING_UNVERIFIED,
            needs_human_review=True)

    # 5. Same ticket string seen downstream, but the company behind that
    #    sighting could not be resolved. We cannot claim it is the same
    #    job -- and we must not offer this as an ordinary new job either.
    if ambiguous_source_matches:
        why.append("The same ticket appears in a technician group whose company could not be "
                   "resolved -- identity cannot be confirmed across companies")
        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected=detected, recommended=recommended, confidence=confidence, why=why,
            status=STATUS_HUMAN_REVIEW, missing=missing,
            proposed_message=None, correlated_job_id=correlated_job_id,
            actual_destination=None, routing_match=ROUTING_UNVERIFIED,
            needs_human_review=True)

    return _candidate(
        message_id, received_at, group_name, company, ticket, service, city, state,
        detected=detected, recommended=recommended, confidence=confidence, why=why,
        status=status, missing=missing,
        proposed_message=proposed_message if recommended else None,
        correlated_job_id=correlated_job_id)


def _candidate(message_id, received_at, group_name, company, ticket, service, city, state,
               *, detected, recommended, confidence, why, status, missing,
               proposed_message, correlated_job_id,
               actual_destination=None, routing_match=None,
               needs_human_review=None) -> dict:
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
        # True whenever a human must look before anything can proceed.
        "needs_human_review": (needs_human_review if needs_human_review is not None
                               else status in (STATUS_HUMAN_REVIEW, STATUS_MAPPING_REQUIRED)),
        # Stated on every payload. A candidate is understanding, not a
        # job, and no consumer may treat it as committed business state.
        "is_candidate": True,
        "canonical_job": False,
    }
