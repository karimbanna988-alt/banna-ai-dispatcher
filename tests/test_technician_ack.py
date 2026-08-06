"""Focused tests for dispatcher.technician_ack -- pure classification
logic, no database, no I/O. See tests/test_application_service.py for
the DispatcherService integration (confirm_technician_accepted/declined)
and tests/test_operator_api.py for the route-level wiring.
"""

from __future__ import annotations

from dispatcher.technician_ack import (
    CATEGORY_AMBIGUOUS, CATEGORY_EXACT, CATEGORY_STRONG, CATEGORY_UNMATCHED,
    classify_candidate, classify_candidates,
)

JOB = {"ticket_number": "F400", "source": "AGN"}
TECH = {"name": "Demo Ashraf", "dispatch_group": "Demo Ashraf Dispatch"}


def obs(**overrides):
    base = {
        "message_id": "wamid.1", "group_name": "Demo Ashraf Dispatch", "text": "K",
        "extracted_ticket": None, "status": "pending",
    }
    base.update(overrides)
    return base


def test_exact_match_group_and_ticket():
    result = classify_candidate(JOB, TECH, obs(extracted_ticket="F400"))
    assert result.category == CATEGORY_EXACT
    assert result.is_decline_shaped is False


def test_strong_match_group_only_no_other_waiting_jobs():
    result = classify_candidate(JOB, TECH, obs())
    assert result.category == CATEGORY_STRONG


def test_ambiguous_when_multiple_jobs_waiting_and_no_ticket_named():
    result = classify_candidate(JOB, TECH, obs(), other_waiting_ticket_numbers=["F500"])
    assert result.category == CATEGORY_AMBIGUOUS


def test_unmatched_when_group_does_not_match():
    result = classify_candidate(JOB, TECH, obs(group_name="Some Other Group"))
    assert result.category == CATEGORY_UNMATCHED


def test_unmatched_when_text_is_not_acknowledgement_shaped():
    result = classify_candidate(JOB, TECH, obs(text="What time will the tech arrive?"))
    assert result.category == CATEGORY_UNMATCHED


def test_unmatched_when_message_references_a_different_ticket():
    result = classify_candidate(JOB, TECH, obs(extracted_ticket="F999"))
    assert result.category == CATEGORY_UNMATCHED


def test_decline_shaped_text_still_classified_by_group_evidence():
    # Exact-vocabulary match only, by design -- see technician_ack.py's
    # "never invent a status" reasoning (mirrors updates.py's own rule).
    result = classify_candidate(JOB, TECH, obs(text="can't"))
    assert result.is_decline_shaped is True
    assert result.category == CATEGORY_STRONG


def test_no_technician_assigned_yet_is_always_unmatched():
    result = classify_candidate(JOB, None, obs())
    assert result.category == CATEGORY_UNMATCHED


def test_classify_candidates_skips_already_resolved_observations():
    results = classify_candidates(JOB, TECH, [obs(message_id="a", status="pending"), obs(message_id="b", status="attached")])
    assert [r.message_id for r in results] == ["a"]


def test_never_invents_a_category_beyond_the_four():
    result = classify_candidate(JOB, TECH, obs())
    assert result.category in (CATEGORY_EXACT, CATEGORY_STRONG, CATEGORY_AMBIGUOUS, CATEGORY_UNMATCHED)


def test_reasons_are_always_populated_never_silent():
    for text, ticket, group in [("K", "F400", "Demo Ashraf Dispatch"), ("random chatter", None, "Wrong Group")]:
        result = classify_candidate(JOB, TECH, obs(text=text, extracted_ticket=ticket, group_name=group))
        assert result.reasons, "every classification must explain itself"
