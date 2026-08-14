"""Banna Apprentice -- evidence aggregation for PROPOSED playbooks.

THE PRINCIPLE
Train Banna Dispatch by working normally:

    OBSERVE -> FIND REPEATED PATTERN -> BUILD EVIDENCE -> PROPOSE
    -> OWNER REVIEWS -> OWNER APPROVES -> only then can business truth change

This module implements the first four steps and nothing else. There is
no approval function here, no activation, no write path, and no caller
in this codebase that can turn a suggestion into a routing rule. A
suggestion is a printed opinion backed by counts; it is inert.

REPEATED OBSERVATIONS ARE EVIDENCE, NOT TRUTH
One example must never create a rule, so a pattern below the evidence
bar is not proposed at all. Even at the top confidence tier the output
is a proposal for a human.

THE TEMPORARY-COVERAGE TRAP
The most dangerous mistake this module could make is turning a stand-in
into a permanent assignment. A technician covering another's area for a
weekend can easily out-number the real rule in a short window. So
evidence concentrated into very few distinct days is capped and labelled
TEMPORARY COVERAGE -- never HIGH confidence, no matter how many messages
it contains.

PURITY
No connection, no SQL, no I/O. The caller assembles RoutingObservations
from existing traces and the existing group registry; this module only
counts them.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Evidence bar. Deliberately strict: this repository has repeatedly
# declined to implement patterns seen 2-3 times, and an automated
# proposal must not be laxer than a human reviewer was.
MIN_TICKETS = 4
MIN_DAYS = 2
HIGH_TICKETS = 8
HIGH_DAYS = 3

# Below this many distinct days, volume alone cannot establish a
# permanent rule -- see "the temporary-coverage trap" above.
TEMPORARY_COVERAGE_MAX_DAYS = 2

STATUS_OWNER_CONFIRMED = "OWNER-CONFIRMED RULE"
STATUS_OBSERVED = "OBSERVED PATTERN"
STATUS_TEMPORARY = "TEMPORARY COVERAGE"

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"


@dataclass(frozen=True)
class RoutingObservation:
    """One observed intake -> dispatch pairing, correlated by EXACT
    ticket (never by timing). Assembled by the caller from existing
    shadow traces plus the existing owner-confirmed group registry."""
    source: str                     # company / job source
    area: Optional[str]
    service: Optional[str]
    destination: str                # technician the job actually went to
    ticket: str
    day: Optional[str]              # ISO date, for distinct-day counting


@dataclass
class PlaybookSuggestion:
    source: str
    area: Optional[str]
    service: Optional[str]
    destination: str
    observations: int = 0
    distinct_tickets: int = 0
    distinct_days: int = 0
    conflicts: int = 0
    conflicting_destinations: Dict[str, int] = field(default_factory=dict)
    confidence: str = CONFIDENCE_LOW
    status: str = STATUS_OBSERVED
    notes: List[str] = field(default_factory=list)
    # Always false in this build. Present so any future approval path has
    # to set it explicitly rather than inherit a truthy default.
    approved: bool = False

    def as_dict(self) -> dict:
        return {
            "source": self.source, "area": self.area, "service": self.service,
            "destination": self.destination,
            "evidence": {
                "observations": self.observations,
                "distinct_tickets": self.distinct_tickets,
                "distinct_days": self.distinct_days,
            },
            "conflicts": self.conflicts,
            "conflicting_destinations": self.conflicting_destinations,
            "confidence": self.confidence,
            "status": self.status,
            "notes": self.notes,
            "approved": self.approved,
            # Stated on every payload so no consumer can mistake a
            # suggestion for an active rule.
            "active": False,
        }


def _key(obs: RoutingObservation):
    return (obs.source, obs.area, obs.service)


def build_suggestions(
    observations: List[RoutingObservation],
    owner_confirmed: Optional[set] = None,
) -> List[PlaybookSuggestion]:
    """Aggregate observations into proposals, strongest evidence first.

    `owner_confirmed` is a set of (source, area, service, destination)
    tuples the owner has ALREADY confirmed. Those are reported with
    STATUS_OWNER_CONFIRMED so the review screen can visibly separate
    "this is already your rule" from "this is only something I saw".
    """
    owner_confirmed = owner_confirmed or set()
    grouped: Dict[tuple, List[RoutingObservation]] = collections.defaultdict(list)
    for obs in observations:
        grouped[_key(obs)].append(obs)

    suggestions: List[PlaybookSuggestion] = []
    for (source, area, service), group in grouped.items():
        destinations = collections.Counter(o.destination for o in group)
        winner, winner_count = destinations.most_common(1)[0]
        for_winner = [o for o in group if o.destination == winner]

        tickets = {o.ticket for o in for_winner}
        days = {o.day for o in for_winner if o.day}
        conflicts = len(group) - winner_count
        other = {d: c for d, c in destinations.items() if d != winner}

        # Below the bar -> not proposed at all. Silence is the correct
        # output for thin evidence.
        if len(tickets) < MIN_TICKETS or len(days) < MIN_DAYS:
            continue

        notes: List[str] = []
        if (source, area, service, winner) in owner_confirmed:
            status = STATUS_OWNER_CONFIRMED
            confidence = CONFIDENCE_HIGH
            notes.append("Already an owner-confirmed rule -- shown as corroboration, not as a change.")
        elif len(days) <= TEMPORARY_COVERAGE_MAX_DAYS:
            status = STATUS_TEMPORARY
            confidence = CONFIDENCE_LOW
            notes.append(
                f"All {len(tickets)} tickets fall on only {len(days)} day(s). This is the shape of "
                "temporary coverage, not a permanent rule, and must not be treated as one.")
        else:
            status = STATUS_OBSERVED
            if conflicts == 0 and len(tickets) >= HIGH_TICKETS and len(days) >= HIGH_DAYS:
                confidence = CONFIDENCE_HIGH
            elif conflicts <= 1:
                confidence = CONFIDENCE_MEDIUM
            else:
                confidence = CONFIDENCE_LOW

        if conflicts:
            notes.append(
                f"{conflicts} observation(s) went elsewhere ({', '.join(sorted(other))}) -- "
                "a competing rule, a split by service type, or coverage.")
        notes.append("Proposal only. No routing changes until the owner approves.")

        suggestions.append(PlaybookSuggestion(
            source=source, area=area, service=service, destination=winner,
            observations=len(for_winner), distinct_tickets=len(tickets),
            distinct_days=len(days), conflicts=conflicts,
            conflicting_destinations=other, confidence=confidence,
            status=status, notes=notes,
        ))

    order = {CONFIDENCE_HIGH: 0, CONFIDENCE_MEDIUM: 1, CONFIDENCE_LOW: 2}
    suggestions.sort(key=lambda s: (order.get(s.confidence, 3), -s.distinct_tickets))
    return suggestions
