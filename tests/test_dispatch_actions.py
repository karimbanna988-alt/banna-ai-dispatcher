"""Tests for the canonical proposal vocabulary and its safety gate."""
import pytest

from dispatcher import dispatch_actions as da
from dispatcher.action_names import ActionName


def test_confidence_gate_blocks_weak_evidence_from_proposing_an_action():
    """The bar that has held through every shadow shift: only EXACT or
    STRONG evidence may propose an operational action."""
    for weak in (da.AMBIGUOUS, da.UNMATCHED, None):
        assert da.gate(da.DISPATCH_TECHNICIAN, weak) == da.HUMAN_REVIEW_NEEDED


def test_confidence_gate_allows_strong_evidence():
    assert da.gate(da.DISPATCH_TECHNICIAN, da.EXACT) == da.DISPATCH_TECHNICIAN
    assert da.gate(da.UPDATE_COMPANY, da.STRONG) == da.UPDATE_COMPANY


def test_human_review_and_no_action_pass_through_any_confidence():
    assert da.gate(da.HUMAN_REVIEW_NEEDED, da.EXACT) == da.HUMAN_REVIEW_NEEDED
    assert da.gate(da.NO_ACTION, None) == da.NO_ACTION


def test_every_proposal_maps_to_a_real_action_or_is_explicitly_routing_only():
    """A proposal either becomes a recorded ActionName when accepted, or
    is deliberately non-committing. Nothing may be ambiguous about it."""
    routing_only = {da.ACKNOWLEDGE_COMPANY, da.PRICE_QUOTE_RECEIVED,
                    da.REQUEST_CLOSING_DETAILS, da.HUMAN_REVIEW_NEEDED, da.NO_ACTION}
    for action in da.ALL_ACTIONS:
        mapped = da.COMMITS_AS.get(action)
        assert (mapped is not None) ^ (action in routing_only), action
        if mapped is not None:
            assert isinstance(mapped, ActionName)


def test_a_proposal_needing_human_review_is_never_committable():
    p = da.Proposal(action=da.COMPLETE_JOB, confidence=da.EXACT, needs_human=True)
    assert p.is_committable is False


def test_human_review_proposal_has_no_commit_mapping():
    p = da.Proposal(action=da.HUMAN_REVIEW_NEEDED, confidence=da.AMBIGUOUS)
    assert p.commits_as is None
    assert p.is_committable is False


def test_a_clean_proposal_is_committable_and_maps_correctly():
    p = da.Proposal(action=da.TECHNICIAN_ACCEPTED, confidence=da.EXACT)
    assert p.commits_as == ActionName.TECHNICIAN_ACCEPTED
    assert p.is_committable is True


# ------------------------------------------------- demo state machine ----
def test_demo_cannot_complete_a_job_that_was_never_assigned():
    """A closing arriving before the technician ever accepted is not a
    valid path to COMPLETED -- the demo must not show that."""
    assert da.demo_transition_allowed(da.DEMO_NEW, da.DEMO_COMPLETED) is False


@pytest.mark.parametrize("current,proposed", [
    (da.DEMO_NEW, da.DEMO_ASSIGNED),
    (da.DEMO_ASSIGNED, da.DEMO_EN_ROUTE),
    (da.DEMO_EN_ROUTE, da.DEMO_COMPLETED),
    (da.DEMO_ASSIGNED, da.DEMO_COMPLETED),  # closed without a separate en-route message
])
def test_valid_demo_transitions(current, proposed):
    assert da.demo_transition_allowed(current, proposed) is True


def test_a_completed_demo_job_cannot_move_again():
    for target in (da.DEMO_NEW, da.DEMO_ASSIGNED, da.DEMO_EN_ROUTE):
        assert da.demo_transition_allowed(da.DEMO_COMPLETED, target) is False


def test_demo_states_are_not_the_production_status_values():
    """Demo states must stay distinguishable from jobs.status so a demo
    can never be mistaken for, or written as, real business truth."""
    from dispatcher import storage
    production = storage.VALID_JOB_STATUSES
    assert da.DEMO_EN_ROUTE not in production   # production has no EN_ROUTE
    assert da.DEMO_ASSIGNED not in production   # production uses "Approved"


def test_module_commits_nothing():
    import pathlib
    source = pathlib.Path(da.__file__).read_text(encoding="utf-8")
    for forbidden in ("sqlite3", "execute(", "save_audit_entry", "derive_job_status("):
        assert forbidden not in source
