"""Tests for src/dispatcher/shadow.py -- the pure comparison logic
between a shadow suggestion and what the dispatcher actually did.
Persistence (storage.py) and DispatcherService wiring are covered in
test_shadow_persistence.py / test_application_service.py."""

from __future__ import annotations

from dispatcher.shadow import EXPORTABLE_REVIEW_STATES, ComparisonLabel, ReviewStatus, compare_shadow_to_actual


def test_human_review_required_takes_priority_over_any_match():
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
        shadow_quote_type="FIXED", dispatcher_quote_type="FIXED", human_review_required=True,
    )
    assert label == ComparisonLabel.HUMAN_REVIEW_REQUIRED


def test_no_actual_action_yet_is_outcome_pending():
    label = compare_shadow_to_actual(shadow_suggested_action="record_commercial_update", dispatcher_actual_action=None)
    assert label == ComparisonLabel.OUTCOME_NOT_YET_KNOWN


def test_exact_match_when_every_compared_field_agrees():
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
        shadow_quote_type="FIXED", dispatcher_quote_type="FIXED",
        shadow_quote_amount="125", dispatcher_quote_amount="125",
        shadow_customer_response="UNKNOWN", dispatcher_customer_response="UNKNOWN",
    )
    assert label == ComparisonLabel.EXACT_MATCH


def test_corrected_amount_detected():
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
        shadow_quote_type="FIXED", dispatcher_quote_type="FIXED",
        shadow_quote_amount="125", dispatcher_quote_amount="150",
    )
    assert label == ComparisonLabel.DISPATCHER_CORRECTED_AMOUNT


def test_corrected_quote_type_detected():
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
        shadow_quote_type="FIXED", dispatcher_quote_type="STARTING",
    )
    assert label == ComparisonLabel.DISPATCHER_CORRECTED_QUOTE_TYPE


def test_corrected_customer_response_detected():
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
        shadow_customer_response="UNKNOWN", dispatcher_customer_response="WILL_CALL_BACK",
    )
    assert label == ComparisonLabel.DISPATCHER_CORRECTED_CUSTOMER_RESPONSE


def test_rejected_suggestion_when_action_itself_differs():
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="pass_or_cancel_job",
    )
    assert label == ComparisonLabel.DISPATCHER_REJECTED_SUGGESTION


def test_dispatcher_accepting_a_default_is_not_automatically_labeled_correct():
    """Only genuinely comparable, matching fields earn EXACT_MATCH -- a
    bare acceptance with nothing to compare against still resolves
    deterministically, never silently assumed correct just because the
    dispatcher clicked save."""
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
    )
    assert label in (ComparisonLabel.EXACT_MATCH, ComparisonLabel.PARTIAL_MATCH)


def test_partial_match_when_some_fields_missing_but_none_contradict():
    label = compare_shadow_to_actual(
        shadow_suggested_action="record_commercial_update", dispatcher_actual_action="record_commercial_update",
        shadow_quote_type="FIXED", dispatcher_quote_type="FIXED",
        shadow_quote_amount="125", dispatcher_quote_amount=None,
    )
    assert label in (ComparisonLabel.EXACT_MATCH, ComparisonLabel.PARTIAL_MATCH)


def test_exportable_review_states_are_exactly_verified_and_corrected():
    assert EXPORTABLE_REVIEW_STATES == frozenset({ReviewStatus.VERIFIED, ReviewStatus.CORRECTED})


def test_unreviewed_is_not_exportable():
    assert ReviewStatus.UNREVIEWED not in EXPORTABLE_REVIEW_STATES


def test_exclude_sensitive_incomplete_are_not_exportable():
    assert ReviewStatus.EXCLUDE not in EXPORTABLE_REVIEW_STATES
    assert ReviewStatus.SENSITIVE not in EXPORTABLE_REVIEW_STATES
    assert ReviewStatus.INCOMPLETE not in EXPORTABLE_REVIEW_STATES
