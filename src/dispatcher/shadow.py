"""Shadow Mode's typed model layer -- the passive observation/learning
bridge described in docs/SHADOW_MODE.md. This module defines shapes and
pure comparison logic only; all persistence lives in storage.py's
shadow_episodes/shadow_events tables (see
storage.find_or_create_shadow_episode / storage.record_shadow_event),
matching this codebase's existing convention that storage.py owns all
SQL and other modules stay framework/persistence-neutral.

Shadow Mode never sends anything and never changes a job's status by
itself -- see docs/SHADOW_MODE.md for the full list of things it must
never do. This module has no Streamlit dependency and no outbound
capability of any kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ShadowChannel(str, Enum):
    QUO_COMPANY = "QUO_COMPANY"
    WHATSAPP_TECHNICIAN = "WHATSAPP_TECHNICIAN"
    WHATSAPP_COMPANY = "WHATSAPP_COMPANY"
    CRM_DISPATCHER_ACTION = "CRM_DISPATCHER_ACTION"
    SYSTEM_SHADOW_SUGGESTION = "SYSTEM_SHADOW_SUGGESTION"


class ShadowEventType(str, Enum):
    SHADOW_OBSERVATION = "SHADOW_OBSERVATION"
    SHADOW_SUGGESTION = "SHADOW_SUGGESTION"
    DISPATCHER_DECISION = "DISPATCHER_DECISION"
    DISPATCHER_REPLY_CONFIRMED = "DISPATCHER_REPLY_CONFIRMED"
    COMMERCIAL_UPDATE = "COMMERCIAL_UPDATE"
    CUSTOMER_RESPONSE = "CUSTOMER_RESPONSE"
    NEGOTIATION_OUTCOME = "NEGOTIATION_OUTCOME"
    SHADOW_REVIEWED = "SHADOW_REVIEWED"
    SHADOW_EXCLUDED = "SHADOW_EXCLUDED"


class ReviewStatus(str, Enum):
    UNREVIEWED = "UNREVIEWED"
    VERIFIED = "VERIFIED"
    CORRECTED = "CORRECTED"
    EXCLUDE = "EXCLUDE"
    SENSITIVE = "SENSITIVE"
    INCOMPLETE = "INCOMPLETE"


# Only these review states may ever be exported -- see
# storage.list_shadow_episodes_for_export() and shadow_export.py.
EXPORTABLE_REVIEW_STATES = frozenset({ReviewStatus.VERIFIED, ReviewStatus.CORRECTED})


class ComparisonLabel(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    DISPATCHER_CORRECTED_AMOUNT = "DISPATCHER_CORRECTED_AMOUNT"
    DISPATCHER_CORRECTED_QUOTE_TYPE = "DISPATCHER_CORRECTED_QUOTE_TYPE"
    DISPATCHER_CORRECTED_CUSTOMER_RESPONSE = "DISPATCHER_CORRECTED_CUSTOMER_RESPONSE"
    DISPATCHER_REJECTED_SUGGESTION = "DISPATCHER_REJECTED_SUGGESTION"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    OUTCOME_NOT_YET_KNOWN = "OUTCOME_NOT_YET_KNOWN"


SHADOW_EPISODE_SCHEMA_VERSION = 1

# Every ShadowEpisode field the contract asks for. Deliberately a plain
# tuple of names, not a heavyweight dataclass with 30 constructor
# arguments the caller must all supply at once -- an episode is built up
# incrementally (see storage.find_or_create_shadow_episode() then
# storage.update_shadow_episode()), so most fields start unset.
EPISODE_FIELDS = (
    "episode_id", "source", "channel", "source_message_id", "pending_message_id",
    "job_id", "ticket_or_pdl", "dispatcher_actor", "technician_id", "previous_job_status",
    "incoming_message_type", "normalized_intent",
    "shadow_suggested_action", "shadow_suggested_reply", "shadow_confidence", "shadow_reasons",
    "validation_issues", "human_review_required",
    "dispatcher_actual_action", "dispatcher_actual_reply", "actual_send_channel", "actual_send_confirmed_at",
    "technician_followup", "customer_response", "negotiation_outcome", "final_job_outcome",
    "created_at", "updated_at", "reviewed_at", "review_status", "eligible_for_export", "schema_version",
)


@dataclass(frozen=True)
class ShadowEpisode:
    """In-memory representation of one shadow_episodes row -- returned
    by storage functions as a plain dict in practice (matching every
    other *_by_id() function in storage.py); this dataclass documents
    the exact contract and is used directly by pure comparison/export
    logic in this module and in tests."""

    episode_id: str
    source: str
    channel: str
    schema_version: int = SHADOW_EPISODE_SCHEMA_VERSION
    source_message_id: Optional[str] = None
    pending_message_id: Optional[str] = None
    job_id: Optional[int] = None
    ticket_or_pdl: Optional[str] = None
    dispatcher_actor: Optional[str] = None
    technician_id: Optional[int] = None
    previous_job_status: Optional[str] = None
    incoming_message_type: Optional[str] = None
    normalized_intent: Optional[str] = None
    shadow_suggested_action: Optional[str] = None
    shadow_suggested_reply: Optional[str] = None
    shadow_confidence: Optional[float] = None
    shadow_reasons: List[str] = field(default_factory=list)
    validation_issues: List[str] = field(default_factory=list)
    human_review_required: bool = False
    dispatcher_actual_action: Optional[str] = None
    dispatcher_actual_reply: Optional[str] = None
    actual_send_channel: Optional[str] = None
    actual_send_confirmed_at: Optional[str] = None
    technician_followup: Optional[str] = None
    customer_response: Optional[str] = None
    negotiation_outcome: Optional[str] = None
    final_job_outcome: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_status: str = ReviewStatus.UNREVIEWED.value
    eligible_for_export: bool = False


def compare_shadow_to_actual(
    *,
    shadow_suggested_action: Optional[str],
    dispatcher_actual_action: Optional[str],
    shadow_quote_type: Optional[str] = None,
    dispatcher_quote_type: Optional[str] = None,
    shadow_quote_amount: Optional[str] = None,
    dispatcher_quote_amount: Optional[str] = None,
    shadow_customer_response: Optional[str] = None,
    dispatcher_customer_response: Optional[str] = None,
    human_review_required: bool = False,
) -> ComparisonLabel:
    """Deterministic comparison between what Shadow Mode suggested and
    what the dispatcher actually did/confirmed. A dispatcher accepting a
    default is EXACT_MATCH only when every compared field genuinely
    matches -- this function never assumes agreement; every branch
    checks an actual value pair. HUMAN_REVIEW_REQUIRED takes priority
    over any match/mismatch verdict, since a suggestion the system
    itself flagged as unreliable cannot be scored as correct."""
    if human_review_required:
        return ComparisonLabel.HUMAN_REVIEW_REQUIRED

    if dispatcher_actual_action is None:
        return ComparisonLabel.OUTCOME_NOT_YET_KNOWN

    if shadow_suggested_action is None:
        return ComparisonLabel.OUTCOME_NOT_YET_KNOWN

    if dispatcher_quote_type is not None and shadow_quote_type is not None and dispatcher_quote_type != shadow_quote_type:
        return ComparisonLabel.DISPATCHER_CORRECTED_QUOTE_TYPE
    if dispatcher_quote_amount is not None and shadow_quote_amount is not None and dispatcher_quote_amount != shadow_quote_amount:
        return ComparisonLabel.DISPATCHER_CORRECTED_AMOUNT
    if (
        dispatcher_customer_response is not None
        and shadow_customer_response is not None
        and dispatcher_customer_response != shadow_customer_response
    ):
        return ComparisonLabel.DISPATCHER_CORRECTED_CUSTOMER_RESPONSE

    if shadow_suggested_action != dispatcher_actual_action:
        return ComparisonLabel.DISPATCHER_REJECTED_SUGGESTION

    fields_compared = [
        (shadow_quote_type, dispatcher_quote_type),
        (shadow_quote_amount, dispatcher_quote_amount),
        (shadow_customer_response, dispatcher_customer_response),
    ]
    considered = [(s, d) for s, d in fields_compared if s is not None or d is not None]
    if considered and all(s == d for s, d in considered):
        return ComparisonLabel.EXACT_MATCH
    if considered:
        return ComparisonLabel.PARTIAL_MATCH
    return ComparisonLabel.EXACT_MATCH
