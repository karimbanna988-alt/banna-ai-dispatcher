"""Correlation safety for WhatsApp dispatch candidates.

Two latent defects were found by audit in the first implementation:

  1. Downstream correlation was ORDER-BLIND. A dispatch observation from
     before an intake suppressed that later intake, so a genuine repost
     (no answer, failed coverage, redispatch request) vanished.

  2. Correlation used a BARE ticket string. The canonical path
     deliberately scopes by (ticket_number, source) -- see
     storage.classify_incoming, whose own comment warns that "a bare
     ticket_number lookup could match the wrong company's job if two
     happened to share the same ticket string".

Neither was reachable in the data at the time, which is exactly why they
needed pinning: a latent correlation bug becomes an operational one the
first day traffic changes shape.

The safety direction throughout: when sequence or identity cannot be
established, say HUMAN_REVIEW_NEEDED and offer nothing to send.
"""
import ast
import inspect
import pathlib

import pytest

from dispatcher import dispatch_candidates as dc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

INTAKE_AT = "2026-08-15T12:00:00+00:00"
EARLIER = "2026-08-14T09:00:00+00:00"
LATER = "2026-08-15T12:00:09+00:00"


def cand(**kw):
    args = dict(
        # Ordering is decided on the WhatsApp EVENT time; received_at is
        # ingestion metadata and must not affect sequencing.
        message_id="m1", received_at=INTAKE_AT, event_at=INTAKE_AT,
        group_name="Augusta - Adam", group_confirmed=True, company="Mike Jobs",
        ticket="JO47-TPXW", service="Car lockout", city="Augusta", state="GA",
        canonical_area="Augusta",
        eligible=[{"name": "Sebai", "dispatch_group": "Sebai Augusta Dispatch"}],
        missing_fields=[], proposed_message="Ticket: JO47-TPXW", correlated_job_id=None,
    )
    args.update(kw)
    return dc.build_candidate(**args)


def dispatch(at, technician="Sebai", group="Sebai Augusta Dispatch"):
    # event_at is what sequencing uses; received_at rides along as the
    # ingestion timestamp it actually is.
    return [{"group_name": group, "technician": technician,
             "event_at": at, "received_at": at}]


# ------------------------------------------------------ CASE A / B ----
def test_case_b_no_dispatch_evidence_waits_for_human_dispatch():
    c = cand()
    assert c["status"] == dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"]


def test_case_a_dispatch_after_intake_is_already_dispatched():
    c = cand(dispatched_to=dispatch(LATER))
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["proposed_message"] is None
    assert c["routing_match"] == dc.ROUTING_MATCH
    assert c["actual_destination"] == "Sebai"


# ---------------------------------------------------------- CASE C ----
def test_case_c_prior_dispatch_then_newer_intake_is_a_repost_for_review():
    """THE ORDER-BLINDNESS DEFECT. A dispatch from yesterday must not
    silently swallow today's repost -- a repost may mean no answer,
    failed coverage, a reminder, or a genuine redispatch request."""
    c = cand(dispatched_to=dispatch(EARLIER))
    assert c["status"] == dc.STATUS_REPOST_AFTER_DISPATCH
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED
    assert c["needs_human_review"] is True


def test_a_repost_offers_nothing_to_send_until_reviewed():
    c = cand(dispatched_to=dispatch(EARLIER))
    assert c["proposed_message"] is None


def test_a_repost_does_not_assume_the_prior_dispatch_satisfied_it():
    c = cand(dispatched_to=dispatch(EARLIER))
    joined = " ".join(c["why"]).lower()
    assert "repost" in joined or "again" in joined
    assert c["status"] != dc.STATUS_WAITING_HUMAN, "not an ordinary new job either"


def test_a_later_dispatch_wins_over_an_earlier_one():
    """Dispatched before AND after this intake: the after occurrence is
    what matters -- the job is genuinely on its way."""
    c = cand(dispatched_to=dispatch(EARLIER) + dispatch(LATER))
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED


# ------------------------------------------------- TIMESTAMP RULES ----
@pytest.mark.parametrize("bad", [None, "", "not-a-timestamp", "2026-13-45T99:99:99"])
def test_unusable_downstream_timestamps_fail_conservatively(bad):
    c = cand(dispatched_to=dispatch(bad))
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["proposed_message"] is None


@pytest.mark.parametrize("bad", [None, "", "not-a-timestamp"])
def test_an_unusable_intake_timestamp_fails_conservatively(bad):
    c = cand(received_at=bad, event_at=bad, dispatched_to=dispatch(LATER))
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["proposed_message"] is None


def test_equal_timestamps_are_ambiguous_and_fail_conservatively():
    """Same second, no way to establish sequence. Guessing here would be
    guessing whether a job has already been sent."""
    c = cand(dispatched_to=dispatch(INTAKE_AT))
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["proposed_message"] is None


def test_ordering_is_parsed_not_lexical():
    """Offsets make lexical comparison wrong: 12:00:09+00:00 is LATER
    than 13:00:00+01:00, but sorts earlier as a string."""
    c = cand(received_at="2026-08-15T13:00:00+01:00",
             event_at="2026-08-15T13:00:00+01:00",  # == 12:00:00Z
             dispatched_to=dispatch("2026-08-15T12:00:09+00:00"))
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED, (
        "a parsed comparison must see the dispatch as later")


def test_naive_and_aware_timestamps_can_still_be_compared():
    c = cand(received_at="2026-08-15T12:00:00", event_at="2026-08-15T12:00:00",
             dispatched_to=dispatch(LATER))
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED


# --------------------------------------------------- CASE D: SCOPE ----
def test_case_d_a_different_company_never_suppresses_the_same_ticket():
    """Bare-ticket correlation could mark Company B's job as already
    dispatched because Company A happened to use the same string."""
    source = _route_source("route_whatsapp_candidates")
    assert "(source" in source or "source_key" in source or "scope" in source, (
        "downstream buckets must be keyed by source as well as ticket")


def test_an_unresolvable_downstream_source_is_reviewed_not_matched():
    """If the company behind a downstream sighting cannot be resolved, we
    cannot claim it is the same job -- but we also must not offer the
    intake as an ordinary new job."""
    c = cand(ambiguous_source_matches=[{"group_name": "Sebai Augusta Dispatch",
                                        "event_at": LATER, "received_at": LATER}])
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["proposed_message"] is None


def test_a_scoped_match_still_requires_exact_ticket_equality():
    source = _route_source("route_whatsapp_candidates")
    assert "ticket_number.value" in source
    for forbidden in ("timedelta", "within_minutes", "closest", "startswith(ticket"):
        assert forbidden not in source


# --------------------------------------------------- EXCLUSIONS -------
def test_closing_after_intake_is_closing_observed_not_dispatchable():
    c = cand(closing_seen_in=[{"group_name": "Sebai augusta closing",
                               "technician": "Sebai", "event_at": LATER,
                               "received_at": LATER}])
    assert c["status"] == dc.STATUS_CLOSING_OBSERVED
    assert c["proposed_message"] is None


def test_only_confirmed_technician_dispatch_groups_are_evidence():
    source = _route_source("route_whatsapp_candidates")
    marker = source.index("dispatched")
    window = source[marker:marker + 1200]
    assert "group_mapping_confirmed" in window
    assert "TECH_DISPATCH" in window


def test_ignore_groups_are_never_dispatch_evidence():
    source = _route_source("route_whatsapp_candidates")
    evidence = source[source.index("dispatched"):]
    assert "'IGNORE'" not in evidence and '"IGNORE"' not in evidence


# ------------------------------------------------------ INVARIANTS ----
def test_nothing_held_back_from_the_board_carries_a_sendable_message():
    for kw in ({"dispatched_to": dispatch(LATER)},
               {"dispatched_to": dispatch(EARLIER)},
               {"dispatched_to": dispatch(None)},
               {"closing_seen_in": [{"technician": "Sebai", "event_at": LATER}]},
               {"ambiguous_source_matches": [{"event_at": LATER}]}):
        assert cand(**kw)["proposed_message"] is None


def test_only_waiting_for_human_dispatch_reaches_the_board():
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "console.js").read_text(encoding="utf-8")
    assert 'c.status === "WAITING FOR HUMAN DISPATCH"' in js


def test_the_projection_remains_read_only():
    source = inspect.getsource(dc)
    # Precise SQL tokens. Bare verbs like "UPDATE " false-positive on the
    # constant NEGATIVE_OR_UPDATE, which is a verdict name, not a write.
    for forbidden in ("sqlite3", "conn.execute", "INSERT INTO", "UPDATE jobs", "UPDATE whatsapp", "DELETE FROM", "commit()"):
        assert forbidden not in source
    route = _route_source("route_whatsapp_candidates")
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "commit()", "derive_job_status"):
        assert forbidden not in route


def test_no_transport_capability_anywhere_in_the_candidate_path():
    tree = ast.parse(inspect.getsource(dc))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(tree).lower()
    # Precise tokens: a bare "quo" matches message_patterns.mentions_quote
    # (a technician PRICE QUOTE), which is unrelated to the Quo CRM.
    for forbidden in ("send(", "sendmessage", "quo_pending", "quo_api",
                      "requests.", "urlopen", "http://", "https://"):
        assert forbidden not in code


def _route_source(name: str) -> str:
    tree = ast.parse((REPO_ROOT / "operator_api.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")
