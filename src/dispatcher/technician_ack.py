"""Deterministic matching between a job waiting on a technician's "K"
(acceptance) reply and the pool of captured WhatsApp observations --
never a guess, never auto-attached. Every signal used here already
exists in the schema (technicians.dispatch_group,
whatsapp_pending_observations.group_name/extracted_ticket/text); this
module only combines them into one of four confidence categories so a
human can decide with the evidence in front of them, not blindly.

See docs/END_TO_END_WORKFLOW_AUDIT.md step 6 for why this didn't exist
before this sprint: dispatch_tracking has milestones for "prepared",
"copied", "WhatsApp opened", "dispatched manually" -- nothing for
"technician replied K". This module plus storage.technician_acceptance
close that gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# Same bare-acknowledgement vocabulary whatsapp_bridge_adapter.py already
# uses to flag a reply for human review -- reused here, not redefined,
# so "looks like a K" means the same thing in both places.
ACKNOWLEDGEMENT_TEXTS = {"k", "kk", "ok", "okay", "yes", "y", "👍", "got it", "on it"}

# A small, explicit, never-inferred-beyond-this vocabulary for the
# opposite case -- a technician declining or reporting unavailability.
# Anything not matching either list is simply not acknowledgement-shaped
# at all (UNMATCHED), never guessed at.
DECLINE_TEXTS = {"no", "can't", "cant", "cannot", "not available", "busy", "pass", "n/a", "unavailable"}

CATEGORY_EXACT = "EXACT"
CATEGORY_STRONG = "STRONG"
CATEGORY_AMBIGUOUS = "AMBIGUOUS"
CATEGORY_UNMATCHED = "UNMATCHED"


@dataclass(frozen=True)
class MatchResult:
    message_id: str
    category: str
    is_decline_shaped: bool
    reasons: List[str] = field(default_factory=list)


def _text_shape(text: str) -> Optional[str]:
    normalized = (text or "").strip().lower()
    if normalized in ACKNOWLEDGEMENT_TEXTS:
        return "acknowledgement"
    if normalized in DECLINE_TEXTS:
        return "decline"
    return None


def classify_candidate(
    job: dict, technician: Optional[dict], observation: dict, *, other_waiting_ticket_numbers: Optional[List[str]] = None,
) -> MatchResult:
    """One observation against one job. `other_waiting_ticket_numbers`
    is the set of ticket numbers for every OTHER job currently also
    waiting on a K from the same technician -- needed to tell STRONG
    (this technician has exactly one job waiting, so an unlabeled "K"
    is unambiguous) from AMBIGUOUS (more than one, and this reply
    doesn't name a ticket) apart.
    """
    reasons: List[str] = []
    other_waiting_ticket_numbers = other_waiting_ticket_numbers or []

    shape = _text_shape(observation.get("text", ""))
    if shape is None:
        return MatchResult(
            message_id=observation["message_id"], category=CATEGORY_UNMATCHED, is_decline_shaped=False,
            reasons=["message text does not look like an acknowledgement or a decline"],
        )
    is_decline_shaped = shape == "decline"
    reasons.append(f"message text is {'decline' if is_decline_shaped else 'acknowledgement'}-shaped")

    group_matches = bool(
        technician and technician.get("dispatch_group") and observation.get("group_name")
        and technician["dispatch_group"].strip().lower() == observation["group_name"].strip().lower()
    )
    if group_matches:
        reasons.append(f"observation's WhatsApp group matches {technician['name']}'s dispatch group")
    else:
        reasons.append("observation's WhatsApp group does not match the assigned technician's dispatch group")

    ticket = (job.get("ticket_number") or "").strip().upper()
    extracted = (observation.get("extracted_ticket") or "").strip().upper()
    ticket_matches = bool(ticket and extracted and ticket == extracted)
    if ticket_matches:
        reasons.append(f"message explicitly references ticket {job['ticket_number']}")

    if not group_matches:
        return MatchResult(observation["message_id"], CATEGORY_UNMATCHED, is_decline_shaped, reasons)

    if ticket_matches:
        return MatchResult(observation["message_id"], CATEGORY_EXACT, is_decline_shaped, reasons)

    if extracted and ticket and extracted != ticket:
        reasons.append(f"message references a different ticket ({observation['extracted_ticket']})")
        return MatchResult(observation["message_id"], CATEGORY_UNMATCHED, is_decline_shaped, reasons)

    if other_waiting_ticket_numbers:
        reasons.append(
            f"this technician has {len(other_waiting_ticket_numbers)} other job(s) also waiting on K "
            "and this message doesn't name a ticket"
        )
        return MatchResult(observation["message_id"], CATEGORY_AMBIGUOUS, is_decline_shaped, reasons)

    reasons.append("this technician has exactly one job waiting on K, and the group matches")
    return MatchResult(observation["message_id"], CATEGORY_STRONG, is_decline_shaped, reasons)


def classify_candidates(
    job: dict, technician: Optional[dict], observations: List[dict], *, other_waiting_ticket_numbers: Optional[List[str]] = None,
) -> List[MatchResult]:
    """classify_candidate() over a list, skipping observations that are
    already resolved (attached/ignored elsewhere) -- callers should
    already be passing only status='pending' observations, but this is
    a cheap belt-and-suspenders filter."""
    return [
        classify_candidate(job, technician, obs, other_waiting_ticket_numbers=other_waiting_ticket_numbers)
        for obs in observations
        if obs.get("status", "pending") == "pending"
    ]
