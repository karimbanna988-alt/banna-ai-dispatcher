"""The dispatcher's canonical action vocabulary and its state rules.

WHY THIS IS SEPARATE FROM action_names.ActionName
ActionName is the vocabulary of things a HUMAN has already done to a
job -- it is written into the events table and feeds
storage.derive_job_status. This module is the vocabulary of what the
system PROPOSES a human should do next. They are deliberately different
layers: a proposal is not a fact, and nothing here may write anything.

Each proposal declares which committed ActionName it would map to if a
human accepted it, so the two vocabularies stay explicitly linked rather
than drifting into parallel meanings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from dispatcher.action_names import ActionName

# ------------------------------------------------- proposed next steps ----
ACKNOWLEDGE_COMPANY = "ACKNOWLEDGE_COMPANY"
DISPATCH_TECHNICIAN = "DISPATCH_TECHNICIAN"
TECHNICIAN_ACCEPTED = "TECHNICIAN_ACCEPTED"
TECHNICIAN_EN_ROUTE = "TECHNICIAN_EN_ROUTE"
UPDATE_COMPANY = "UPDATE_COMPANY"
PRICE_QUOTE_RECEIVED = "PRICE_QUOTE_RECEIVED"
CUSTOMER_NO_ANSWER = "CUSTOMER_NO_ANSWER"
REASSIGN_TECHNICIAN = "REASSIGN_TECHNICIAN"
REQUEST_CLOSING_DETAILS = "REQUEST_CLOSING_DETAILS"
SEND_CLOSING_TO_COMPANY = "SEND_CLOSING_TO_COMPANY"
COMPLETE_JOB = "COMPLETE_JOB"
HUMAN_REVIEW_NEEDED = "HUMAN_REVIEW_NEEDED"
NO_ACTION = "NO_ACTION"

ALL_ACTIONS = frozenset({
    ACKNOWLEDGE_COMPANY, DISPATCH_TECHNICIAN, TECHNICIAN_ACCEPTED, TECHNICIAN_EN_ROUTE,
    UPDATE_COMPANY, PRICE_QUOTE_RECEIVED, CUSTOMER_NO_ANSWER, REASSIGN_TECHNICIAN,
    REQUEST_CLOSING_DETAILS, SEND_CLOSING_TO_COMPANY, COMPLETE_JOB,
    HUMAN_REVIEW_NEEDED, NO_ACTION,
})

# Which committed ActionName each proposal becomes IF a human accepts it.
# A proposal with no mapping cannot be committed at all -- it exists only
# to route work to a person.
COMMITS_AS = {
    TECHNICIAN_ACCEPTED: ActionName.TECHNICIAN_ACCEPTED,
    REASSIGN_TECHNICIAN: ActionName.TECHNICIAN_DECLINED,
    UPDATE_COMPANY: ActionName.COMPANY_UPDATE_PREPARED,
    TECHNICIAN_EN_ROUTE: ActionName.COMPANY_UPDATE_PREPARED,
    CUSTOMER_NO_ANSWER: ActionName.COMPANY_UPDATE_PREPARED,
    SEND_CLOSING_TO_COMPANY: ActionName.COMPANY_CLOSING_PREPARED,
    COMPLETE_JOB: ActionName.JOB_COMPLETED,
    DISPATCH_TECHNICIAN: ActionName.ASSIGN,
    # Deliberately absent: ACKNOWLEDGE_COMPANY, PRICE_QUOTE_RECEIVED,
    # REQUEST_CLOSING_DETAILS, HUMAN_REVIEW_NEEDED, NO_ACTION. None of
    # these changes a job's committed state on its own.
}

# ------------------------------------------------------------ confidence ----
EXACT = "EXACT"
STRONG = "STRONG"
AMBIGUOUS = "AMBIGUOUS"
UNMATCHED = "UNMATCHED"

# Only these may carry a normal operational proposal. AMBIGUOUS and
# UNMATCHED always become HUMAN_REVIEW_NEEDED -- the bar that has held
# through every shadow shift and must not be lowered for a demo.
ACTIONABLE_CONFIDENCE = frozenset({EXACT, STRONG})


def is_actionable(confidence: Optional[str]) -> bool:
    return confidence in ACTIONABLE_CONFIDENCE


def gate(action: str, confidence: Optional[str]) -> str:
    """The single place the confidence bar is enforced. Any proposal made
    on weak evidence is downgraded to human review rather than shown as
    something to act on."""
    if action in (HUMAN_REVIEW_NEEDED, NO_ACTION):
        return action
    return action if is_actionable(confidence) else HUMAN_REVIEW_NEEDED


# --------------------------------------------------- demo lifecycle ----
# The isolated demo's own state names. Deliberately NOT the production
# jobs.status values: a demo must never be able to manufacture a real
# status. storage.derive_job_status remains the only authority for those.
DEMO_NEW = "NEW"
DEMO_ASSIGNED = "ASSIGNED"
DEMO_EN_ROUTE = "EN_ROUTE"
DEMO_COMPLETED = "COMPLETED"
DEMO_NEEDS_REVIEW = "HUMAN_REVIEW_NEEDED"

# Which demo transitions are legal. A closing arriving before the
# technician ever accepted is not a valid path to COMPLETED -- it is a
# human-review case, so the demo cannot show a job completing out of
# order.
DEMO_TRANSITIONS = {
    DEMO_NEW: {DEMO_NEW, DEMO_ASSIGNED, DEMO_NEEDS_REVIEW},
    DEMO_ASSIGNED: {DEMO_ASSIGNED, DEMO_EN_ROUTE, DEMO_COMPLETED, DEMO_NEEDS_REVIEW},
    DEMO_EN_ROUTE: {DEMO_EN_ROUTE, DEMO_COMPLETED, DEMO_NEEDS_REVIEW},
    DEMO_COMPLETED: {DEMO_COMPLETED},
    DEMO_NEEDS_REVIEW: {DEMO_NEEDS_REVIEW, DEMO_ASSIGNED, DEMO_EN_ROUTE, DEMO_COMPLETED},
}


def demo_transition_allowed(current: str, proposed: str) -> bool:
    return proposed in DEMO_TRANSITIONS.get(current, set())


@dataclass(frozen=True)
class Proposal:
    """What the system thinks should happen next, and why. Inert by
    construction -- holds no connection and commits nothing."""
    action: str
    confidence: Optional[str]
    reasons: tuple = ()
    proposed_message: Optional[str] = None
    needs_human: bool = False

    @property
    def commits_as(self) -> Optional[ActionName]:
        return COMMITS_AS.get(self.action)

    @property
    def is_committable(self) -> bool:
        """True only if a human accepting this would map to a real
        recorded action. Everything else is routing, not a change."""
        return self.commits_as is not None and not self.needs_human
