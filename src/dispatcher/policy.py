"""Deterministic per-stream, per-message-type response policy.

One declarative table, not company-specific branches scattered through the
pipeline -- matches the existing company-profile registry pattern
(companies/__init__.py). Nothing here is inferred: any (stream,
message_type) combination not explicitly listed falls back to
SAFE_DEFAULT_POLICY (no acknowledgment, no sending, human review
mandatory) rather than guessing at a company's expectations.

This module defines policy only -- it never sends anything itself. See
luxor_outbound.py for the separate, disabled-by-default outbound
boundary that would eventually act on `quo_sending_allowed`.

"Lucky -- Closings & Replies" is a STREAM (a Quo contact polled), not a
Source company (see luxor_ops_shared.STREAMS) -- it is keyed separately
below via LUCKY_CLOSINGS_STREAM_KEY, since a message arriving on that
channel is never treated as a new-job acknowledgment regardless of which
Source it would otherwise fingerprint as.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .models import Source
from .storage import MessageType

LUCKY_CLOSINGS_STREAM_KEY = "lucky_closings_stream"


@dataclass(frozen=True)
class ResponsePolicy:
    acknowledgment_required: bool
    acknowledgment_text: Optional[str]
    quo_sending_allowed: bool
    technician_formatting_allowed: bool
    closing_handling_supported: bool
    human_review_mandatory: bool
    notes: str = ""


# Applied whenever no explicit entry exists below -- the safest possible
# policy (nothing sent, nothing formatted for a technician, always
# reviewed by a human).
SAFE_DEFAULT_POLICY = ResponsePolicy(
    acknowledgment_required=False,
    acknowledgment_text=None,
    quo_sending_allowed=False,
    technician_formatting_allowed=False,
    closing_handling_supported=False,
    human_review_mandatory=True,
    notes="No explicit policy configured for this source/message-type combination -- safest default applied.",
)

_KNOWN_SOURCES = (Source.AGN.value, Source.LUCKY.value, Source.MAIN_LEADS.value)

# Every entry's `quo_sending_allowed` is a *policy* opinion only -- the
# actual outbound module (luxor_outbound.py) additionally gates every send
# behind QUO_OUTBOUND_MODE (default "disabled"), a global kill switch, and
# a per-source allowlist. Setting this True here is necessary but never
# sufficient for a real send to happen.
_POLICY_TABLE: Dict[Tuple[str, str], ResponsePolicy] = {
    # ------------------------------------------------------- New jobs ----
    # AGN / Lucky valid new job -> acknowledgment candidate "K" (business-
    # confirmed text). Sending stays disabled until Phase 4's outbound
    # module is explicitly enabled and approved -- this only records the
    # *candidate* text.
    (Source.AGN.value, MessageType.NEW.value): ResponsePolicy(
        acknowledgment_required=True, acknowledgment_text="K", quo_sending_allowed=True,
        technician_formatting_allowed=True, closing_handling_supported=True, human_review_mandatory=True,
        notes='Business-confirmed candidate acknowledgment text "K".',
    ),
    (Source.LUCKY.value, MessageType.NEW.value): ResponsePolicy(
        acknowledgment_required=True, acknowledgment_text="K", quo_sending_allowed=True,
        technician_formatting_allowed=True, closing_handling_supported=True, human_review_mandatory=True,
        notes='Business-confirmed candidate acknowledgment text "K".',
    ),
    (Source.MAIN_LEADS.value, MessageType.NEW.value): ResponsePolicy(
        acknowledgment_required=False, acknowledgment_text=None, quo_sending_allowed=False,
        technician_formatting_allowed=True, closing_handling_supported=False, human_review_mandatory=True,
        notes="Main Leads jobs are our own ad-generated leads -- dispatched directly, no company acknowledgment sent back.",
    ),
    # "Lucky -- Closings & Replies" is never a new-job acknowledgment
    # channel, regardless of what Source a stray message on it would
    # otherwise fingerprint as.
    (LUCKY_CLOSINGS_STREAM_KEY, MessageType.NEW.value): ResponsePolicy(
        acknowledgment_required=False, acknowledgment_text=None, quo_sending_allowed=False,
        technician_formatting_allowed=False, closing_handling_supported=False, human_review_mandatory=True,
        notes="Lucky -- Closings & Replies is an operational reply channel, never a new-job acknowledgment channel.",
    ),
}

# Duplicate messages -> never repeat an acknowledgment unless a future
# policy explicitly allows it (none currently do).
for _source in _KNOWN_SOURCES:
    _POLICY_TABLE[(_source, MessageType.DUPLICATE.value)] = ResponsePolicy(
        acknowledgment_required=False, acknowledgment_text=None, quo_sending_allowed=False,
        technician_formatting_allowed=False, closing_handling_supported=False, human_review_mandatory=True,
        notes="Duplicate of the most recent message for this job -- no repeat acknowledgment.",
    )

# Cancellation / correction / status / update-request / closing must
# correlate to an existing job before any response is even considered --
# none of these are ever auto-acknowledged.
for _source in _KNOWN_SOURCES:
    for _mtype in (
        MessageType.CANCELLATION.value, MessageType.CORRECTION.value, MessageType.STATUS.value,
        MessageType.UPDATE_REQUEST.value, MessageType.CLOSING.value,
    ):
        _POLICY_TABLE[(_source, _mtype)] = ResponsePolicy(
            acknowledgment_required=False, acknowledgment_text=None, quo_sending_allowed=False,
            technician_formatting_allowed=(_mtype != MessageType.CLOSING.value),
            closing_handling_supported=(_mtype == MessageType.CLOSING.value and _source != Source.MAIN_LEADS.value),
            human_review_mandatory=True,
            notes="Must correlate to an existing job before any response is considered; never auto-acknowledged.",
        )

# Unrecognized / closing-without-dispatch / assignment -> always the
# safest default (explicit here so it's visible in the table rather than
# silently relying on the fallback for these specific anomaly types).
for _source in _KNOWN_SOURCES + (Source.UNKNOWN.value,):
    for _mtype in (MessageType.UNRECOGNIZED.value, MessageType.CLOSING_WITHOUT_DISPATCH.value, MessageType.ASSIGNMENT.value):
        _POLICY_TABLE[(_source, _mtype)] = SAFE_DEFAULT_POLICY

# Unknown source is never sent to or acknowledged, for any message type.
for _mtype in (m.value for m in MessageType):
    _POLICY_TABLE.setdefault((Source.UNKNOWN.value, _mtype), SAFE_DEFAULT_POLICY)


def get_policy(stream_key: str, message_type: str) -> ResponsePolicy:
    """`stream_key` is either a Source value (Main Leads/AGN/Lucky/Unknown)
    or LUCKY_CLOSINGS_STREAM_KEY for the Closings & Replies channel --
    never a raw Quo group_label. Missing/incomplete/conflicting messages
    (message_type not present in the table for this stream) receive
    SAFE_DEFAULT_POLICY, never an inferred one."""
    return _POLICY_TABLE.get((stream_key, message_type), SAFE_DEFAULT_POLICY)


def is_policy_configured(stream_key: str, message_type: str) -> bool:
    """True only when an explicit entry exists -- used to flag "configuration
    required" rather than silently treating a gap as an intentional
    safe-default decision."""
    return (stream_key, message_type) in _POLICY_TABLE
