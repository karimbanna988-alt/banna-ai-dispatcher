"""The single source of truth for deterministic message recognition.

Every pattern here was derived by measuring real WhatsApp traffic during
the shadow shifts, and every one carries the false positive that shaped
it. They live in one module because they had started to diverge: the
production interpreter and the owner demo each grew their own copy of
the closing/ETA/en-route rules, which meant the demo could drift from
the behaviour it was supposed to demonstrate.

Deterministic only. No model, no inference, no scoring -- a message
either matches a measured shape or it does not, and "does not" is a
first-class answer that routes to a human.
"""
from __future__ import annotations

import re

from dispatcher import technician_ack

# ---------------------------------------------------------------- ACK ----
# Reused directly rather than re-listed, so the technician side and the
# dispatcher side can never disagree about what "K" means.
ACKNOWLEDGEMENT_TEXTS = technician_ack.ACKNOWLEDGEMENT_TEXTS
DECLINE_TEXTS = technician_ack.DECLINE_TEXTS

# ------------------------------------------------------------ MENTION ----
# A WhatsApp @mention with nothing else. Measured: 33 of 105 messages the
# old "any digit means ETA" rule called an ETA were exactly this, and
# they carry no status information at all.
MENTION_ONLY_RE = re.compile(r"^@\d+$")

# ---------------------------------------------------------------- ETA ----
# Requires real time language. Replaces "any digit means ETA", which was
# wrong ~94% of the time on live traffic -- it swept up phone numbers,
# street numbers, a VIN, price quotes and @mentions. Only 6 of 105 such
# messages actually stated a time.
# The optional leading quantity is captured so stated_eta() can report
# the whole phrase ("20 min", "1.5 hours") rather than the bare unit.
# Production only asks this pattern a yes/no question, so widening the
# match this way cannot change any classification -- it matches exactly
# the same set of messages, just more of each one.
ETA_TIME_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*)?(?:eta|min|mins|minute|minutes|hour|hours|hr|hrs)\b"
    r"|\b\d{1,2}:\d{2}\b",
    re.IGNORECASE,
)

# ----------------------------------------------------------- EN ROUTE ----
# Anchored to FIRST PERSON on purpose. The real false positive this
# excludes: "She has someone on the way" is the CUSTOMER having arranged
# another locksmith -- close to the opposite of this technician
# travelling. "heading" is also excluded: its only observed use was about
# a different, later job ("...the one i'm heading to").
EN_ROUTE_RE = re.compile(r"\b(on my way|omw)\b|^on the way\b", re.IGNORECASE)

# --------------------------------------------------------------- QUOTE ----
# A price discussed with the customer. Never parsed into an amount: a
# quote is a negotiation, not a settled figure, and quoting language is
# far looser than a closing report.
QUOTE_RE = re.compile(r"\b(quote|quoted|quoting)\b", re.IGNORECASE)

# ----------------------------------------------------------- NO ANSWER ----
# The customer could not be reached. Operationally distinct from a
# decline: the technician is not refusing, the job is stalling.
NO_ANSWER_RE = re.compile(
    r"(no answer|didn'?t answer|doesn'?t answer|not answering|no reply|voice ?mail)", re.IGNORECASE,
)

# Separates "dropping this job because they didn't answer" (reassign)
# from "they didn't answer, I'll retry" (update the company). Anchored to
# a leading "pass" specifically: a leading "no" cannot be the signal,
# because "No answer sent a text" begins with "No" and is a report, not
# a refusal.
PASS_PREFIX_RE = re.compile(r"^\s*pass\b", re.IGNORECASE)

# -------------------------------------------------------------- CLOSING ----
# An amount IMMEDIATELY followed by a payment word. Measured: 5 of 8 real
# closing messages carried exactly this adjacent shape. The adjacency
# requirement is what excludes the real false positive "Da keda
# additional to the $150", which mentions money but is not a closing.
# The multi-line Fees/Parts/Total format seen in 2 other messages is
# deliberately NOT parsed -- not enough repeated evidence yet.
CLOSING_AMOUNT_RE = re.compile(
    r"\$?\s*([\d,]+\.?\d*)\s+(cash|cc|card|venmo|zelle|check)\b", re.IGNORECASE,
)

# "The job is done" language, used only to tell "done but no amount
# stated" (ask a human) apart from an ordinary update.
CLOSING_WORDS_RE = re.compile(r"\b(done|finished|completed|closed|closing)\b", re.IGNORECASE)


def is_acknowledgement(text: str) -> bool:
    return (text or "").strip().lower() in ACKNOWLEDGEMENT_TEXTS


def is_decline(text: str) -> bool:
    return (text or "").strip().lower() in DECLINE_TEXTS


def is_mention_only(text: str) -> bool:
    return bool(MENTION_ONLY_RE.match((text or "").strip()))


def stated_eta(text: str):
    """The matched time phrase, or None. Never a guess."""
    m = ETA_TIME_RE.search(text or "")
    return m.group(0) if m else None


def is_en_route(text: str) -> bool:
    return bool(EN_ROUTE_RE.search((text or "").strip()))


def mentions_quote(text: str) -> bool:
    return bool(QUOTE_RE.search(text or ""))


def is_no_answer(text: str) -> bool:
    return bool(NO_ANSWER_RE.search(text or ""))


def is_pass_prefixed(text: str) -> bool:
    return bool(PASS_PREFIX_RE.search((text or "").strip()))


def says_job_done(text: str) -> bool:
    return bool(CLOSING_WORDS_RE.search(text or ""))


def extract_closing_amount(text: str):
    """(amount, payment_method) as the exact substrings matched, or
    (None, None). Never invents either half: a message stating only one
    of them yields neither, because a closing needs both."""
    m = CLOSING_AMOUNT_RE.search(text or "")
    if not m:
        return None, None
    return m.group(1), m.group(2).lower()
