"""Tests for Live Operations, real health, branding and the Apprentice.

The theme of this file is TRUTHFULNESS. A live feed that invents events,
a health bar that paints green when it does not know, or an Apprentice
that promotes a weekend of coverage into a permanent rule would each be
worse than not shipping the feature at all.
"""
import ast
import inspect
import pathlib

import pytest

from dispatcher import apprentice, branding, live_feed

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def item(**kw):
    base = {
        "time": "2026-08-14T21:46:02+00:00", "group_name": "Augusta - Adam",
        "role": "COMPANY_JOB_INTAKE", "role_label": "COMPANY INTAKE",
        "mapping_status": "CONFIRMED", "company": "Mike Jobs",
        "service_area": "Augusta", "technician": None,
        "interpretation": "NEW JOB DETECTED", "proposed_ticket": "JO47-TPXW",
        "confidence": "OK", "missing_fields": [], "next_action": "REVIEW -- READY TO PROMOTE",
        "text_preview": "Ticket: JO47-TPXW",
    }
    base.update(kw)
    return base


# ------------------------------------------------- the feed is a view --
def test_the_feed_never_parses_a_message_itself():
    """It must stay a projection. A parser here would be a SECOND
    interpretation engine that could disagree with the brain."""
    source = inspect.getsource(live_feed)
    for forbidden in ("re.compile", "extract_job", "message_patterns", "sqlite3", "SELECT "):
        assert forbidden not in source, f"live_feed must not use {forbidden}"


def test_the_feed_reports_the_brains_verdict_verbatim():
    events = live_feed.events_for(item())
    titles = [e["title"] for e in events]
    assert "NEW JOB DETECTED" in titles, "the brain's own label must be shown unchanged"


def test_an_observation_always_produces_a_received_event():
    events = live_feed.events_for(item())
    assert events[0]["type"] == live_feed.RECEIVED
    assert events[0]["title"] == "Augusta - Adam"


def test_the_next_step_is_always_stated_explicitly():
    events = live_feed.events_for(item())
    assert any(e["title"].startswith("NEXT:") for e in events)


def test_no_correlation_event_without_a_ticket():
    """Correlation is exact-ticket only. No ticket, no correlation line --
    never a guess from timing or proximity."""
    events = live_feed.events_for(item(proposed_ticket=None))
    assert not any(e["type"] == live_feed.CORRELATED for e in events)


def test_missing_fields_are_named_and_never_filled_in():
    events = live_feed.events_for(item(missing_fields=["phone", "address"]))
    why = " ".join(w for e in events for w in e["why"])
    assert "phone" in why and "address" in why
    assert "never invented" in why


def test_a_closing_reads_as_a_closing_even_when_it_needs_a_human():
    """REGRESSION: typing every event by next_action made the whole feed
    one flat HUMAN REVIEW column -- a $125 closing looked exactly like a
    stray @mention. Type says WHAT happened; needs_human says whether it
    needs the operator."""
    events = live_feed.events_for(item(
        interpretation="CLOSING RECEIVED -- $125 cash",
        next_action="HUMAN_REVIEW_NEEDED", role_label="TECH CLOSINGS"))
    verdict = [e for e in events if e["type"] == live_feed.CLOSING]
    assert verdict, "a closing must be typed CLOSING"
    assert verdict[0]["needs_human"] is True


def test_an_unrecognised_message_routed_to_a_human_is_typed_human_review():
    events = live_feed.events_for(item(
        interpretation=None, next_action="HUMAN_REVIEW_NEEDED"))
    assert live_feed.event_type_for(None, "HUMAN_REVIEW_NEEDED") == live_feed.HUMAN_REVIEW
    assert any(e["needs_human"] for e in events)


def test_every_event_type_is_in_the_declared_vocabulary():
    for kw in ({}, {"interpretation": "TECHNICIAN K"}, {"interpretation": "CLOSING RECEIVED -- $1 cash"},
               {"interpretation": "JOB FORWARDED TO TECHNICIAN"}, {"interpretation": None}):
        for event in live_feed.events_for(item(**kw)):
            assert event["type"] in live_feed.EVENT_TYPES


def test_a_multiline_message_stays_one_feed_line():
    events = live_feed.events_for(item(text_preview="line one\nline two\n\nline three"))
    for why in events[0]["why"]:
        assert "\n" not in why


def test_the_feed_shows_no_private_reasoning_only_business_evidence():
    events = live_feed.events_for(item())
    why = " ".join(w for e in events for w in e["why"]).lower()
    for leak in ("because i", "i think", "reasoning", "chain of thought", "let me"):
        assert leak not in why


# ---------------------------------------------------------- branding --
def test_product_and_tenant_are_separate_concepts():
    b = branding.branding()
    assert b["product_name"] == "BANNA DISPATCH"
    assert b["tenant_name"] == "Luxor Locksmith"


def test_the_tenant_is_configuration_not_a_fork(monkeypatch):
    """Pointing Banna Dispatch at another locksmith must be a config
    change, not a second application."""
    monkeypatch.setenv("BANNA_TENANT_NAME", "ABC Locksmith")
    import importlib

    reloaded = importlib.reload(branding)
    try:
        assert reloaded.branding()["tenant_name"] == "ABC Locksmith"
    finally:
        monkeypatch.delenv("BANNA_TENANT_NAME", raising=False)
        importlib.reload(branding)


def test_branding_reads_only_its_own_configuration_keys():
    """Checked against CODE, not the docstring -- branding.py explains
    the LUXOR_* rule in prose, and a naive substring scan would match
    the explanation itself."""
    tree = ast.parse(inspect.getsource(branding))
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    env_keys = {v for v in literals if v.isupper() and "_" in v}
    assert env_keys, "branding should read named configuration keys"
    assert all(k.startswith("BANNA_") for k in env_keys), (
        f"branding must not read or redefine a LUXOR_* key: {env_keys}")


def test_the_luxor_names_were_not_globally_renamed():
    """The real risk of a rebrand is a blind find-and-replace. LUXOR_*
    env vars, fixtures and scripts are load-bearing; only the visible UI
    was rebranded, so those references must still be here."""
    survivors = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if "LUXOR" in path.read_text(encoding="utf-8", errors="ignore"):
            survivors.append(path.name)
    for extra in ("start_crm.ps1", "stop_crm.ps1"):
        assert (REPO_ROOT / "scripts" / extra).is_file(), f"{extra} must not be renamed"
    assert survivors, "expected existing LUXOR references to be left intact"


# ------------------------------------------------------- health bar ---
def _route_source(name: str) -> str:
    tree = ast.parse((REPO_ROOT / "operator_api.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_health_never_polls_the_bridge_over_the_network():
    """operator_api.py deliberately makes no outbound HTTP calls. Health
    must stay derived from local evidence."""
    source = _route_source("route_live_health")
    for forbidden in ("urlopen", "requests.", "http.client", "socket."):
        assert forbidden not in source


def test_adapter_health_comes_from_the_real_thread():
    source = _route_source("route_live_health")
    assert "is_alive()" in source, "adapter health must be the thread's actual liveness"


def test_an_unreadable_queue_is_unknown_and_never_healthy():
    """A false green here would hide exactly the backlog it exists to
    reveal -- found live with 25 stuck observations."""
    source = _route_source("route_live_health")
    assert "queue_depth is not None" in source
    js = (REPO_ROOT / "operator_ui_v2_connected" / "js" / "console.js").read_text(encoding="utf-8")
    assert 'q.depth == null ? "unknown"' in js


def test_shadow_mode_does_not_claim_analysis_just_because_capture_is_alive():
    source = _route_source("route_live_health")
    assert "active_analysis.lock" in source, (
        "ACTIVE ANALYSIS must require a deliberate marker, not merely live capture")
    assert "PASSIVE" in source


def test_uptime_is_omitted_rather_than_invented():
    source = _route_source("route_live_health")
    assert "_RUNTIME" in source and "started_at" in source


# ------------------------------------------------------- apprentice ---
def obs(n, destination="Toto", day="2026-08-14", source="Mike Jobs",
        area="Greenville", service="Lockout", start=0):
    return [apprentice.RoutingObservation(
        source=source, area=area, service=service, destination=destination,
        ticket=f"T{start + i}", day=day) for i in range(n)]


def test_one_example_never_creates_a_rule():
    assert apprentice.build_suggestions(obs(1)) == []


def test_thin_evidence_is_silent_not_low_confidence():
    """Below the bar the correct output is nothing at all."""
    assert apprentice.build_suggestions(obs(3)) == []


def test_a_burst_on_too_few_days_is_capped_as_temporary_coverage():
    """THE trap this module exists to avoid: a stand-in covering a
    weekend must never become a permanent assignment, however many
    messages it produced."""
    suggestions = apprentice.build_suggestions(obs(20, day="2026-08-14"))
    # 20 tickets but a single day -> not even proposed (needs >= 2 days).
    assert suggestions == []

    spread = obs(10, day="2026-08-13") + obs(10, day="2026-08-14", start=100)
    result = apprentice.build_suggestions(spread)
    assert len(result) == 1
    assert result[0].status == apprentice.STATUS_TEMPORARY
    assert result[0].confidence == apprentice.CONFIDENCE_LOW
    assert any("temporary coverage" in n.lower() for n in result[0].notes)


def test_broad_consistent_evidence_can_reach_high_confidence():
    spread = []
    for i, day in enumerate(("2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13")):
        spread += obs(3, day=day, start=i * 10)
    result = apprentice.build_suggestions(spread)
    assert len(result) == 1
    assert result[0].confidence == apprentice.CONFIDENCE_HIGH
    assert result[0].status == apprentice.STATUS_OBSERVED


def test_a_conflicting_destination_is_reported_never_hidden():
    spread = []
    for i, day in enumerate(("2026-08-10", "2026-08-11", "2026-08-12")):
        spread += obs(3, day=day, start=i * 10)
    spread += obs(2, destination="Joe", day="2026-08-12", start=500)
    result = apprentice.build_suggestions(spread)
    assert result[0].conflicts == 2
    assert "Joe" in result[0].conflicting_destinations
    assert result[0].confidence != apprentice.CONFIDENCE_HIGH


def test_a_suggestion_is_never_active_and_never_pre_approved():
    spread = []
    for i, day in enumerate(("2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13")):
        spread += obs(3, day=day, start=i * 10)
    for s in apprentice.build_suggestions(spread):
        assert s.approved is False
        assert s.as_dict()["active"] is False
        assert any("owner approves" in n.lower() for n in s.notes)


def test_owner_confirmed_rules_are_labelled_as_corroboration():
    spread = []
    for i, day in enumerate(("2026-08-10", "2026-08-11", "2026-08-12")):
        spread += obs(3, day=day, start=i * 10)
    confirmed = {("Mike Jobs", "Greenville", "Lockout", "Toto")}
    result = apprentice.build_suggestions(spread, owner_confirmed=confirmed)
    assert result[0].status == apprentice.STATUS_OWNER_CONFIRMED


def test_the_apprentice_cannot_write_anything():
    """No approval function, no SQL, no connection -- the module is inert
    by construction, not by convention."""
    source = inspect.getsource(apprentice)
    for forbidden in ("sqlite3", "conn.", "INSERT ", "UPDATE ", "DELETE ", "commit("):
        assert forbidden not in source, f"apprentice must stay read-only ({forbidden})"


def test_no_approval_endpoint_exists_anywhere():
    api_source = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    for forbidden in ("apprentice_approve", "approve_playbook", "activate_playbook"):
        assert forbidden not in api_source, (
            "this sprint deliberately ships no activation path for a playbook")


def test_the_apprentice_route_is_read_only_and_says_so():
    source = _route_source("route_apprentice_suggestions")
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "commit()"):
        assert forbidden not in source
    assert "'read_only': True" in source or '"read_only": True' in source
