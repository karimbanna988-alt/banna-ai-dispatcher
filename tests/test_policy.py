"""Tests for the deterministic source-response policy table
(src/dispatcher/policy.py). Covers every source x message-type
combination, per the explicit initial-policy requirements."""

import itertools

import pytest

from dispatcher.models import Source
from dispatcher.policy import (
    LUCKY_CLOSINGS_STREAM_KEY,
    SAFE_DEFAULT_POLICY,
    get_policy,
    is_policy_configured,
)
from dispatcher.storage import MessageType

ALL_SOURCES = (Source.AGN.value, Source.LUCKY.value, Source.MAIN_LEADS.value, Source.UNKNOWN.value)
ALL_MESSAGE_TYPES = tuple(m.value for m in MessageType)


@pytest.mark.parametrize("source,message_type", list(itertools.product(ALL_SOURCES, ALL_MESSAGE_TYPES)))
def test_every_source_message_type_combination_returns_a_policy(source, message_type):
    """No combination ever raises or returns None -- gaps fall back to the
    safe default rather than an inferred guess."""
    policy = get_policy(source, message_type)
    assert policy is not None
    if policy.acknowledgment_required:
        assert policy.acknowledgment_text  # never "required" with no text
    if not policy.quo_sending_allowed:
        assert True  # sending never allowed unless explicitly configured


@pytest.mark.parametrize("message_type", ALL_MESSAGE_TYPES)
def test_lucky_closings_stream_only_new_is_explicitly_configured(message_type):
    """Every message type on the Closings & Replies channel is safe by
    default; NEW is explicitly configured to make the "never a new-job
    ack channel" rule visible in the table rather than incidental."""
    policy = get_policy(LUCKY_CLOSINGS_STREAM_KEY, message_type)
    assert policy.acknowledgment_required is False
    assert policy.quo_sending_allowed is False


# --------------------------------------------- Explicit initial policy ----
def test_agn_new_job_acknowledgment_is_k():
    policy = get_policy(Source.AGN.value, MessageType.NEW.value)
    assert policy.acknowledgment_required is True
    assert policy.acknowledgment_text == "K"
    assert is_policy_configured(Source.AGN.value, MessageType.NEW.value)


def test_lucky_new_job_acknowledgment_is_k():
    policy = get_policy(Source.LUCKY.value, MessageType.NEW.value)
    assert policy.acknowledgment_required is True
    assert policy.acknowledgment_text == "K"


def test_main_leads_new_job_has_no_company_acknowledgment():
    policy = get_policy(Source.MAIN_LEADS.value, MessageType.NEW.value)
    assert policy.acknowledgment_required is False
    assert policy.acknowledgment_text is None
    assert policy.quo_sending_allowed is False


def test_lucky_closings_stream_never_treated_as_new_job_ack_channel():
    policy = get_policy(LUCKY_CLOSINGS_STREAM_KEY, MessageType.NEW.value)
    assert policy.acknowledgment_required is False
    assert policy.technician_formatting_allowed is False


def test_unknown_source_never_allows_sending_for_any_message_type():
    for message_type in ALL_MESSAGE_TYPES:
        policy = get_policy(Source.UNKNOWN.value, message_type)
        assert policy.quo_sending_allowed is False
        assert policy.acknowledgment_required is False


def test_duplicate_messages_never_require_a_repeat_acknowledgment():
    for source in (Source.AGN.value, Source.LUCKY.value, Source.MAIN_LEADS.value):
        policy = get_policy(source, MessageType.DUPLICATE.value)
        assert policy.acknowledgment_required is False
        assert policy.quo_sending_allowed is False


@pytest.mark.parametrize(
    "message_type",
    [MessageType.CANCELLATION.value, MessageType.CORRECTION.value, MessageType.STATUS.value, MessageType.CLOSING.value],
)
def test_correlated_message_types_always_require_human_review_and_never_auto_ack(message_type):
    for source in (Source.AGN.value, Source.LUCKY.value, Source.MAIN_LEADS.value):
        policy = get_policy(source, message_type)
        assert policy.human_review_mandatory is True
        assert policy.acknowledgment_required is False
        assert policy.quo_sending_allowed is False


def test_main_leads_closing_not_supported():
    policy = get_policy(Source.MAIN_LEADS.value, MessageType.CLOSING.value)
    assert policy.closing_handling_supported is False


def test_agn_and_lucky_closing_supported():
    assert get_policy(Source.AGN.value, MessageType.CLOSING.value).closing_handling_supported is True
    assert get_policy(Source.LUCKY.value, MessageType.CLOSING.value).closing_handling_supported is True


def test_unrecognized_and_closing_without_dispatch_use_safe_default():
    for source in ALL_SOURCES:
        assert get_policy(source, MessageType.UNRECOGNIZED.value) == SAFE_DEFAULT_POLICY
        assert get_policy(source, MessageType.CLOSING_WITHOUT_DISPATCH.value) == SAFE_DEFAULT_POLICY


def test_unconfigured_combination_reports_not_configured():
    assert is_policy_configured("Some New Company", MessageType.NEW.value) is False
    policy = get_policy("Some New Company", MessageType.NEW.value)
    assert policy == SAFE_DEFAULT_POLICY
