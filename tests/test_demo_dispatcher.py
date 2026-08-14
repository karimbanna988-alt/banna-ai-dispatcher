"""Tests for the owner-facing demo reasoning engine.

Two things matter here: the demo must show correct reasoning, and it
must be structurally incapable of touching production state.
"""
import pathlib

import pytest

from dispatcher import demo_dispatcher as dd
from dispatcher.parser import load_vehicle_rules


@pytest.fixture(scope="module")
def vr():
    return load_vehicle_rules()


def demo_job_message(ticket="DEMO-001", service="House lockout"):
    return (
        "Power By Ucallz App\n\nDemo Company\n"
        f"Ticket: {ticket}\nName: Demo Customer\nPhone: (555) 010-0000\n"
        f"Address: 1 Demo Street, Demo City\nDesc: {service}\n"
    )


# ========================================================== ISOLATION ====

def test_module_cannot_touch_persistent_state():
    """The whole safety case for the demo: it is pure. No connection, no
    SQL, no file access, no send. It therefore cannot write a real job
    or mutate a technician however it is driven."""
    source = pathlib.Path(dd.__file__).read_text(encoding="utf-8")
    for forbidden in ("sqlite3", "execute(", "INSERT", "UPDATE ", "DELETE",
                      "commit(", "open(", "requests", "sendMessage", "save_audit_entry"):
        assert forbidden not in source, f"demo module must stay pure -- found {forbidden!r}"


def test_demo_never_calls_the_production_status_machine():
    """A demo must not be able to manufacture a real job status change.

    Checks for a CALL, not the bare name: the module's docstring names
    derive_job_status precisely to record that it deliberately does not
    use it, and that explanation is worth keeping."""
    source = pathlib.Path(dd.__file__).read_text(encoding="utf-8")
    assert "derive_job_status(" not in source
    assert "storage." not in source.replace("(storage.derive_job_status)", "")


def test_demo_identities_are_fabricated_not_looked_up():
    """Demo must not reference a real technician or imply a real customer."""
    source = pathlib.Path(dd.__file__).read_text(encoding="utf-8")
    assert "get_technician" not in source
    assert "list_technicians" not in source
    assert dd.DEMO_TECHNICIAN == "Demo Technician"


# ===================================================== HAPPY LIFECYCLE ====

def test_full_lifecycle_new_to_completed(vr):
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()),
        (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, "on my way, 20 minutes"),
        (dd.ROLE_TECHNICIAN, "done, 150 cash"),
    ], vr)
    assert [d.state_after for d in decisions] == [
        dd.STATE_NEW, dd.STATE_ASSIGNED, dd.STATE_EN_ROUTE, dd.STATE_COMPLETED,
    ]
    assert job.closing_amount == "150"
    assert job.closing_payment_method == "cash"


def test_intake_explains_its_routing_decision(vr):
    decisions, _ = dd.run_sequence([(dd.ROLE_COMPANY, demo_job_message())], vr)
    d = decisions[0]
    assert d.decision == f"Dispatch to {dd.DEMO_TECHNICIAN}"
    assert d.confidence == dd.CONFIDENCE_EXACT
    assert d.proposed_action == "SEND_TO_TECHNICIAN"
    assert any("Routing rule matched exactly" in w for w in d.why)
    assert d.ticket == "DEMO-001"


def test_proposed_technician_message_contains_only_extracted_fields(vr):
    decisions, _ = dd.run_sequence([(dd.ROLE_COMPANY, demo_job_message())], vr)
    msg = decisions[0].proposed_message
    assert "DEMO-001" in msg
    assert "1 Demo Street" in msg
    assert "not stated" not in msg  # everything was present in this fixture


def test_missing_fields_are_shown_as_not_stated_never_invented(vr):
    """A job posting missing an address must say so, not fabricate one."""
    text = "Power By Ucallz App\n\nDemo Company\nTicket: DEMO-002\nPhone: (555) 010-0000\nDesc: Car key\n"
    decisions, _ = dd.run_sequence([(dd.ROLE_COMPANY, text)], vr)
    d = decisions[0]
    assert "address" in d.missing_fields
    assert "not stated" in d.proposed_message
    assert d.confidence == dd.CONFIDENCE_STRONG  # routed, but flagged
    assert d.needs_human is True


# ======================================================== TECH REPLIES ====

def test_acceptance_is_recognized_and_explained(vr):
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()), (dd.ROLE_TECHNICIAN, "K"),
    ], vr)
    d = decisions[1]
    assert d.detected == "Technician accepted the job"
    assert d.confidence == dd.CONFIDENCE_EXACT
    assert d.proposed_action == "UPDATE_COMPANY"
    assert job.state == dd.STATE_ASSIGNED


def test_decline_routes_to_a_human_and_never_auto_reassigns(vr):
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()), (dd.ROLE_TECHNICIAN, "can't"),
    ], vr)
    d = decisions[1]
    assert d.proposed_action == "HUMAN_REVIEW_NEEDED"
    assert d.needs_human is True
    assert job.state == dd.STATE_NEEDS_REVIEW


@pytest.mark.parametrize("text", ["on my way", "omw", "20 minutes out", "1:30 hours"])
def test_travel_and_eta_move_the_job_en_route(vr, text):
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()), (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, text),
    ], vr)
    assert job.state == dd.STATE_EN_ROUTE
    assert decisions[2].proposed_action == "UPDATE_COMPANY"


# ============================================================ CLOSINGS ====

def test_closing_with_an_explicit_amount_completes_the_job(vr):
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()), (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, "finished 220 card"),
    ], vr)
    d = decisions[2]
    assert job.state == dd.STATE_COMPLETED
    assert job.closing_amount == "220"
    assert job.closing_payment_method == "card"
    assert "220" in d.proposed_message


def test_done_without_an_amount_never_invents_one(vr):
    """The single most important demo safety property."""
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()), (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, "all done"),
    ], vr)
    d = decisions[2]
    assert job.closing_amount is None
    assert job.closing_payment_method is None
    assert d.proposed_action == "HUMAN_REVIEW_NEEDED"
    assert d.needs_human is True
    assert job.state == dd.STATE_NEEDS_REVIEW
    assert set(d.missing_fields) == {"amount", "payment_method"}


def test_a_figure_mentioned_in_passing_is_not_a_closing_amount(vr):
    """Same adjacency rule production uses -- "$150" alone is not a
    closing."""
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()), (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, "customer is arguing about the $150"),
    ], vr)
    assert job.closing_amount is None
    assert job.state != dd.STATE_COMPLETED


# ======================================================= AMBIGUITY ========

def test_job_posting_with_no_ticket_cannot_be_routed(vr):
    decisions, job = dd.run_sequence([(dd.ROLE_COMPANY, "someone needs a locksmith")], vr)
    d = decisions[0]
    assert d.proposed_action == "HUMAN_REVIEW_NEEDED"
    assert d.needs_human is True
    assert job.ticket is None
    assert d.proposed_message is None  # nothing proposed for an unroutable message


def test_technician_reply_with_no_open_job_is_not_correlated(vr):
    decisions, _ = dd.run_sequence([(dd.ROLE_TECHNICIAN, "K")], vr)
    d = decisions[0]
    assert d.proposed_action == "HUMAN_REVIEW_NEEDED"
    assert d.needs_human is True


def test_unrecognized_chat_is_ignored(vr):
    decisions, _ = dd.run_sequence([("SOME_OTHER_CHAT", demo_job_message())], vr)
    assert decisions[0].decision == "Ignore"
    assert decisions[0].needs_human is True


def test_the_exact_owner_demo_sequence(vr):
    """The precise sequence the owner will type tonight, pinned so it
    cannot silently break: job -> K -> "20 min" -> "Done 180 cash"."""
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()),
        (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, "20 min"),
        (dd.ROLE_TECHNICIAN, "Done 180 cash"),
    ], vr)
    assert decisions[0].proposed_action == "SEND_TO_TECHNICIAN"
    assert decisions[1].detected == "Technician accepted the job"
    assert decisions[2].state_after == dd.STATE_EN_ROUTE
    assert "20 min" in decisions[2].detected
    assert job.state == dd.STATE_COMPLETED
    assert job.closing_amount == "180"
    assert job.closing_payment_method == "cash"


def test_the_exact_owner_negative_case_bare_done(vr):
    """The owner will type a bare "Done" to prove the system refuses to
    invent a price. This must stay true."""
    decisions, job = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()),
        (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, "Done"),
    ], vr)
    d = decisions[2]
    assert job.closing_amount is None
    assert job.closing_payment_method is None
    assert d.proposed_action == "HUMAN_REVIEW_NEEDED"
    assert set(d.missing_fields) == {"amount", "payment_method"}
    assert job.state != dd.STATE_COMPLETED


def test_every_decision_carries_a_reason(vr):
    """The demo's whole point is visible reasoning -- no step may be
    silent."""
    decisions, _ = dd.run_sequence([
        (dd.ROLE_COMPANY, demo_job_message()), (dd.ROLE_TECHNICIAN, "K"),
        (dd.ROLE_TECHNICIAN, "on my way"), (dd.ROLE_TECHNICIAN, "done 100 cash"),
    ], vr)
    for d in decisions:
        assert d.detected and d.decision and d.why, f"decision without reasoning: {d}"
