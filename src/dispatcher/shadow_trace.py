"""Reconstructs end-to-end job traces from WhatsApp shadow observations
alone -- read-only, derived, never persisted as business truth.

WHY THIS EXISTS SEPARATELY FROM technician_ack CORRELATION:
technician_ack matches a technician's reply against an OPEN JOB ASSIGNED
TO THAT TECHNICIAN in the jobs table. That is the right rule for the
committed workflow, but during receive-only shadowing no WhatsApp-observed
job has been promoted into `jobs`, so it correctly returns UNMATCHED for
essentially everything. The real workflow evidence during shadowing lives
entirely in the observation data itself: the SAME ticket number appearing
in a company intake group, then a technician dispatch group, then a
closings group.

CORRELATION KEY: the exact ticket/PDL string, and nothing else. Two
messages are only ever linked because they literally name the same
ticket. Timing is recorded for ordering and displayed, but is NEVER used
to establish a link -- two messages close together in time are not a
trace.

This module is pure: it takes already-fetched rows and returns derived
structures. It performs no I/O, holds no connection, and cannot write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# The expected operational progression. Index = how far along the job is.
STAGE_ORDER = ["COMPANY_JOB_INTAKE", "TECH_DISPATCH", "TECH_CLOSINGS", "COMPANY_CLOSINGS"]

CONFIDENCE_EXACT = "EXACT"
CONFIDENCE_STRONG = "STRONG"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"
CONFIDENCE_UNMATCHED = "UNMATCHED"


@dataclass(frozen=True)
class TraceHop:
    """One observed message that named the trace's ticket."""
    ticket: str
    group_name: str
    group_id: str
    role: Optional[str]
    direction: str          # "incoming" | "outgoing" -- outgoing is the dispatcher's own message
    timestamp: Optional[str]
    confirmed_group: bool


@dataclass
class ShadowTrace:
    ticket: str
    hops: List[TraceHop] = field(default_factory=list)
    confidence: str = CONFIDENCE_UNMATCHED
    stages_covered: List[str] = field(default_factory=list)
    is_complete: bool = False
    notes: List[str] = field(default_factory=list)


def _sort_key(hop: TraceHop):
    """Timestamps arrive as numeric-ish strings; fall back to 0 so a hop
    with a missing timestamp orders first rather than crashing."""
    try:
        return int(hop.timestamp)
    except (TypeError, ValueError):
        return 0


def _classify(hops: List[TraceHop]) -> tuple:
    """Returns (confidence, stages_covered, is_complete, notes).

    Never forces a match: a ticket seen in only one group is UNMATCHED,
    not a trace, and same-role repeats are surfaced as AMBIGUOUS rather
    than silently flattened into a single clean-looking chain."""
    notes = []
    distinct_groups = {h.group_id for h in hops}
    if len(distinct_groups) < 2:
        return CONFIDENCE_UNMATCHED, [], False, ["ticket seen in only one group -- not a trace"]

    known = [h for h in hops if h.role in STAGE_ORDER]
    stages_covered = []
    for h in sorted(known, key=_sort_key):
        if h.role not in stages_covered:
            stages_covered.append(h.role)

    if any(h.role not in STAGE_ORDER for h in hops):
        notes.append("includes a hop in a group whose role is not yet confirmed")

    # Same role in two DIFFERENT groups means the same ticket went to two
    # different technicians/companies at the same workflow stage -- a real
    # and important signal (usually a reassignment after a decline), but
    # genuinely ambiguous as one linear chain. Never collapsed silently.
    by_role = {}
    for h in known:
        by_role.setdefault(h.role, set()).add(h.group_id)
    repeated = [role for role, gids in by_role.items() if len(gids) > 1]
    if repeated:
        notes.append(
            "same ticket appears at the same workflow stage in more than one group "
            f"({', '.join(sorted(repeated))}) -- possible reassignment, needs a human"
        )
        return CONFIDENCE_AMBIGUOUS, stages_covered, False, notes

    if len(stages_covered) < 2:
        # Two groups, but not two *known* stages -- can't assert a
        # progression without confirmed roles on both ends.
        return CONFIDENCE_STRONG, stages_covered, False, notes + [
            "spans multiple groups but fewer than two confirmed workflow stages"
        ]

    positions = [STAGE_ORDER.index(s) for s in stages_covered]
    is_ordered = all(a < b for a, b in zip(positions, positions[1:]))
    is_complete = stages_covered == STAGE_ORDER

    if is_ordered:
        return CONFIDENCE_EXACT, stages_covered, is_complete, notes
    notes.append("workflow stages observed out of the expected order")
    return CONFIDENCE_STRONG, stages_covered, is_complete, notes


def build_traces(hops: List[TraceHop]) -> List[ShadowTrace]:
    """Groups hops by exact ticket and classifies each resulting chain.
    Returns traces sorted by their earliest hop."""
    by_ticket = {}
    for hop in hops:
        by_ticket.setdefault(hop.ticket, []).append(hop)

    traces = []
    for ticket, ticket_hops in by_ticket.items():
        ordered = sorted(ticket_hops, key=_sort_key)
        confidence, stages, complete, notes = _classify(ordered)
        traces.append(ShadowTrace(
            ticket=ticket, hops=ordered, confidence=confidence,
            stages_covered=stages, is_complete=complete, notes=notes,
        ))
    traces.sort(key=lambda t: _sort_key(t.hops[0]) if t.hops else 0)
    return traces


def actionable_traces(traces: List[ShadowTrace]) -> List[ShadowTrace]:
    """Traces that actually link groups. UNMATCHED ones are excluded --
    they are single observations, not chains."""
    return [t for t in traces if t.confidence != CONFIDENCE_UNMATCHED]
