"""Operator read-model: what is this job waiting for, in words?

WHY THIS EXISTS
The phone console needs an explicit waiting state per active job --
"WAITING FOR TECH K", "ETA 20 MIN", "CUSTOMER NO ANSWER" -- because an
operator glancing at a phone cannot infer that from a status enum.

WHY IT LIVES IN PYTHON AND NOT IN console.js
The label is derived from the technician's actual message text, and the
rules that read technician message text are business logic. They live
once, in `message_patterns`, and are shared by production interpretation,
the shadow tracer and the owner demo. Re-implementing "what counts as an
ETA" in JavaScript would fork that logic and let the phone disagree with
the brain. So the server computes the label and the phone renders it.

WHAT THIS IS NOT
This is a PRESENTATION projection over backend truth. It is not a state
machine, it decides nothing, it writes nothing, and it must never be
consulted by `storage.derive_job_status`. Production statuses stay
exactly as they are; this only chooses the words shown above a card.

PURITY
No connection, no SQL, no I/O. Inputs are already-fetched scalars.
"""
from __future__ import annotations

from typing import Optional

from dispatcher import message_patterns

# Tone maps to the console's existing badge classes. "rev"/"bad" are the
# two that visually demand attention, so anything a human must decide
# uses one of them.
TONE_NEUTRAL = ""
TONE_ACTION = "act"
TONE_REVIEW = "rev"
TONE_BAD = "bad"
TONE_OK = "ok"


def _r(label: str, tone: str, next_expected: str) -> dict:
    return {"label": label, "tone": tone, "next_expected": next_expected}


def _normalize_update(update):
    """The read model returns latest_technician_update as a DICT
    ({update_type, raw_message, eta_minutes, created_at}) or None --
    see luxor_desk_read_model.get_latest_technician_update. A bare
    string is also accepted so this stays usable from a test or a
    caller that only has the raw text.

    Returns (update_type, text, eta_minutes).
    """
    if not update:
        return None, "", None
    if isinstance(update, dict):
        return (update.get("update_type"),
                update.get("raw_message") or "",
                update.get("eta_minutes"))
    return None, str(update), None


def _from_update_type(update_type: str, text: str, eta_minutes) -> Optional[dict]:
    """A recorded update_type is a human's own classification of what the
    technician said. It outranks re-reading the text, because a human
    already decided it. Returns None for "Custom", which carries no
    classification and falls through to the shared patterns."""
    if update_type == "Completed":
        amount, method = message_patterns.extract_closing_amount(text)
        if amount:
            return _r(f"Closing ${amount} {method}".rstrip(), TONE_OK,
                      "the closing to be sent to the company")
        # Completed with no money facts is NOT a closing.
        return _r("Done - amount missing", TONE_REVIEW,
                  "an explicit amount and payment method")
    if update_type == "ETA":
        stated = f"{eta_minutes} min" if eta_minutes else message_patterns.stated_eta(text)
        return _r(f"ETA {stated}" if stated else "ETA stated", TONE_ACTION,
                  "the technician to arrive")
    if update_type == "Going":
        return _r("En route", TONE_ACTION, "arrival, then the closing")
    if update_type == "No Answer":
        return _r("Customer no answer", TONE_REVIEW,
                  "a human to contact the customer or company")
    if update_type == "No Show":
        return _r("Customer no show", TONE_REVIEW, "a human decision")
    if update_type == "Not Available":
        return _r("Tech not available", TONE_BAD, "reassignment by a human")
    if update_type == "Customer Cancelled":
        return _r("Customer cancelled", TONE_REVIEW, "a human to close this out")
    return None


def waiting_state(
    *,
    status: Optional[str] = None,
    bucket: Optional[str] = None,
    next_action: Optional[str] = None,
    latest_technician_update=None,
    company_update_owed: Optional[object] = None,
) -> dict:
    """One active job in, one {label, tone, next_expected} out.

    Ordering is a safety decision, not a style one: anything the backend
    has already flagged for a human wins over any text-derived reading,
    so a cheerful "ETA 20 MIN" can never paper over a job the system
    said it could not resolve.
    """
    na = (next_action or "").upper()
    update_type, text, eta_minutes = _normalize_update(latest_technician_update)

    # 1. The backend already said a human must decide. Nothing below may
    #    override that.
    if "HUMAN_REVIEW" in na:
        return _r("Human review", TONE_REVIEW, "a human decision")

    # 2. Declines and reassignment, from the backend label or from the
    #    technician's own words.
    if "DECLIN" in na or "REASSIGN" in na:
        return _r("Tech declined", TONE_BAD, "reassignment by a human")
    if text and message_patterns.is_decline(text):
        return _r("Tech declined", TONE_BAD, "reassignment by a human")

    # 3. A recorded update_type -- a human's own classification of the
    #    technician's message. Outranks re-reading the raw text.
    if update_type:
        from_type = _from_update_type(update_type, text, eta_minutes)
        if from_type:
            return from_type

    # 4. What the technician actually last said. Most specific first:
    #    a closing beats "done", and "done" beats a bare update.
    if text:
        amount, method = message_patterns.extract_closing_amount(text)
        if amount:
            return _r(f"Closing ${amount} {method}".rstrip(), TONE_OK,
                      "the closing to be sent to the company")
        if message_patterns.says_job_done(text):
            # Done with no amount is deliberately NOT a closing.
            return _r("Done - amount missing", TONE_REVIEW,
                      "an explicit amount and payment method")
        if message_patterns.is_no_answer(text):
            return _r("Customer no answer", TONE_REVIEW,
                      "a human to contact the customer or company")
        if message_patterns.mentions_quote(text):
            return _r("Price quoted", TONE_REVIEW,
                      "the customer to accept the quote")
        if message_patterns.is_en_route(text):
            eta = message_patterns.stated_eta(text)
            return _r(f"En route - {eta}" if eta else "En route", TONE_ACTION,
                      "arrival, then the closing")
        eta = message_patterns.stated_eta(text)
        if eta:
            return _r(f"ETA {eta}", TONE_ACTION, "the technician to arrive")
        if message_patterns.is_acknowledgement(text):
            return _r("Tech accepted", TONE_ACTION, "an ETA from the technician")

    # 5. Backend-declared next actions.
    if "K" in na.split("_") or "ACK" in na:
        return _r("Waiting for tech K", TONE_ACTION, "the technician to accept")
    if "UPDATE_COMPANY" in na:
        return _r("Update company", TONE_ACTION, "you to send the company update")
    if "CLOSING" in na:
        return _r("Waiting for closing", TONE_NEUTRAL, "the technician's closing report")

    # 6. A company update is owed even if no other signal fired.
    if company_update_owed:
        return _r("Update company", TONE_ACTION, "you to send the company update")

    # 7. Fall back to backend truth, never to a guess.
    if bucket:
        return _r(str(bucket).replace("_", " ").title(), TONE_NEUTRAL, "the next technician update")
    return _r(status or "In progress", TONE_NEUTRAL, "the next technician update")
