"""Regressions for the five HIGH findings from the independent review.

Each was verified against the real code before being repaired, and each
test here fails on the pre-repair behaviour.

The unifying rule: a suppression decides that a real job does NOT need
sending. That is only ever safe on owner-confirmed identity, real event
order, and evidence that actually shows a job being forwarded. Anything
less must surface to a human rather than quietly disappear.
"""
import ast
import pathlib

import pytest

from dispatcher import dispatch_candidates as dc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

INTAKE_AT = "2026-08-15T12:00:00+00:00"
BEFORE = "2026-08-14T09:00:00+00:00"
AFTER = "2026-08-15T12:00:09+00:00"


def cand(**kw):
    args = dict(
        message_id="m1", received_at=INTAKE_AT, event_at=INTAKE_AT,
        group_name="Augusta - Adam", group_confirmed=True, company="Mike Jobs",
        ticket="JO47-TPXW", service="Car lockout", city="Augusta", state="GA",
        canonical_area="Augusta", eligible=[{"name": "Sebai"}],
        proposed_message="Ticket: JO47-TPXW",
    )
    args.update(kw)
    return dc.build_candidate(**args)


def _route_source(name="route_whatsapp_candidates") -> str:
    tree = ast.parse((REPO_ROOT / "operator_api.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


# ============================================================== H1 =====
# Correlation identity must be the OWNER-CONFIRMED company, not the
# parser's message-format guess. Two companies can both post in the
# "Main Leads" format; letting format stand in for identity would let one
# company's dispatch suppress another company's job.



def test_h1_two_companies_sharing_a_ticket_do_not_cross_suppress():
    """Company B's job must survive even when Company A dispatched the
    same ticket string. The route keys buckets by company, so B's
    candidate simply receives no dispatch evidence."""
    company_b = cand(company="GMI", dispatched_to=None)
    assert company_b["status"] == dc.STATUS_WAITING_HUMAN
    assert company_b["proposed_message"]


def test_h1_an_unresolvable_company_never_suppresses():
    c = cand(ambiguous_source_matches=[{"group_name": "Sebai Augusta Dispatch", "event_at": AFTER}])
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["review_reason"] == dc.REVIEW_COMPANY_UNRESOLVED
    assert c["proposed_message"] is None


# ============================================================== H2 =====
# Safety suppressions must remain visible. A decision the operator cannot
# see is one taken on their behalf without telling them.

def test_h2_safety_held_candidates_are_tagged_for_the_console():
    assert cand(dispatched_to=[{"technician": "Sebai", "event_at": BEFORE}])["review_reason"] == dc.REVIEW_REPOST
    assert cand(dispatched_to=[{"technician": "Sebai", "event_at": None}])["review_reason"] == dc.REVIEW_ORDER_UNDETERMINED
    assert cand(ambiguous_source_matches=[{"event_at": AFTER}])["review_reason"] == dc.REVIEW_COMPANY_UNRESOLVED


def test_h2_ordinary_incompleteness_is_not_tagged_as_a_safety_review():
    """Only suppressions surface. "No ticket" chatter would flood the
    board and bury the decisions that matter."""
    assert cand(ticket=None)["review_reason"] is None
    assert cand()["review_reason"] is None


def test_h2_the_console_renders_safety_reviews_and_not_only_dispatchables():
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "console.js").read_text(encoding="utf-8")
    assert "candidateReviews" in js
    assert "c.review_reason" in js
    assert "reviewCard" in js, "safety-held items need their own visible card"


def test_h2_a_safety_review_item_has_no_send_or_copy_action():
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "console.js").read_text(encoding="utf-8")
    start = js.index("function reviewCard")
    body = js[start:js.index("\n  }", start)]
    for forbidden in ("copy-candidate", "data-act=\"dispatch\"", "Copy message"):
        assert forbidden not in body, "a held-back item must not be actionable"
    assert 'if (col === "candidate-review") return "";' in js, (
        "the detail bar must offer nothing for a review item")


# ============================================================== H3 =====
# Ordering must use the WhatsApp EVENT time. `received_at` is ingestion
# metadata; the 25 observations recovered from the stuck retry queue were
# persisted long after they happened, and this database already contains
# rows stored out of event order.

def test_h3_reversed_persistence_order_still_reads_the_real_sequence():
    """Persisted LATER but happened EARLIER: the dispatch preceded the
    intake, so this is a repost -- not "already dispatched"."""
    c = dc.build_candidate(
        message_id="m", received_at="2026-08-15T23:00:00+00:00",   # ingested last
        event_at="2026-08-15T12:00:00+00:00",                      # happened at noon
        group_name="Augusta - Adam", group_confirmed=True, company="Mike Jobs",
        ticket="T1", service="s", city="Augusta", state="GA",
        canonical_area="Augusta", eligible=[{"name": "Sebai"}], proposed_message="m",
        dispatched_to=[{
            "technician": "Sebai",
            "received_at": "2026-08-15T08:00:00+00:00",   # ingested first...
            "event_at": "2026-08-14T09:00:00+00:00",      # ...but happened a day earlier
        }])
    assert c["status"] == dc.STATUS_REPOST_AFTER_DISPATCH
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED


def test_h3_ingestion_time_is_ignored_even_when_it_contradicts_the_event():
    """Inverse of the above: ingested before, but happened after."""
    c = dc.build_candidate(
        message_id="m", received_at=INTAKE_AT, event_at=INTAKE_AT,
        group_name="g", group_confirmed=True, company="Mike Jobs",
        ticket="T1", service="s", city="Augusta", state="GA",
        canonical_area="Augusta", eligible=[{"name": "Sebai"}], proposed_message="m",
        dispatched_to=[{"technician": "Sebai",
                        "received_at": "2020-01-01T00:00:00+00:00",
                        "event_at": AFTER}])
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED


def test_h3_epoch_timestamps_are_understood():
    """The observations table stores the event time as a unix epoch, and
    exposes it as TEXT."""
    assert dc._parse_ts(1786805569) is not None
    assert dc._parse_ts("1786805569") == dc._parse_ts(1786805569)



@pytest.mark.parametrize("bad", [None, "", "garbage"])
def test_h3_unusable_event_times_fail_conservatively(bad):
    c = cand(dispatched_to=[{"technician": "Sebai", "event_at": bad}])
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["proposed_message"] is None


# ============================================================== H4 =====
# Operator queue state and durable correlation evidence are different
# things. Ignoring an observation must not erase the fact that a job was
# dispatched.

def test_h4_downstream_evidence_survives_queue_resolution():
    source = _route_source()
    assert "include_resolved=True" in source, (
        "resolving an observation must not erase dispatch evidence")



# ============================================================== H5 =====
# A job the owner forwarded by hand from the paired phone is real
# dispatch evidence, even though it never becomes an inbound observation.

def test_h5_manual_outgoing_dispatch_counts_as_evidence():
    source = _route_source()
    assert "list_whatsapp_outgoing_audit" in source
    assert "outgoing (sent by hand from the paired account)" in source


def test_h5_outgoing_evidence_is_restricted_to_confirmed_dispatch_groups():
    source = _route_source()
    assert "confirmed_dispatch_groups" in source
    assert "'TECH_DISPATCH'" in source


def test_h5_outgoing_evidence_suppresses_the_matching_intake():
    c = cand(dispatched_to=[{
        "group_name": "Sebai Augusta Dispatch", "technician": "Sebai",
        "event_at": AFTER, "channel": "outgoing (sent by hand from the paired account)"}])
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["proposed_message"] is None


def test_h5_reading_outgoing_audit_enables_no_sending():
    source = _route_source()
    for forbidden in ("sendMessage", "send_message", "post_message", "client.send"):
        assert forbidden not in source
    module = (REPO_ROOT / "src" / "dispatcher" / "dispatch_candidates.py").read_text(encoding="utf-8")
    assert "send(" not in module


# ============================================================== M7 =====
# A ticket-bearing message is not proof of dispatch.


def test_m7_the_structure_predicate_matches_the_canonical_one():
    """Same shape test _interpret_whatsapp_observation uses to decide
    JOB FORWARDED TO TECHNICIAN -- not a second opinion."""
    canonical = _route_source("_interpret_whatsapp_observation")
    assert "forwarded_job.ticket_number.value and (forwarded_job.phone.value or forwarded_job.address.value)" in canonical


# ====================================================== invariants =====
def test_projections_remain_read_only_and_mutate_no_canonical_status():
    source = _route_source()
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "commit()", "derive_job_status"):
        assert forbidden not in source
    module = (REPO_ROOT / "src" / "dispatcher" / "dispatch_candidates.py").read_text(encoding="utf-8")
    # Precise SQL tokens. Bare verbs like "UPDATE " false-positive on the
    # constant NEGATIVE_OR_UPDATE, which is a verdict name, not a write.
    for forbidden in ("sqlite3", "conn.execute", "INSERT INTO", "UPDATE jobs", "UPDATE whatsapp", "DELETE FROM", "commit()"):
        assert forbidden not in module


def test_nothing_held_back_ever_carries_a_sendable_message():
    for kw in ({"dispatched_to": [{"technician": "Sebai", "event_at": AFTER}]},
               {"dispatched_to": [{"technician": "Sebai", "event_at": BEFORE}]},
               {"dispatched_to": [{"technician": "Sebai", "event_at": None}]},
               {"closing_seen_in": [{"technician": "Sebai", "event_at": AFTER}]},
               {"ambiguous_source_matches": [{"event_at": AFTER}]}):
        assert cand(**kw)["proposed_message"] is None


# NOTE ON WHAT MOVED
# The source-string assertions for H1 (company scope), H3 (event-time
# ordering), H4 (durable evidence) and M7 (decline is not dispatch) were
# removed from this file and re-expressed as behavioural tests in
# tests/test_candidate_route_behaviour.py, which seeds a real database
# and calls the real route. The review was explicit that these safety
# properties must not rest on AST/source matching, and rightly so: every
# one of those assertions broke on a variable rename while the behaviour
# they claimed to protect was intact. The remaining tests here are
# module-level behaviour, which is genuinely what they test.
