"""Focused tests for dispatcher.workflow_projection -- pure logic, no
database, no I/O. Every next_action branch and precedence rule gets its
own test. See tests/test_operator_api.py for the route-level wiring
(the Action Center and Active-screen integration).
"""

from __future__ import annotations

from dispatcher.workflow_projection import (
    LIFECYCLE_STEPS, NEXT_ACTIONS, STEP_BLOCKED, STEP_COMPLETED, STEP_CURRENT, STEP_PENDING,
    derive_lifecycle_steps, derive_next_action, derive_pending_message_next_action,
)


def ctx(**overrides):
    base = {
        "status": "Approved",
        "reconciliation_state": None,
        "technician_name": "Demo Ashraf",
        "technician_k": None,
        "latest_technician_update": None,
        "company_update_owed": None,
        "closing": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- terminal --
def test_cancelled_is_complete_and_terminal():
    r = derive_next_action(ctx(status="Cancelled"))
    assert r.next_action == "COMPLETE"
    assert r.terminal is True
    assert r.human_review_needed is False


def test_rejected_is_complete_and_terminal():
    r = derive_next_action(ctx(status="Rejected"))
    assert r.next_action == "COMPLETE"
    assert r.terminal is True


def test_closed_and_settled_is_complete_and_terminal():
    r = derive_next_action(ctx(status="Closed", reconciliation_state="Settled"))
    assert r.next_action == "COMPLETE"
    assert r.terminal is True


def test_closed_but_not_settled_is_not_terminal():
    r = derive_next_action(ctx(status="Closed", reconciliation_state=None, closing={"missing": [], "supported": True, "sent_confirmed": True}))
    assert r.terminal is False


# ------------------------------------------------------- human review -----
def test_handle_manually_status_is_human_review():
    r = derive_next_action(ctx(status="Handle Manually"))
    assert r.next_action == "HUMAN_REVIEW_NEEDED"
    assert r.human_review_needed is True


def test_custom_technician_update_is_human_review():
    r = derive_next_action(ctx(status="In Progress", latest_technician_update={"update_type": "Custom"}))
    assert r.next_action == "HUMAN_REVIEW_NEEDED"


def test_technician_declined_is_human_review():
    r = derive_next_action(ctx(technician_k={"status": "declined"}))
    assert r.next_action == "HUMAN_REVIEW_NEEDED"


def test_all_ambiguous_k_candidates_is_human_review():
    r = derive_next_action(ctx(technician_k={"status": "waiting", "candidates": [{"category": "AMBIGUOUS"}]}))
    assert r.next_action == "HUMAN_REVIEW_NEEDED"


def test_mixed_ambiguous_and_strong_k_candidates_is_not_human_review():
    r = derive_next_action(ctx(technician_k={"status": "waiting", "candidates": [{"category": "AMBIGUOUS"}, {"category": "STRONG"}]}))
    assert r.next_action == "WAIT_TECHNICIAN_K"


def test_unsupported_closing_template_is_human_review():
    r = derive_next_action(ctx(status="Closed", closing={"missing": [], "supported": False, "unsupported_reason": "no template", "sent_confirmed": False}))
    assert r.next_action == "HUMAN_REVIEW_NEEDED"
    assert r.blocking_reason == "no template"


def test_human_review_takes_precedence_over_needs_review_status():
    """Handle Manually outranks everything else even if some other
    field would otherwise suggest a different action."""
    r = derive_next_action(ctx(status="Handle Manually", technician_name=None))
    assert r.next_action == "HUMAN_REVIEW_NEEDED"


# ---------------------------------------------------------- new job -------
def test_needs_review_status_is_review_new_job():
    r = derive_next_action(ctx(status="Needs Review"))
    assert r.next_action == "REVIEW_NEW_JOB"


# ------------------------------------------------------ assign technician -
def test_approved_no_technician_is_assign_technician():
    r = derive_next_action(ctx(status="Approved", technician_name=None))
    assert r.next_action == "ASSIGN_TECHNICIAN"


# --------------------------------------------------------- waiting on K ---
def test_approved_with_technician_no_update_is_wait_technician_k():
    r = derive_next_action(ctx(status="Approved", technician_name="Demo Ashraf", latest_technician_update=None))
    assert r.next_action == "WAIT_TECHNICIAN_K"


def test_wait_technician_k_reason_differs_once_accepted():
    waiting = derive_next_action(ctx(status="Approved", technician_k={"status": "waiting", "candidates": []}))
    accepted = derive_next_action(ctx(status="Approved", technician_k={"status": "accepted"}))
    assert waiting.next_action == accepted.next_action == "WAIT_TECHNICIAN_K"
    assert waiting.blocking_reason != accepted.blocking_reason


# ------------------------------------------------- review technician update
def test_completed_update_not_yet_closed_is_review_technician_update():
    r = derive_next_action(ctx(status="In Progress", latest_technician_update={"update_type": "Completed"}))
    assert r.next_action == "REVIEW_TECHNICIAN_UPDATE"


def test_completed_update_but_already_closed_does_not_reach_review_technician_update():
    r = derive_next_action(ctx(status="Closed", latest_technician_update={"update_type": "Completed"}, closing={"missing": [], "supported": True, "sent_confirmed": True}))
    assert r.next_action != "REVIEW_TECHNICIAN_UPDATE"


# ------------------------------------------------------- update company ---
def test_company_update_owed_takes_precedence_over_fallback():
    r = derive_next_action(ctx(status="In Progress", latest_technician_update={"update_type": "Going"}, company_update_owed={"suggested_reply": "Going"}))
    assert r.next_action == "UPDATE_COMPANY"


# ------------------------------------------------------------- closing ----
def test_closed_with_missing_fields_is_review_closing():
    r = derive_next_action(ctx(status="Closed", closing={"missing": ["amount"], "supported": None, "sent_confirmed": False}))
    assert r.next_action == "REVIEW_CLOSING"


def test_closed_complete_not_sent_is_send_closing_to_company():
    r = derive_next_action(ctx(status="Closed", closing={"missing": [], "supported": True, "sent_confirmed": False}))
    assert r.next_action == "SEND_CLOSING_TO_COMPANY"


def test_closed_sent_but_not_yet_settled_is_complete_not_terminal():
    r = derive_next_action(ctx(status="Closed", reconciliation_state=None, closing={"missing": [], "supported": True, "sent_confirmed": True}))
    assert r.next_action == "COMPLETE"
    assert r.terminal is False


# --------------------------------------------------------------- fallback -
def test_in_progress_no_update_owed_no_closing_falls_back_to_wait():
    r = derive_next_action(ctx(status="In Progress", latest_technician_update={"update_type": "Going"}, company_update_owed=None, closing=None))
    assert r.next_action == "WAIT_TECHNICIAN_K"


# ---------------------------------------------------------- vocabulary ----
def test_every_returned_next_action_is_in_the_fixed_vocabulary():
    scenarios = [
        ctx(status="Cancelled"), ctx(status="Rejected"), ctx(status="Closed", reconciliation_state="Settled"),
        ctx(status="Handle Manually"), ctx(status="Needs Review"), ctx(status="Approved", technician_name=None),
        ctx(status="Approved"), ctx(status="In Progress", latest_technician_update={"update_type": "Completed"}),
        ctx(status="In Progress", latest_technician_update={"update_type": "Going"}, company_update_owed={"x": 1}),
        ctx(status="Closed", closing={"missing": ["amount"], "supported": None, "sent_confirmed": False}),
        ctx(status="Closed", closing={"missing": [], "supported": True, "sent_confirmed": False}),
        ctx(status="Closed", closing={"missing": [], "supported": True, "sent_confirmed": True}),
    ]
    for c in scenarios:
        r = derive_next_action(c)
        assert r.next_action in NEXT_ACTIONS, f"{r.next_action!r} not in fixed vocabulary for {c}"


def test_ack_company_k_and_send_job_to_technician_never_returned_by_the_committed_job_function():
    """ACK_COMPANY_K and SEND_JOB_TO_TECHNICIAN are real, reachable
    steps -- just not through derive_next_action() (the committed-job
    half). See derive_pending_message_next_action() below for where
    they actually live, and the module docstring for why the lifecycle
    is split this way."""
    unreachable_here = {"ACK_COMPANY_K", "SEND_JOB_TO_TECHNICIAN"}
    scenarios = [
        ctx(status=s, technician_name=name, latest_technician_update=upd, closing=cl)
        for s in ("New", "Needs Review", "Approved", "Handle Manually", "In Progress", "Closed", "Cancelled", "Rejected")
        for name in (None, "Demo Ashraf")
        for upd in (None, {"update_type": "Going"}, {"update_type": "Completed"}, {"update_type": "Custom"})
        for cl in (None, {"missing": [], "supported": True, "sent_confirmed": True})
    ]
    for c in scenarios:
        assert derive_next_action(c).next_action not in unreachable_here


# ---------------------------------------- pending-message projection ------
def pctx(**overrides):
    base = {"needs_review": False, "k_required": False, "k_sent": False, "eligible_count": 1, "dispatched": False}
    base.update(overrides)
    return base


def test_pending_needs_review_is_review_new_job():
    r = derive_pending_message_next_action(pctx(needs_review=True))
    assert r.next_action == "REVIEW_NEW_JOB"


def test_pending_k_required_and_not_sent_is_ack_company_k():
    r = derive_pending_message_next_action(pctx(k_required=True, k_sent=False))
    assert r.next_action == "ACK_COMPANY_K"


def test_ack_company_k_is_genuinely_reachable():
    """The gap flagged in the prior report -- proven fixed, not just
    asserted."""
    r = derive_pending_message_next_action(pctx(k_required=True, k_sent=False))
    assert r.next_action == "ACK_COMPANY_K"
    assert r.human_review_needed is False
    assert r.terminal is False


def test_pending_k_required_but_already_sent_moves_past_ack():
    r = derive_pending_message_next_action(pctx(k_required=True, k_sent=True))
    assert r.next_action != "ACK_COMPANY_K"


def test_pending_no_eligible_technician_is_human_review():
    r = derive_pending_message_next_action(pctx(eligible_count=0))
    assert r.next_action == "HUMAN_REVIEW_NEEDED"
    assert r.human_review_needed is True


def test_pending_reviewed_k_resolved_eligible_not_dispatched_is_send_job_to_technician():
    r = derive_pending_message_next_action(pctx())
    assert r.next_action == "SEND_JOB_TO_TECHNICIAN"


def test_send_job_to_technician_is_genuinely_reachable():
    """The other half of the gap flagged in the prior report -- proven
    fixed, not just asserted."""
    r = derive_pending_message_next_action(pctx(needs_review=False, k_required=False, eligible_count=2, dispatched=False))
    assert r.next_action == "SEND_JOB_TO_TECHNICIAN"


def test_pending_already_dispatched_is_terminal_for_this_projection():
    r = derive_pending_message_next_action(pctx(dispatched=True))
    assert r.terminal is True


def test_pending_review_new_job_outranks_ack_company_k():
    r = derive_pending_message_next_action(pctx(needs_review=True, k_required=True, k_sent=False))
    assert r.next_action == "REVIEW_NEW_JOB"


def test_pending_ack_company_k_outranks_no_eligible_technician():
    r = derive_pending_message_next_action(pctx(k_required=True, k_sent=False, eligible_count=0))
    assert r.next_action == "ACK_COMPANY_K"


def test_pending_projection_next_actions_stay_in_the_fixed_vocabulary():
    scenarios = [
        pctx(needs_review=True), pctx(k_required=True, k_sent=False), pctx(eligible_count=0),
        pctx(), pctx(dispatched=True),
    ]
    for c in scenarios:
        assert derive_pending_message_next_action(c).next_action in NEXT_ACTIONS


# --------------------------------------------------------- lifecycle steps
def test_lifecycle_steps_always_returns_exactly_eleven_entries():
    steps = derive_lifecycle_steps({"entity_type": "JOB", "status": "Approved"})
    assert len(steps) == 11
    assert [s["label"] for s in steps] == list(LIFECYCLE_STEPS)


def test_lifecycle_brand_new_message_only_received_is_completed():
    steps = derive_lifecycle_steps({"entity_type": "INCOMING_MESSAGE", "needs_review": True})
    assert steps[0]["status"] == STEP_COMPLETED  # Received
    assert steps[1]["status"] == STEP_CURRENT    # Reviewed -- needs review


def test_lifecycle_reviewed_message_k_owed_is_current_at_ack_step():
    steps = derive_lifecycle_steps({
        "entity_type": "INCOMING_MESSAGE", "needs_review": False, "k_required": True, "k_sent": False,
    })
    assert steps[1]["status"] == STEP_COMPLETED  # Reviewed
    assert steps[2]["status"] == STEP_CURRENT    # Company acknowledged


def test_lifecycle_committed_job_company_ack_step_always_completed():
    """Committed jobs never show "current" at step 3 -- see the module
    docstring on why K is always already resolved by commit time."""
    steps = derive_lifecycle_steps({"entity_type": "JOB", "status": "Approved", "technician_name": None})
    assert steps[2]["status"] == STEP_COMPLETED  # Company acknowledged
    assert steps[3]["status"] == STEP_CURRENT    # Technician assigned -- none yet


def test_lifecycle_committed_job_dispatch_step_always_completed():
    steps = derive_lifecycle_steps({"entity_type": "JOB", "status": "Approved", "technician_name": "Demo Ashraf"})
    assert steps[4]["status"] == STEP_COMPLETED  # Sent to technician


def test_lifecycle_waiting_on_k_is_current_at_accepted_step():
    steps = derive_lifecycle_steps({
        "entity_type": "JOB", "status": "Approved", "technician_name": "Demo Ashraf",
        "technician_k": {"status": "waiting", "candidates": []},
    })
    assert steps[5]["status"] == STEP_CURRENT  # Technician accepted


def test_lifecycle_declined_k_is_blocked_at_accepted_step():
    steps = derive_lifecycle_steps({
        "entity_type": "JOB", "status": "Approved", "technician_name": "Demo Ashraf",
        "technician_k": {"status": "declined"}, "human_review_needed": True,
    })
    assert steps[5]["status"] == STEP_BLOCKED  # Technician accepted


def test_lifecycle_accepted_moves_current_to_work_update():
    steps = derive_lifecycle_steps({
        "entity_type": "JOB", "status": "Approved", "technician_name": "Demo Ashraf",
        "technician_k": {"status": "accepted"},
    })
    assert steps[5]["status"] == STEP_COMPLETED  # Technician accepted
    assert steps[6]["status"] == STEP_CURRENT    # Work/update


def test_lifecycle_technician_update_recorded_moves_current_to_company_update():
    steps = derive_lifecycle_steps({
        "entity_type": "JOB", "status": "In Progress", "technician_name": "Demo Ashraf",
        "technician_k": {"status": "accepted"}, "latest_technician_update": {"update_type": "Going"},
        "company_update_owed": {"suggested_reply": "Going"},
    })
    assert steps[6]["status"] == STEP_COMPLETED  # Work/update
    assert steps[7]["status"] == STEP_CURRENT    # Company updated


def test_lifecycle_closed_job_closing_received_is_completed():
    steps = derive_lifecycle_steps({
        "entity_type": "JOB", "status": "Closed", "technician_name": "Demo Ashraf",
        "technician_k": {"status": "accepted"}, "latest_technician_update": {"update_type": "Completed"},
        "closing": {"missing": [], "supported": True, "sent_confirmed": False},
    })
    assert steps[8]["status"] == STEP_COMPLETED  # Closing received
    assert steps[9]["status"] == STEP_CURRENT    # Closing sent


def test_lifecycle_settled_job_every_step_completed():
    steps = derive_lifecycle_steps({
        "entity_type": "JOB", "status": "Closed", "reconciliation_state": "Settled", "technician_name": "Demo Ashraf",
        "technician_k": {"status": "accepted"}, "latest_technician_update": {"update_type": "Completed"},
        "closing": {"missing": [], "supported": True, "sent_confirmed": True},
    })
    assert all(s["status"] == STEP_COMPLETED for s in steps)


def test_lifecycle_at_most_one_step_is_current_or_blocked():
    scenarios_ctx = [
        {"entity_type": "INCOMING_MESSAGE", "needs_review": True},
        {"entity_type": "INCOMING_MESSAGE", "k_required": True, "k_sent": False},
        {"entity_type": "JOB", "status": "Approved", "technician_name": None},
        {"entity_type": "JOB", "status": "Approved", "technician_name": "Demo Ashraf", "technician_k": {"status": "declined"}, "human_review_needed": True},
        {"entity_type": "JOB", "status": "Cancelled"},
        {"entity_type": "JOB", "status": "Closed", "reconciliation_state": "Settled"},
    ]
    for c in scenarios_ctx:
        steps = derive_lifecycle_steps(c)
        active = [s for s in steps if s["status"] in (STEP_CURRENT, STEP_BLOCKED)]
        assert len(active) <= 1, f"more than one active step for {c}: {steps}"


def test_lifecycle_cancelled_job_has_no_current_step():
    steps = derive_lifecycle_steps({"entity_type": "JOB", "status": "Cancelled", "technician_name": None})
    assert not any(s["status"] == STEP_CURRENT for s in steps)
    assert steps[10]["status"] == STEP_COMPLETED  # Complete
    assert steps[3]["status"] == STEP_PENDING     # Technician assigned -- never happened, but not "current" either
