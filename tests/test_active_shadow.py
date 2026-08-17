"""ACTIVE SHADOW.

Banna runs the same deterministic pipeline the dispatcher would and shows
the decision it WOULD take on real traffic. The name has to be honest in
both halves:

  ACTIVE  the analysis genuinely runs on every captured job
  SHADOW  nothing leaves the building

So the tests here are mostly about what the feature CANNOT do. The one
thing that would make this dangerous is a shadow decision acquiring a
send path, and the one thing that would make it dishonest is claiming a
model is thinking when the software is doing the work.
"""
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "operator_ui_v2_connected"
INDEX = UI / "index.html"
CSS = UI / "css" / "console.css"
JS = UI / "js" / "console.js"


@pytest.fixture(scope="module")
def js():
    return JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html():
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(name)


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


# ==================================================== cannot act =======
def test_shadow_cannot_send_anything(js):
    """The decisive property. No send exists in this file at all, so a
    shadow decision has nothing to escalate to."""
    for forbidden in ("sendMessage", "client.send", "whatsapp.send",
                      "sendText", "postMessage("):
        assert forbidden not in js, f"a send path appeared: {forbidden}"


def test_shadow_rendering_makes_no_write_call(js):
    for name in ("renderShadow", "shadowSection", "shadowDecision", "comparison", "humanAction"):
        body = _strip_comments(_body(js, name))
        # Precise tokens only: a bare "POST" also matches the legitimate
        # backend state REPOST AFTER PRIOR DISPATCH, which is a status
        # being read, not a request being made.
        for forbidden in ("Api.acknowledge", "Api.prepareDispatch", "Api.confirmDispatch",
                          "Api.post", "fetch(", 'method: "POST"', '"POST"'):
            assert forbidden not in body, f"{name} performs {forbidden}"


def test_shadow_never_mutates_job_state(js):
    body = _strip_comments(_body(js, "renderShadow"))
    for forbidden in ("status =", "jobs.status", "promote", "confirmDispatch"):
        assert forbidden not in body


def test_the_proposed_message_is_display_only(js):
    """It is rendered into a text block, never handed to a transport."""
    body = _body(js, "renderShadow")
    assert 'class="sh-msg"' in body
    assert "proposed_message" in body
    # The only way text leaves this app is the clipboard, and that is a
    # deliberate human action on the Jobs screen, not part of shadow.
    assert "navigator.clipboard" not in body


def test_simulation_is_stated_on_every_shadow_card(js):
    body = _body(js, "renderShadow")
    assert body.count("Simulation only") >= 2, (
        "both the dispatch and no-dispatch branches must say nothing was sent")


# ================================================ honest labelling =====
def test_mode_is_declared_deterministic_not_model_assisted(index_html):
    assert "ACTIVE SHADOW" in index_html
    assert "Deterministic analysis" in index_html
    assert "simulation only" in index_html.lower()


def test_no_model_call_is_introduced(js):
    """The point is to watch OUR software decide. Comments may discuss
    models -- one explains that Live Operations keeps working while
    Claude is idle -- so the check runs on code, not prose."""
    code = _strip_comments(js).lower()
    for forbidden in ("anthropic", "openai", "claude", "gpt-", "completion(", "llm"):
        assert forbidden not in code, f"a model dependency appeared: {forbidden}"


def test_the_misleading_passive_only_label_is_gone(index_html):
    assert "passive capture &middot; no model required" not in index_html


# ============================================== decisions are real =====
def test_shadow_decisions_come_from_existing_backend_state(js):
    """No second decision engine: every branch reads the status the
    deterministic pipeline already produced."""
    body = _strip_comments(_body(js, "shadowDecision"))
    for state in ("HUMAN_REVIEW_NEEDED", "ALREADY DISPATCHED", "WAITING FOR HUMAN DISPATCH",
                  "REPOST AFTER PRIOR DISPATCH", "CLOSING OBSERVED", "GROUP MAPPING REQUIRED"):
        assert state in body, f"missing backend state {state}"
    for invented in ("available", "eta", "price", "estimate"):
        assert invented not in body.lower(), f"shadow invents {invented}"


def test_a_review_hold_is_never_a_dispatch_decision(js):
    body = _body(js, "shadowDecision")
    first = body[body.index("const s = c.status"):body.index('if (s === "ALREADY DISPATCHED")')]
    assert "review_reason" in first and '"Human review"' in first, (
        "a safety hold must be decided before any dispatch branch is considered")


def test_uncertainty_falls_back_to_review_or_cannot_route(js):
    body = _body(js, "shadowDecision")
    assert 'c.recommended && c.proposed_message' in body, (
        "dispatch requires both a technician and a message")
    assert '"Cannot route"' in body


# ============================================ human comparison =========
def test_human_action_uses_only_observed_evidence(js):
    """Read-only evidence the system already captures: what the owner
    actually sent, and sightings in confirmed technician groups."""
    body = _strip_comments(_body(js, "humanAction"))
    # actual_destination is where the job was OBSERVED going, derived
    # server-side from hand-sent messages and confirmed-group sightings.
    assert "actual_destination" in body
    assert "routing_match" in body
    for forbidden in ("fetch(", "Api.", "prompt(", "confirm("):
        assert forbidden not in body


def test_comparison_does_not_declare_a_winner(js):
    body = _body(js, "comparison")
    assert '"Match"' in body and "Different" in body
    for judgement in ("wrong", "error", "incorrect", "mistake", "better"):
        assert judgement not in body.lower(), (
            "the comparison reports a difference; it does not adjudicate it")


def test_waiting_is_distinct_from_disagreement(js):
    body = _body(js, "comparison")
    assert '"waiting"' in body
    assert "Waiting for human action" in body


# ============================================ session scoping ==========
def test_the_shadow_session_starts_when_the_console_opens(js):
    assert "const SHADOW_SESSION_START" in js
    assert "formatDisplayTime(SHADOW_SESSION_START)" in js


def test_history_does_not_flood_the_session(js):
    """Old observations stay available to correlation -- they are real
    evidence -- but the visible session follows current work."""
    body = _body(js, "shadowJobs")
    assert "SHADOW_SESSION_START.getTime()" in body
    assert ">=" in body


def test_only_real_candidates_reach_the_shadow_view(js):
    body = _strip_comments(_body(js, "shadowJobs"))
    assert "state.candidates" in body and "state.candidatesAll" in body
    for fake in ("demo", "sample", "fixture", "mock"):
        assert fake not in body.lower(), f"shadow view includes {fake} data"


def test_shadow_reports_on_every_evaluated_job_not_just_actionable(js):
    """The judgement not to act is exactly what the owner is watching."""
    assert "state.candidatesAll = (wa.candidates || [])" in js


# ================================================ timestamps ===========
def test_shadow_times_use_the_shared_12_hour_formatter(js):
    body = _body(js, "renderShadow")
    assert "formatDisplayTime(c.received_at)" in body
    assert body.count("toLocaleTimeString") == 0


def test_evidence_epochs_are_parsed_not_invented(js):
    body = _body(js, "parseEventStamp")
    assert "parseInt" in body
    assert "return null" in body, "a missing timestamp stays missing"
