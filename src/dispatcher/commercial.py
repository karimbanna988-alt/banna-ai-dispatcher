"""Deterministic extraction of quote/negotiation facts from a
technician's real WhatsApp text, and deterministic company-facing reply
preparation from those facts.

Sibling to updates.py (same "never invent a status" discipline, same
StandardReply return shape for the prepared-reply side) rather than a
replacement for it -- updates.py answers "what is the technician's
status", this module answers "what did the technician quote and how is
the customer responding". Neither infers a price, a currency, or a
negotiation outcome beyond what the text actually states.

DEFAULT_CURRENCY is a disclosed, single-country business assumption
(this business only ever quotes USD) applied only when preparing a
company-facing reply -- extraction itself (extract_commercial_update)
never invents a currency; `currency` stays None unless the technician's
own text used "$".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .updates import StandardReply

DEFAULT_CURRENCY = "USD"
_CURRENCY_SYMBOL = {"USD": "$"}


class QuoteType(str, Enum):
    FIXED = "FIXED"
    STARTING = "STARTING"
    RANGE = "RANGE"
    ESTIMATE = "ESTIMATE"
    NO_AMOUNT = "NO_AMOUNT"
    UNKNOWN = "UNKNOWN"


class CustomerResponseType(str, Enum):
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    WILL_CALL_BACK = "WILL_CALL_BACK"
    THINKING = "THINKING"
    TOO_EXPENSIVE = "TOO_EXPENSIVE"
    SHOPPING_AROUND = "SHOPPING_AROUND"
    REQUESTED_DISCOUNT = "REQUESTED_DISCOUNT"
    NO_RESPONSE = "NO_RESPONSE"
    UNKNOWN = "UNKNOWN"


class NegotiationState(str, Enum):
    QUOTE_GIVEN = "QUOTE_GIVEN"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    NEGOTIATING = "NEGOTIATING"
    ACCEPTED = "ACCEPTED"
    LOST_ON_PRICE = "LOST_ON_PRICE"
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CommercialUpdate:
    raw_technician_text: str
    quote_type: QuoteType = QuoteType.UNKNOWN
    quote_amount: Optional[str] = None
    quote_amount_text: Optional[str] = None
    quote_range_low: Optional[str] = None
    quote_range_high: Optional[str] = None
    currency: Optional[str] = None
    customer_response: CustomerResponseType = CustomerResponseType.UNKNOWN
    customer_response_text: Optional[str] = None
    follow_up_required: bool = False
    follow_up_time_text: Optional[str] = None
    negotiation_state: NegotiationState = NegotiationState.UNKNOWN
    confidence: float = 0.0
    validation_issues: List[str] = field(default_factory=list)
    human_review_required: bool = False


_NUM = r"\$?(?P<amt>\d+(?:\.\d+)?)"

_RANGE_PATTERNS = [
    re.compile(r"between\s+\$?(?P<low>\d+(?:\.\d+)?)\s+and\s+\$?(?P<high>\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\$?(?P<low>\d+(?:\.\d+)?)\s*(?:-|–|to)\s*\$?(?P<high>\d+(?:\.\d+)?)", re.IGNORECASE),
]
_STARTING_PATTERNS = [
    re.compile(r"start(?:s|ing)?\s*(?:at\s*)?" + _NUM, re.IGNORECASE),
    re.compile(r"\$?(?P<amt>\d+(?:\.\d+)?)\s*(?:and up|&\s*up|\+)", re.IGNORECASE),
]
_ESTIMATE_PATTERNS = [
    re.compile(r"(?:around|approx(?:imately)?|about)\s*" + _NUM, re.IGNORECASE),
    re.compile(r"estimate(?:d)?\s*(?:of\s*)?" + _NUM, re.IGNORECASE),
]
_FIXED_PATTERNS = [
    re.compile(r"quot(?:ed|e)\s*" + _NUM, re.IGNORECASE),
    re.compile(r"gave\s+price\s*" + _NUM, re.IGNORECASE),
    re.compile(r"price\s+is\s*" + _NUM, re.IGNORECASE),
]
_ANY_NUMBER = re.compile(r"\$?\d+(?:\.\d+)?")
_HAS_DOLLAR_NEAR = re.compile(r"\$\d")


def _extract_quote(text: str) -> tuple:
    """Returns (quote_type, amount, amount_text, low, high, currency,
    human_review_required). Checked in order most-specific-first so
    "starting 125" never matches the plain FIXED pattern, "125-175"
    never matches FIXED, etc."""
    for pat in _RANGE_PATTERNS:
        m = pat.search(text)
        if m:
            low, high = m.group("low"), m.group("high")
            currency = DEFAULT_CURRENCY if "$" in m.group(0) else None
            return QuoteType.RANGE, None, None, low, high, currency, False

    for pat in _STARTING_PATTERNS:
        m = pat.search(text)
        if m:
            amt = m.group("amt")
            currency = DEFAULT_CURRENCY if "$" in m.group(0) else None
            return QuoteType.STARTING, amt, amt, None, None, currency, False

    for pat in _ESTIMATE_PATTERNS:
        m = pat.search(text)
        if m:
            amt = m.group("amt")
            currency = DEFAULT_CURRENCY if "$" in m.group(0) else None
            return QuoteType.ESTIMATE, amt, amt, None, None, currency, False

    for pat in _FIXED_PATTERNS:
        m = pat.search(text)
        if m:
            amt = m.group("amt")
            currency = DEFAULT_CURRENCY if "$" in m.group(0) else None
            return QuoteType.FIXED, amt, amt, None, None, currency, False

    if _ANY_NUMBER.search(text):
        # A number is present but none of the known phrasings matched --
        # ambiguous, never guessed. See docs: "Ambiguous wording must
        # produce HUMAN_REVIEW_NEEDED."
        return QuoteType.UNKNOWN, None, None, None, None, None, True

    return QuoteType.NO_AMOUNT, None, None, None, None, None, False


_CUSTOMER_RESPONSE_PATTERNS = [
    (CustomerResponseType.WILL_CALL_BACK, re.compile(r"(?:will|gonna|going to)\s+call\s*(?:back|us)?\b|call\s*back", re.IGNORECASE)),
    (CustomerResponseType.TOO_EXPENSIVE, re.compile(r"too\s+expensive|too\s+high|price\s+too\s+high", re.IGNORECASE)),
    (CustomerResponseType.SHOPPING_AROUND, re.compile(r"shop(?:ping)?\s+around", re.IGNORECASE)),
    (CustomerResponseType.REQUESTED_DISCOUNT, re.compile(r"(?:wants?|asked\s+for|requested)\s+(?:a\s+)?discount", re.IGNORECASE)),
    (CustomerResponseType.DECLINED, re.compile(r"customer\s+(?:said\s+)?(?:they\s+|he\s+|she\s+)?declin(?:ed|e)", re.IGNORECASE)),
    (CustomerResponseType.ACCEPTED, re.compile(r"customer\s+(?:said\s+)?(?:they\s+|he\s+|she\s+)?accept(?:ed)?", re.IGNORECASE)),
    (CustomerResponseType.THINKING, re.compile(r"(?:customer\s+is\s+|customer\s+)?think(?:ing)?(?:\s+it\s+over)?", re.IGNORECASE)),
    (CustomerResponseType.NO_RESPONSE, re.compile(r"no\s+response", re.IGNORECASE)),
]


def _extract_customer_response(text: str) -> tuple:
    for response_type, pat in _CUSTOMER_RESPONSE_PATTERNS:
        m = pat.search(text)
        if m:
            return response_type, m.group(0)
    return CustomerResponseType.UNKNOWN, None


def derive_negotiation_state(quote_type: QuoteType, response: CustomerResponseType) -> tuple:
    """Returns (negotiation_state, follow_up_required). Deliberately
    never returns LOST_ON_PRICE from "too expensive"/"shopping around"
    alone -- those are negotiation signals, not a cancellation, per the
    explicit business rule. LOST_ON_PRICE only follows an explicit
    DECLINED, or a later record_negotiation_outcome() call the
    dispatcher makes once the outcome is actually known."""
    if response == CustomerResponseType.ACCEPTED:
        return NegotiationState.ACCEPTED, False
    if response == CustomerResponseType.DECLINED:
        return NegotiationState.LOST_ON_PRICE, False
    if response == CustomerResponseType.WILL_CALL_BACK:
        return NegotiationState.FOLLOW_UP_REQUIRED, True
    if response == CustomerResponseType.THINKING:
        return NegotiationState.WAITING_FOR_CUSTOMER, False
    if response == CustomerResponseType.NO_RESPONSE:
        return NegotiationState.WAITING_FOR_CUSTOMER, False
    if response in (CustomerResponseType.TOO_EXPENSIVE, CustomerResponseType.SHOPPING_AROUND, CustomerResponseType.REQUESTED_DISCOUNT):
        return NegotiationState.NEGOTIATING, False
    if quote_type not in (QuoteType.UNKNOWN, QuoteType.NO_AMOUNT):
        return NegotiationState.QUOTE_GIVEN, False
    return NegotiationState.UNKNOWN, False


def extract_commercial_update(raw_technician_text: str) -> CommercialUpdate:
    """The one deterministic authority for turning a technician's real
    WhatsApp text into structured commercial fields. Never modifies,
    rounds, or reformats the amount text it finds -- `quote_amount`/
    `quote_range_low`/`quote_range_high` are exactly the digits matched,
    `raw_technician_text` is always preserved verbatim."""
    text = (raw_technician_text or "").strip()
    if not text:
        return CommercialUpdate(
            raw_technician_text=raw_technician_text or "",
            validation_issues=["raw_technician_text is required"],
            human_review_required=True,
        )

    quote_type, amount, amount_text, low, high, currency, quote_ambiguous = _extract_quote(text)
    response, response_text = _extract_customer_response(text)
    negotiation_state, follow_up_required = derive_negotiation_state(quote_type, response)

    validation_issues: List[str] = []
    if quote_ambiguous:
        validation_issues.append("a number was found but the wording did not match a known quote phrasing")

    confidence = 0.0
    if quote_type not in (QuoteType.UNKNOWN, QuoteType.NO_AMOUNT):
        confidence += 0.6
    elif quote_type == QuoteType.NO_AMOUNT:
        confidence += 0.3
    if response != CustomerResponseType.UNKNOWN:
        confidence += 0.4
    confidence = min(confidence, 1.0)

    return CommercialUpdate(
        raw_technician_text=raw_technician_text,
        quote_type=quote_type,
        quote_amount=amount,
        quote_amount_text=amount_text,
        quote_range_low=low,
        quote_range_high=high,
        currency=currency,
        customer_response=response,
        customer_response_text=response_text,
        follow_up_required=follow_up_required,
        follow_up_time_text=None,
        negotiation_state=negotiation_state,
        confidence=confidence,
        validation_issues=validation_issues,
        human_review_required=quote_ambiguous,
    )


def _fmt_amount(amount: Optional[str], currency: Optional[str]) -> str:
    if amount is None:
        return ""
    symbol = _CURRENCY_SYMBOL.get(currency or DEFAULT_CURRENCY, "")
    return f"{symbol}{amount}"


_CUSTOMER_RESPONSE_CLAUSE = {
    CustomerResponseType.WILL_CALL_BACK: "Customer said they will call back.",
    CustomerResponseType.THINKING: "Customer is thinking it over.",
    CustomerResponseType.TOO_EXPENSIVE: "Customer said the price was too high.",
    CustomerResponseType.SHOPPING_AROUND: "Customer said they are shopping around.",
    CustomerResponseType.REQUESTED_DISCOUNT: "Customer asked for a discount.",
    CustomerResponseType.ACCEPTED: "Customer accepted.",
    CustomerResponseType.DECLINED: "Customer declined.",
    CustomerResponseType.NO_RESPONSE: "No response from customer yet.",
}


def prepare_commercial_company_reply(source: str, update: CommercialUpdate) -> StandardReply:
    """Deterministic company-facing reply from an already-extracted
    CommercialUpdate. `source` is accepted (not currently branched on --
    no existing per-company commercial-wording convention was found
    anywhere in this codebase to extend; see final report) so a future
    company-specific template can be added here without a signature
    change. Always comes back unconfirmed for UNKNOWN/ambiguous input --
    matches updates.reply_for_update_type()'s "never invents" contract."""
    if update.human_review_required or update.quote_type == QuoteType.UNKNOWN:
        return StandardReply(
            reply=None, confirmed=False, raw_input=update.raw_technician_text,
            reason="ambiguous commercial update -- requires human review before a company reply can be sent",
        )

    parts: List[str] = []
    if update.quote_type == QuoteType.FIXED:
        parts.append(f"Technician quoted {_fmt_amount(update.quote_amount, update.currency)}.")
    elif update.quote_type == QuoteType.STARTING:
        parts.append(f"Technician advised pricing starts at {_fmt_amount(update.quote_amount, update.currency)}.")
    elif update.quote_type == QuoteType.RANGE:
        low = _fmt_amount(update.quote_range_low, update.currency)
        high = _fmt_amount(update.quote_range_high, update.currency)
        parts.append(f"Technician quoted a range of {low} to {high}.")
    elif update.quote_type == QuoteType.ESTIMATE:
        parts.append(f"Technician gave an estimate of {_fmt_amount(update.quote_amount, update.currency)}.")
    elif update.quote_type == QuoteType.NO_AMOUNT:
        parts.append("Technician has not provided a price yet.")

    clause = _CUSTOMER_RESPONSE_CLAUSE.get(update.customer_response)
    if clause:
        parts.append(clause)

    return StandardReply(
        reply=" ".join(parts), confirmed=True, raw_input=update.raw_technician_text,
        reason="deterministic commercial-update reply",
    )


class CompanyQuestionType(str, Enum):
    DID_YOU_QUOTE = "DID_YOU_QUOTE"
    WHAT_PRICE = "WHAT_PRICE"
    CUSTOMER_AGREED = "CUSTOMER_AGREED"
    CUSTOMER_RESPONSE_STATUS = "CUSTOMER_RESPONSE_STATUS"
    CAN_DO_LESS = "CAN_DO_LESS"
    UNKNOWN = "UNKNOWN"


_COMPANY_QUESTION_PATTERNS = [
    (CompanyQuestionType.CAN_DO_LESS, re.compile(r"can\s+(?:the\s+)?(?:technician|tech|he|she)\s+do\s+less|lower\s+the\s+price|do\s+(?:any\s+)?better\s+on\s+price", re.IGNORECASE)),
    (CompanyQuestionType.WHAT_PRICE, re.compile(r"what\s+price|how\s+much\s+(?:was|is)\s+quoted|any\s+update\s+on\s+price", re.IGNORECASE)),
    (CompanyQuestionType.DID_YOU_QUOTE, re.compile(r"did\s+(?:you|technician|tech)\s+quote|did\s+you\s+give\s+a\s+price|give\s+a\s+price", re.IGNORECASE)),
    (CompanyQuestionType.CUSTOMER_AGREED, re.compile(r"did\s+customer\s+agree|customer\s+agree", re.IGNORECASE)),
    (CompanyQuestionType.CUSTOMER_RESPONSE_STATUS, re.compile(r"any\s+response\s+from\s+customer|is\s+customer\s+calling\s+back|did\s+(?:anyone|customer)\s+call|what\s+happened\s+with\s+the\s+customer", re.IGNORECASE)),
]


def detect_company_question(raw_text: str) -> CompanyQuestionType:
    text = (raw_text or "").strip()
    for question_type, pat in _COMPANY_QUESTION_PATTERNS:
        if pat.search(text):
            return question_type
    return CompanyQuestionType.UNKNOWN


def answer_company_commercial_question(
    question_type: CompanyQuestionType, latest_update: Optional[CommercialUpdate], source: str = "",
) -> StandardReply:
    """Answers only from the latest verified commercial update the
    dispatcher already recorded for this job -- never from another
    job, never from a stale quote once a corrected one exists (the
    caller is responsible for passing the *latest* one; see
    storage.get_latest_verified_commercial_update_for_job()).
    CAN_DO_LESS never negotiates automatically -- it always comes back
    unconfirmed with a technician-follow-up instruction."""
    if question_type == CompanyQuestionType.CAN_DO_LESS:
        return StandardReply(
            reply=None, confirmed=False, raw_input="",
            reason="cannot negotiate a price automatically -- contact the technician for a follow-up before replying",
        )

    if latest_update is None or latest_update.quote_type in (QuoteType.UNKNOWN, QuoteType.NO_AMOUNT):
        return StandardReply(
            reply=None, confirmed=False, raw_input="",
            reason="no verified price update is recorded for this job -- ask the technician for a price update",
        )

    if question_type in (CompanyQuestionType.DID_YOU_QUOTE, CompanyQuestionType.WHAT_PRICE):
        return prepare_commercial_company_reply(source, latest_update)

    if question_type == CompanyQuestionType.CUSTOMER_AGREED:
        if latest_update.customer_response == CustomerResponseType.ACCEPTED:
            return StandardReply(reply="Yes, customer accepted.", confirmed=True, raw_input="", reason="verified customer acceptance on record")
        if latest_update.customer_response == CustomerResponseType.DECLINED:
            return StandardReply(reply="No, customer declined.", confirmed=True, raw_input="", reason="verified customer decline on record")
        return StandardReply(
            reply=None, confirmed=False, raw_input="",
            reason="no confirmed customer acceptance/decline on record -- requires human review",
        )

    if question_type == CompanyQuestionType.CUSTOMER_RESPONSE_STATUS:
        clause = _CUSTOMER_RESPONSE_CLAUSE.get(latest_update.customer_response)
        if clause:
            return StandardReply(reply=clause, confirmed=True, raw_input="", reason="latest verified customer response on record")
        return StandardReply(
            reply=None, confirmed=False, raw_input="",
            reason="no confirmed customer response on record -- requires human review",
        )

    return StandardReply(
        reply=None, confirmed=False, raw_input="",
        reason="unrecognized company question -- requires human review",
    )
