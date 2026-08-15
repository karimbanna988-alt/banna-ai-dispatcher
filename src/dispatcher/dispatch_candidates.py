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

import re
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

# Why a candidate is being held for a human by a SAFETY decision (as
# opposed to ordinary incompleteness like "no ticket"). These must stay
# visible in the console: a suppression the operator cannot see is a
# decision made on their behalf without telling them.
REVIEW_REPOST = "REPOST AFTER PRIOR DISPATCH"
REVIEW_ORDER_UNDETERMINED = "ORDER UNDETERMINED"
REVIEW_COMPANY_UNRESOLVED = "COMPANY UNRESOLVED"


def _parse_ts(value) -> Optional[datetime]:
    """A real parsed instant, or None when the value cannot be trusted.

    Accepts an ISO string or a unix epoch (int, or the TEXT epoch the
    observations table actually stores).

    Lexical comparison is wrong across offsets: "2026-08-15T12:00:09+00:00"
    is genuinely later than "2026-08-15T13:00:00+01:00" but sorts earlier
    as a string. Naive values are read as UTC so they remain comparable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.isdigit():                      # epoch stored as TEXT
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def parse_event_time(value):
    """Public form of the event-time parser, for callers that need to
    ORDER rows before building candidates. One parser, one definition."""
    return _parse_ts(value)


def _event_time(record: dict):
    """When the message ACTUALLY happened on WhatsApp.

    Business sequencing must never use ingestion time. `received_at` is
    when this system persisted the row, which can be far later and even
    out of order -- the 25 observations recovered from the stuck retry
    queue were all persisted long after they occurred, and this database
    already contains rows stored out of WhatsApp-event order. Ordering on
    ingestion would have declared those dispatches "after" intakes they
    actually preceded.

    `received_at` stays what it is: ingestion/health metadata.
    """
    return record.get("event_at")


def _sequence(intake_at, occurrences: List[dict]) -> Tuple[str, List[dict]]:
    """Where these occurrences sit relative to the intake message.

    `intake_at` and each occurrence's `event_at` are WhatsApp event
    times, not ingestion times -- see _event_time().

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
        stamp = _parse_ts(_event_time(occurrence)) if base else None
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
    event_at=None,
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
    order_unprovable: bool = False,
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

    # 2. No owner-confirmed company means no correlation identity. The
    #    job may be perfectly real, but nothing can be checked against
    #    it -- not a prior dispatch, not a repost -- so it can never be
    #    offered as sendable.
    if not company:
        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected=f"Job {ticket}" if ticket else "Message with no ticket number",
            recommended=None, confidence=CONFIDENCE_UNMATCHED,
            why=["This group has no owner-confirmed company",
                 "Without a company there is no identity to correlate on, so this "
                 "cannot be checked against a prior dispatch"],
            status=STATUS_HUMAN_REVIEW, missing=missing,
            proposed_message=None, correlated_job_id=correlated_job_id,
            needs_human_review=True, review_reason=REVIEW_COMPANY_UNRESOLVED)

    # 3. No ticket means nothing can be tracked or correlated later.
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

    # More than one intake exists for this (company, ticket) and their
    # order cannot be established. One of them may be a genuine repost,
    # so neither is offered as sendable and the uncertainty is shown.
    if order_unprovable:
        why.append("Several intakes exist for this ticket and their order cannot be "
                   "established from the WhatsApp timestamps")
        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected=detected, recommended=recommended, confidence=confidence, why=why,
            status=STATUS_HUMAN_REVIEW, missing=missing,
            proposed_message=None, correlated_job_id=correlated_job_id,
            needs_human_review=True, review_reason=REVIEW_ORDER_UNDETERMINED)

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
        order, relevant = _sequence(event_at, occurrences)
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
                needs_human_review=True, review_reason=REVIEW_REPOST)

        why.append(f"This ticket also appears in {names or 'a technician group'}, but the "
                   "order of events cannot be established from the timestamps")
        return _candidate(
            message_id, received_at, group_name, company, ticket, service, city, state,
            detected=detected, recommended=recommended, confidence=confidence, why=why,
            status=STATUS_HUMAN_REVIEW, missing=missing,
            proposed_message=None, correlated_job_id=correlated_job_id,
            actual_destination=names or None, routing_match=ROUTING_UNVERIFIED,
            needs_human_review=True, review_reason=REVIEW_ORDER_UNDETERMINED)

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
            needs_human_review=True, review_reason=REVIEW_COMPANY_UNRESOLVED)

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
               needs_human_review=None, review_reason=None) -> dict:
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
        # Set only when a SAFETY rule held this back. The console keeps
        # these visible; ordinary incompleteness stays off the board.
        "review_reason": review_reason,
        # Stated on every payload. A candidate is understanding, not a
        # job, and no consumer may treat it as committed business state.
        "is_candidate": True,
        "canonical_job": False,
    }


# ---------------------------------------------------------------------
# Shared correlation primitives.
#
# ONE definition each, used by every caller: intake identity, downstream
# evidence, outgoing-audit evidence, existing-job comparison, dedup and
# lookup. A second spelling of either of these is how two code paths
# quietly stop agreeing about whether a job was already sent.
# ---------------------------------------------------------------------

def normalize_ticket(value) -> Optional[str]:
    """The correlation form of a ticket.

    Case and surrounding whitespace are noise: "abc123" and "ABC123" are
    the same ticket to a human and must be the same key here. Nothing
    else is touched -- no fuzzy matching, no punctuation stripping, no
    prefix logic. Two tickets that differ by a real character remain
    different tickets.
    """
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


# Message meanings that CONTRADICT "this job was handed to a technician".
# A decline, a status update or a closing can quote or paste the whole
# original job block, so structure alone proves nothing about dispatch.
# ---------------------------------------------------------------------
# EVIDENCE CLASSIFICATION -- one deterministic, tri-state answer.
#
# The question is never "does this text contain the word ETA". It is
# "does this message PROVE a job was handed to a technician". Three
# answers, and the third one is not a failure mode -- it is the honest
# result when attribution is genuinely unclear.
# ---------------------------------------------------------------------

POSITIVE_DISPATCH = "POSITIVE_DISPATCH"
NEGATIVE_OR_UPDATE = "NEGATIVE_OR_UPDATE"
AMBIGUOUS = "AMBIGUOUS"

# A forwarded job block is "Label: value" lines. Values wrap onto
# continuation lines ("Desc: Storage lockout" / "he needs it open in the
# next hour and a half"). Both belong to the CUSTOMER; a technician's own
# words come after the block. This structural split replaces the earlier
# line-length guess, which was arbitrary and broke on real wording.
_FIELD_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9 #/'()-]{0,24}:\s")
# Labels that introduce a ticket, used only to COUNT jobs in a message.
def _ticket_matches(text: str) -> list:
    """Every structurally plausible ticket reference, normalized, in order.

    This is lexical extraction only. Correlation evidence must additionally
    pass validated_tickets(), which checks the candidate against identities
    already known in the relevant company scope.
    """
    seen, out = set(), []
    for match in _TICKET_LABEL.finditer(text or ""):
        key = normalize_ticket(match.group("val"))
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def count_tickets(text: str) -> int:
    """How many distinct jobs this message references."""
    return len(_ticket_matches(text))


def all_tickets(text: str) -> list:
    """Every structurally plausible ticket reference, normalized."""
    return _ticket_matches(text)


def validated_tickets(text: str, known_tickets) -> list:
    """Ticket identities this text may use as correlation evidence.

    Shape cannot distinguish a real all-letter identifier such as WJFBT
    from an English word such as READY. The deterministic boundary is an
    exact normalized match against tickets already known from confirmed
    intake or persisted job evidence in the relevant company scope.
    """
    known = {normalize_ticket(ticket) for ticket in (known_tickets or [])}
    known.discard(None)
    return [ticket for ticket in _ticket_matches(text) if ticket in known]


def _segments(text: str) -> list:
    """Split a message into (kind, line) where kind is:

      "field"        a "Label: value" job-block line, or its wrapped
                     continuation -- the CUSTOMER's words
      "detached"     a line after the block was closed by a blank line --
                     unambiguously the TECHNICIAN speaking

    Continuation lines are marked separately because they are genuinely
    uncertain: a technician may reply on the very next line without a
    blank, and a customer description may wrap onto one. That uncertainty
    is preserved rather than guessed at with a length rule.
    """
    out, in_block, closed = [], False, False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if in_block:
                in_block, closed = False, True
            continue
        if _FIELD_LINE.match(line):
            in_block, closed = True, False
            out.append(("field", line))
        elif in_block:
            out.append(("continuation", line))
        elif closed:
            out.append(("detached", line))
        else:
            out.append(("header", line))
    return out


def _exact_technician_token(fragment: str) -> Optional[str]:
    """Vocabulary only a TECHNICIAN uses, matched against the whole
    fragment: "pass", "busy", "k", "cannot take it". A customer
    description never consists solely of these."""
    from dispatcher import message_patterns

    if message_patterns.is_decline(fragment) or message_patterns.is_pass_prefixed(fragment):
        return "decline"
    if message_patterns.is_acknowledgement(fragment):
        return "acknowledgement"
    lowered = fragment.lower()
    for phrase in ("cannot take", "can't take", "cant take", "not taking",
                   "won't take", "wont take"):
        if phrase in lowered:
            return "decline"
    # A customer job description does not report that the customer did not
    # answer -- that is the technician reporting back.
    for phrase in ("no answer", "not answering", "didn't answer", "didnt answer",
                   "no reply", "voicemail", "voice mail"):
        if phrase in lowered:
            return "customer no answer"
    return None


# Words that carry no job information on their own -- they only frame a
# time or a movement. A line built ENTIRELY from these plus numbers is a
# technician status ("20 min", "eta 20", "be there in 30", "on my way"),
# never a customer job description, which always names something concrete
# ("he needs the storage open in the next hour" -> storage, open, needs).
#
# This is a structural test on CONTENT, not a length threshold: a long
# line of pure filler would still be a status, and a short line naming a
# real thing would still be description.
_UPDATE_FILLER = {
    "eta", "be", "there", "in", "on", "my", "way", "omw", "away", "from",
    "i", "am", "ill", "will", "he", "she", "they", "we", "is", "are",
    "about", "around", "approx", "approximately", "roughly", "maybe",
    "min", "mins", "minute", "minutes", "hour", "hours", "hr", "hrs",
    "the", "a", "to", "at", "and", "customer", "cx", "client", "now",
}
_WORD = re.compile(r"[A-Za-z0-9']+")

# THE ticket extractor for correlation evidence. One definition, used by
# the classifier AND by the route -- a second grammar in either place is
# how a real forwarded job gets skipped and quietly becomes sendable
# again.
#
# Labels: Ticket / PDL only. `Ref:` names the SOURCE ("Ref: AGN Locksmith
# Services") and is deliberately not a ticket label. The colon is
# OPTIONAL and the label may appear mid-line, because production writes
# all of: "Ticket: X", "Ticket X", "PDL X", "//Ticket X //".
#
# The structural grammar is limited to the two shapes found in the measured
# production corpus (340 distinct tickets): five alphanumeric characters, or
# four + hyphen + four. Case is normalized later because correlation itself is
# case-insensitive. Shape is NOT the identity boundary -- validated_tickets()
# supplies that by matching against known company-scoped tickets.
_TICKET_LABEL = re.compile(
    r"\b(?:Ticket|PDL)\b[:#]?[ \t]*"
    r"(?P<val>(?:[A-Za-z0-9]{4}-[A-Za-z0-9]{4}|[A-Za-z0-9]{5}))"
    r"(?![A-Za-z0-9-])",
    re.IGNORECASE,
)


def _is_standalone_status_line(line: str) -> bool:
    """True when every word on the line is filler or a number.

    "20 min" / "eta 20" / "be there in 30" / "on my way" -> True
    "he needs the storage open in the next hour"          -> False
    """
    words = _WORD.findall(line or "")
    if not words:
        return False
    if not any(w.isdigit() for w in words) and not any(
            w.lower() in ("omw", "way") for w in words):
        # No time and no movement: not a status expression.
        return False
    return all(w.isdigit() or w.lower() in _UPDATE_FILLER for w in words)


def _status_meaning(fragment: str) -> Optional[str]:
    """Update-style meaning: ETA, en route, quote, closing, no answer,
    availability. These words also occur in customer descriptions, so
    WHERE they appear decides how much they are trusted."""
    from dispatcher import message_patterns

    if message_patterns.is_no_answer(fragment):
        return "customer no answer"
    if message_patterns.says_job_done(fragment):
        return "completion/closing"
    if message_patterns.mentions_quote(fragment):
        return "price quote"
    if message_patterns.is_en_route(fragment) or message_patterns.stated_eta(fragment):
        return "status update"
    lowered = fragment.lower()
    for phrase in ("not available", "unavailable"):
        if phrase in lowered:
            return "availability"
    return None


def classify_dispatch_evidence(
    text: str,
    has_forwarded_structure: bool,
    evidence_tickets=None,
) -> tuple:
    """(verdict, reason) for one candidate piece of dispatch evidence.

    POSITIVE_DISPATCH  a single coherent forwarded job, nothing from the
                       technician contradicting it
    NEGATIVE_OR_UPDATE unambiguous technician response -- a refusal, or a
                       status update written clearly outside the job block
    AMBIGUOUS          attribution cannot be established: more than one
                       ticket, or status wording sitting where it could
                       belong to either the customer or the technician

    AMBIGUOUS never means "safe to send" and never means "already sent".
    """
    body = text or ""
    tickets = (_ticket_matches(body) if evidence_tickets is None
               else list(dict.fromkeys(normalize_ticket(t) for t in evidence_tickets
                                       if normalize_ticket(t))))
    if len(tickets) > 1:
        return AMBIGUOUS, "message references more than one ticket"

    if not has_forwarded_structure:
        return NEGATIVE_OR_UPDATE, "no forwarded job block"

    segments = _segments(body)

    # 1. Technician-only vocabulary, wherever it appears. A customer
    #    description is never just "pass" or "cannot take it".
    for kind, line in segments:
        if kind in ("continuation", "detached", "header"):
            token = _exact_technician_token(line)
            if token:
                return NEGATIVE_OR_UPDATE, token

    # 2. Status wording clearly detached from the block: the technician.
    for kind, line in segments:
        if kind == "detached":
            meaning = _status_meaning(line)
            if meaning:
                return NEGATIVE_OR_UPDATE, meaning

    # 3. A trailing standalone status line, written under the block with
    #    no blank separator. "20 min" typed straight after the last field
    #    is a technician ETA, not part of the address. Recognised by its
    #    CONTENT being nothing but time/movement filler -- so real job
    #    prose on the same kind of line is untouched.
    if segments and segments[-1][0] == "continuation":
        last = segments[-1][1]
        if _is_standalone_status_line(last):
            return NEGATIVE_OR_UPDATE, "trailing technician status (%s)" % last.strip()

    # 4. Other wrapped lines inside the block are the CUSTOMER's job text, and
    #    status-shaped words there are description, not a technician
    #    report: real postings say "he needs it open in the next hour" and
    #    "customer not available until 3 PM". Treating those as technician
    #    updates rejected every genuine Augusta dispatch in live data --
    #    22 correctly-identified dispatches collapsed to 0 and became
    #    review noise. A technician's own status still registers: exact
    #    vocabulary is caught above wherever it appears, and anything
    #    written after a blank line is handled as detached speech.

    return POSITIVE_DISPATCH, "coherent forwarded job with no contradicting technician words"


def is_positive_forwarded_dispatch(text: str, has_forwarded_structure: bool) -> bool:
    """Back-compatible boolean wrapper over the tri-state classifier.
    AMBIGUOUS is deliberately NOT positive."""
    verdict, _ = classify_dispatch_evidence(text, has_forwarded_structure)
    return verdict == POSITIVE_DISPATCH
