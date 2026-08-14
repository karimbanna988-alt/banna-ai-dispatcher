"""Live Operations Feed -- the owner-facing view of passive shadowing.

WHAT THIS IS
A PRESENTATION projection that turns one already-interpreted observation
into the operator-facing event lines the console shows:

    21:46:02  RECEIVED    Augusta-Adam
    21:46:02  PARSED      Company / Area / Service
    21:46:03  CORRELATED  JO47-TPXW  EXACT
    21:46:03  DECISION    Recommended technician: Sebai
    21:46:03  NEXT        WAITING FOR HUMAN DISPATCH

WHAT THIS IS NOT
It is NOT a second interpretation engine. It never reads raw WhatsApp
text, never parses, never classifies a message and never decides
anything. Its whole input is the dict `_interpret_whatsapp_observation`
already produced from the shared dispatcher brain, and its whole job is
to choose an operator-facing event TYPE and wording for that existing
verdict. If the brain's label changes, this follows; it can never
disagree with the brain, because it has nothing of its own to disagree
with.

It also cannot mutate anything. No connection, no SQL, no I/O.

WHY / EVIDENCE RULE
`why` lines carry deterministic business evidence only -- the company,
the area, the service, the routing role, the confidence, the fields that
were missing. Never model reasoning, never a narrative.

PRIVACY
This module never receives raw message text. The caller passes an
already-masked preview (shared.mask_sensitive) and this module truncates
it further for the feed line. Secrets and session material never enter
here at all.
"""
from __future__ import annotations

from typing import List, Optional

# Operator-facing event types. Deliberately a small closed vocabulary --
# an operator scanning a phone should recognise the left column instantly.
RECEIVED = "RECEIVED"
PARSED = "PARSED"
CORRELATED = "CORRELATED"
ROUTING = "ROUTING"
DECISION = "DECISION"
STATE = "STATE"
TECH_UPDATE = "TECH UPDATE"
CLOSING = "CLOSING"
HUMAN_REVIEW = "HUMAN REVIEW"
ERROR = "ERROR"
SYSTEM = "SYSTEM"

EVENT_TYPES = (RECEIVED, PARSED, CORRELATED, ROUTING, DECISION, STATE,
               TECH_UPDATE, CLOSING, HUMAN_REVIEW, ERROR, SYSTEM)

# How an existing interpretation label maps to an operator event type.
# This categorises a verdict the brain already reached -- it does not
# re-derive it. Longest-prefix-first so "TECHNICIAN DECLINE" cannot be
# swallowed by a shorter "TECHNICIAN" entry.
_LABEL_TYPES = (
    ("NEW JOB DETECTED", DECISION),
    ("JOB FORWARDED TO TECHNICIAN", ROUTING),
    ("TECHNICIAN DECLINE", TECH_UPDATE),
    ("TECHNICIAN NO ANSWER", TECH_UPDATE),
    ("TECHNICIAN PRICE QUOTE", TECH_UPDATE),
    ("TECHNICIAN EN ROUTE", TECH_UPDATE),
    ("TECHNICIAN ETA", TECH_UPDATE),
    ("TECHNICIAN UPDATE", TECH_UPDATE),
    ("TECHNICIAN K", TECH_UPDATE),
    ("CLOSING RECEIVED", CLOSING),
    ("CLOSING", CLOSING),
    ("ACKNOWLEDGEMENT", STATE),
    ("MENTION ONLY", STATE),
    ("MESSAGE RECEIVED", STATE),
    ("Unmapped", HUMAN_REVIEW),
)

_PREVIEW_CHARS = 90


def _event(when, kind: str, title: str, why: Optional[List[str]] = None,
           ticket: Optional[str] = None, confidence: Optional[str] = None,
           needs_human: bool = False) -> dict:
    return {
        "time": when, "type": kind, "title": title,
        "why": [w for w in (why or []) if w],
        "ticket": ticket, "confidence": confidence,
        "needs_human": needs_human,
    }


def needs_human(next_action: Optional[str]) -> bool:
    na = (next_action or "").upper()
    return "HUMAN_REVIEW" in na or "MAPPING_REQUIRED" in na


def event_type_for(interpretation: Optional[str], next_action: Optional[str] = None) -> str:
    """Categorise an existing interpretation label.

    The semantic type wins over the review flag, deliberately. Almost
    every shadow observation currently routes to HUMAN_REVIEW_NEEDED
    (nothing is auto-committed), so typing them all as HUMAN REVIEW made
    the feed one flat column and hid what actually happened -- a closing
    worth $125 read exactly like a stray @mention. The operator needs
    both facts, so the type carries WHAT IT WAS and the separate
    `needs_human` flag carries WHETHER IT NEEDS THEM.

    Only a message with no recognisable label falls back to HUMAN REVIEW.
    """
    label = interpretation or ""
    for prefix, kind in _LABEL_TYPES:
        if label.startswith(prefix):
            return kind
    return HUMAN_REVIEW if needs_human(next_action) else STATE


def events_for(item: dict) -> List[dict]:
    """One interpreted observation in, its operator event lines out.

    `item` is exactly what /api/whatsapp_shadow_feed already builds:
    the observation fields plus _interpret_whatsapp_observation's verdict.
    """
    when = item.get("time")
    group = item.get("group_name") or "unknown group"
    role_label = item.get("role_label") or item.get("role") or ""
    ticket = item.get("proposed_ticket")
    confidence = item.get("confidence")
    company = item.get("company")
    area = item.get("service_area")
    technician = item.get("technician")
    interpretation = item.get("interpretation")
    next_action = item.get("next_action")
    missing = item.get("missing_fields") or []
    # One feed line per event: a multi-line WhatsApp message must not
    # break the row. Already masked by the caller before it gets here.
    preview = " ".join((item.get("text_preview") or "").split())[:_PREVIEW_CHARS]

    events: List[dict] = []

    # 1. It arrived. Always true, always first.
    events.append(_event(
        when, RECEIVED, group,
        why=[role_label, preview] if preview else [role_label],
        ticket=ticket))

    # 2. What was read out of it -- only fields that actually exist.
    parsed_why = [f"Company: {company}" if company else None,
                  f"Area: {area}" if area else None,
                  f"Ticket: {ticket}" if ticket else None]
    if any(parsed_why):
        if missing:
            parsed_why.append("Missing: " + ", ".join(missing) + " (never invented)")
        events.append(_event(when, PARSED, "Job facts read", why=parsed_why, ticket=ticket))

    # 3. Correlation, only when there is a ticket to correlate on.
    if ticket:
        job_id = item.get("proposed_job_id") or item.get("existing_job_id")
        events.append(_event(
            when, CORRELATED, ticket,
            why=[f"Confidence: {confidence}" if confidence else None,
                 f"Matches job #{job_id}" if job_id else None],
            ticket=ticket, confidence=confidence))

    # 4. The brain's own verdict, verbatim.
    if interpretation:
        kind = event_type_for(interpretation, next_action)
        review = needs_human(next_action)
        why = [f"Company: {company}" if company else None,
               f"Area: {area}" if area else None,
               f"Technician group: {technician}" if technician else None,
               f"Routing role: {role_label}" if role_label else None]
        if item.get("mapping_status") == "CONFIRMED":
            why.append("Owner-confirmed group mapping")
        events.append(_event(when, kind, interpretation, why=why,
                             ticket=ticket, confidence=confidence,
                             needs_human=review))

    # 5. What happens next -- always explicit, never implied.
    if next_action:
        events.append(_event(when, SYSTEM, f"NEXT: {next_action}", ticket=ticket,
                             needs_human=needs_human(next_action)))

    return events


def feed_from_items(items: List[dict], limit: int = 60) -> List[dict]:
    """Flatten many interpreted observations into one newest-first feed.
    `items` is expected newest-first already; the per-observation event
    order is preserved within each observation so a reader sees
    RECEIVED -> PARSED -> CORRELATED -> verdict -> NEXT."""
    out: List[dict] = []
    for item in items:
        out.extend(events_for(item))
        if len(out) >= limit:
            break
    return out[:limit]
