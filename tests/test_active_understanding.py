"""Stale-work separation, WhatsApp dispatch candidates, and the owner's
batch-4 mapping decisions.

The through-line: Banna may UNDERSTAND anything, but it may never invent
business truth -- not a job, not a technician, not a permanent
assignment, and not a destination for a group the owner has not
confirmed.
"""
import ast
import inspect
import pathlib
import sqlite3

import pytest

from dispatcher import dispatch_candidates as dc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = REPO_ROOT / "data" / "dispatcher.db"


def base(**kw):
    args = dict(
        message_id="m1", received_at="2026-08-14T21:00:00+00:00",
        group_name="Augusta - Adam", group_confirmed=True, company="Mike Jobs",
        ticket="JO47-TPXW", service="Car lockout", city="Augusta", state="GA",
        canonical_area="Augusta", eligible=[{"name": "Sebai", "dispatch_group": "Sebai Augusta Dispatch"}],
        missing_fields=[], proposed_message="Ticket: JO47-TPXW", correlated_job_id=None,
    )
    args.update(kw)
    return dc.build_candidate(**args)


# ------------------------------------------------- candidate safety ---
def test_a_candidate_is_never_canonical_business_state():
    """The whole safety story. A candidate is understanding; a job is
    truth. Confusing them would put invented work into the record."""
    c = base()
    assert c["is_candidate"] is True
    assert c["canonical_job"] is False


def test_the_candidate_module_cannot_write_anything():
    source = inspect.getsource(dc)
    for forbidden in ("sqlite3", "conn.", "INSERT ", "UPDATE ", "DELETE ", "commit("):
        assert forbidden not in source, f"dispatch_candidates must stay pure ({forbidden})"


def test_it_reimplements_no_parser_router_or_formatter():
    """Parsing, routing and message text are reused from the existing
    modules. A private copy here could disagree with the real dispatch
    path.

    Checked against CODE only -- the module documents what it reuses, and
    a naive substring scan would match its own explanation."""
    code = _code_only(dc)
    for forbidden in ("re.compile", "extract_job", "resolve_job_routing", "format_technician_message"):
        assert forbidden not in code


# ---------------------------------------------- recommendation rules --
def test_exactly_one_eligible_technician_becomes_a_recommendation():
    c = base()
    assert c["recommended"] == "Sebai"
    assert c["confidence"] == dc.CONFIDENCE_EXACT
    assert c["status"] == dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"]


def test_more_than_one_eligible_technician_is_a_human_decision():
    c = base(eligible=[{"name": "Tarek"}, {"name": "Omar Shata"}])
    assert c["recommended"] is None
    assert c["confidence"] == dc.CONFIDENCE_AMBIGUOUS
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["proposed_message"] is None, "never prepare a message for a technician nobody chose"


def test_no_eligible_technician_never_invents_one():
    c = base(eligible=[])
    assert c["recommended"] is None
    assert c["confidence"] == dc.CONFIDENCE_UNMATCHED
    assert c["status"] == dc.STATUS_HUMAN_REVIEW


def test_an_unresolved_area_blocks_routing():
    c = base(canonical_area=None, eligible=[])
    assert c["recommended"] is None
    assert any("routing cannot be applied" in w for w in c["why"])


def test_an_unconfirmed_group_can_never_route_a_job():
    """PHASE 7: guessing beats nothing here would send a real job to the
    wrong technician."""
    c = base(group_confirmed=False)
    assert c["status"] == dc.STATUS_MAPPING_REQUIRED
    assert c["recommended"] is None
    assert c["proposed_message"] is None


def test_no_ticket_means_no_candidate_recommendation():
    c = base(ticket=None)
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["recommended"] is None


def test_missing_facts_are_named_and_downgrade_confidence():
    c = base(missing_fields=["phone", "address"])
    assert "Missing: phone, address (never invented)" in c["why"]
    assert c["confidence"] == dc.CONFIDENCE_STRONG, "missing facts must not stay EXACT"


def test_why_is_business_evidence_not_reasoning():
    why = " ".join(base()["why"]).lower()
    assert "company:" in why and "area:" in why
    for leak in ("i think", "because i", "reasoning", "chain of thought"):
        assert leak not in why


# ---------------------------------------------------- the API route ---
def _route_source(name: str) -> str:
    tree = ast.parse((REPO_ROOT / "operator_api.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_the_candidate_route_writes_nothing():
    source = _route_source("route_whatsapp_candidates")
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "commit()", "promote_to_job", "confirm_whatsapp_dispatch"):
        assert forbidden not in source


def test_only_company_intake_groups_produce_candidates():
    """Technician groups carry replies, and IGNORE groups are internal
    chat. Neither posts a new job."""
    source = _route_source("route_whatsapp_candidates")
    assert "COMPANY_JOB_INTAKE" in source


def test_candidates_filter_before_slicing():
    """REGRESSION: slicing the newest observations BEFORE filtering to
    intake groups filled the window with technician chatter, so real job
    postings never became candidates at all."""
    source = _route_source("route_whatsapp_candidates")
    intake_at = source.index("COMPANY_JOB_INTAKE")
    slice_at = source.index("[:40]")
    assert intake_at < slice_at, "filter to intake groups first, then take the newest N"


# ------------------------------------------------------ stale split ---
def test_stale_active_jobs_are_split_out_not_deleted():
    source = _route_source("route_active")
    assert "stale" in source
    assert "ACTION_CENTER_MESSAGE_LOOKBACK_MINUTES" in source, (
        "reuse the existing 24h definition rather than inventing a third threshold")
    for forbidden in ("DELETE ", "UPDATE jobs", "derive_job_status"):
        assert forbidden not in source, "aging a job must never mutate or remove it"


def test_a_job_with_no_timestamp_is_never_treated_as_stale():
    source = _route_source("route_active")
    # ast.unparse normalises quoting, so match on the normalised form.
    assert "'age_minutes'] is None" in source


def test_the_iso_age_helper_is_not_the_seconds_helper():
    """REGRESSION: a second function named _age_minutes shadowed the
    original seconds-based one, so _new_job_dto passed a float into a
    string parser. Every TO DISPATCH item would have had age_minutes=None
    and route_new_jobs would then compare None > 60 and 500."""
    source = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    assert source.count("def _age_minutes(") == 1
    assert source.count("def _age_minutes_from_iso(") == 1


# -------------------------------------- owner batch-4 mapping truth ---
requires_db = pytest.mark.skipif(not DB.is_file(), reason="production database not present")


@pytest.fixture
def registry():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield {r["group_id"]: r for r in conn.execute(
            "SELECT group_id, exact_name, role, service_area, technician_id, "
            "confirmed_by_owner, notes FROM whatsapp_group_registry")}
    finally:
        conn.close()


@requires_db
def test_owner_confirmed_groups_are_keyed_by_exact_group_id(registry):
    for group_id, name in (
        ("120363428199635701@g.us", "Ashraf Charlotte Dispatch"),
        ("120363420633655712@g.us", "Osos nj Closings"),
        ("120363408345627351@g.us", "Luxor Savannah dispatch"),
        ("120363406073039318@g.us", "Vancouver Closings"),
        ("120363410595686038@g.us", "Trio Dispatch"),
    ):
        row = registry.get(group_id)
        assert row is not None, f"{name} missing from the registry"
        assert row["exact_name"] == name
        assert row["confirmed_by_owner"] == 1


@requires_db
def test_the_shared_savannah_group_has_no_permanent_technician(registry):
    """The owner's explicit architecture: a group maps to an AREA, the
    current technician comes from routing. Binding a person here would
    turn shared coverage into permanent ownership."""
    row = registry["120363408345627351@g.us"]
    assert row["technician_id"] is None
    assert row["service_area"] == "Savannah"
    assert "SHARED" in (row["notes"] or "").upper()


@requires_db
def test_trio_dispatch_is_excluded_from_operational_routing(registry):
    row = registry["120363410595686038@g.us"]
    assert row["role"] == "IGNORE"
    assert row["technician_id"] is None


@requires_db
def test_tarek_keeps_no_permanent_destinations_from_temporary_coverage():
    """Tarek covers Columbia temporarily; his own record must keep
    reflecting permanent Savannah, not that coverage."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT dispatch_group, closing_group, areas_json "
                           "FROM technicians WHERE name = 'Tarek'").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert not row["dispatch_group"], "temporary Columbia coverage must not become a permanent destination"
    assert "Savannah" in row["areas_json"]


@requires_db
def test_osos_is_not_mohamed_osama(registry):
    """The owner has stated this twice. They are different people who
    both work NJ."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        osos = conn.execute("SELECT id FROM technicians WHERE name = 'Osos'").fetchone()
        osama = conn.execute("SELECT id FROM technicians WHERE name = 'Mohamed Osama'").fetchone()
    finally:
        conn.close()
    assert osos and osama and osos["id"] != osama["id"]
    assert registry["120363420633655712@g.us"]["technician_id"] == osos["id"]


# ------------------------------------------------------ transport ----
def test_the_candidate_path_cannot_send_or_write_to_quo():
    """CODE only. The module's docstring necessarily contains the word
    "send" while explaining that the human does the sending."""
    code = _code_only(dc).lower()
    for forbidden in ("send(", "sendmessage", "quo", "requests.", "urlopen", "http"):
        assert forbidden not in code, f"dispatch_candidates must have no transport capability ({forbidden})"


def _code_only(module) -> str:
    """Module source with comments and docstrings removed, so a guard
    tests behaviour rather than the prose describing it."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ============================================================== SEMANTICS ==
# A job block appearing in a technician group means a human ALREADY sent
# it. Re-offering it as "waiting for human dispatch" would invite a
# duplicate send of work already on its way. Found in live traffic: the
# dispatcher forwards an Augusta job to "Sebai Augusta Dispatch" within
# ~10 seconds of it landing in "Augusta - Adam", and every one of those
# came back as a dispatch request.

def test_an_already_dispatched_job_is_not_offered_for_dispatch_again():
    c = base(dispatched_to=[{"group_name": "Sebai Augusta Dispatch", "technician": "Sebai"}])
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["status"] != dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"] is None, "nothing may be offered to send for work already sent"


def test_an_already_dispatched_job_becomes_routing_verification():
    """Observing it downstream still has value -- it proves the rules
    agree with what the human actually did."""
    c = base(dispatched_to=[{"group_name": "Sebai Augusta Dispatch", "technician": "Sebai"}])
    assert c["actual_destination"] == "Sebai"
    assert c["recommended"] == "Sebai"
    assert c["routing_match"] == dc.ROUTING_MATCH


def test_a_routing_disagreement_is_reported_not_hidden():
    c = base(dispatched_to=[{"group_name": "Joe Greenville Dispatch", "technician": "Joe"}])
    assert c["routing_match"] == dc.ROUTING_MISMATCH
    assert any("expected Sebai" in w for w in c["why"])


def test_routing_is_unverified_when_the_rules_could_not_recommend():
    c = base(eligible=[], dispatched_to=[{"group_name": "Sebai Augusta Dispatch", "technician": "Sebai"}])
    assert c["routing_match"] == dc.ROUTING_UNVERIFIED
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED


def test_a_closing_observation_is_never_a_dispatch_candidate():
    c = base(closing_seen_in=[{"group_name": "Sebai augusta closing", "technician": "Sebai"}])
    assert c["status"] == dc.STATUS_CLOSING_OBSERVED
    assert c["status"] != dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"] is None


def test_only_a_genuinely_undispatched_intake_job_waits_for_human_dispatch():
    assert base(dispatched_to=None, closing_seen_in=None)["status"] == dc.STATUS_WAITING_HUMAN
    for kw in ({"dispatched_to": [{"technician": "Sebai"}]},
               {"closing_seen_in": [{"technician": "Sebai"}]}):
        assert base(**kw)["status"] != dc.STATUS_WAITING_HUMAN


def test_downstream_evidence_only_counts_confirmed_technician_groups():
    """An unconfirmed group's traffic must not be treated as proof that a
    job was dispatched -- nor as proof that it was not."""
    source = _route_source("route_whatsapp_candidates")
    marker = source.index("dispatched_by_ticket")
    window = source[marker:marker + 900]
    assert "group_mapping_confirmed" in window
    assert "TECH_DISPATCH" in window and "TECH_CLOSINGS" in window


def test_downstream_correlation_is_by_exact_ticket_never_timing():
    source = _route_source("route_whatsapp_candidates")
    assert "ticket_number.value" in source
    for forbidden in ("timedelta", "seconds_between", "within_minutes", "closest"):
        assert forbidden not in source, "correlation must never be time-proximity based"


def test_one_ticket_cannot_produce_duplicate_candidates():
    """The same job is often reposted in an intake group; each repost
    must not become another board row."""
    source = _route_source("route_whatsapp_candidates")
    assert "seen_tickets" in source
    assert "continue" in source


def test_an_ignore_group_produces_no_candidate():
    """Trio Dispatch is internal chat. It is neither intake nor a
    technician destination, so it can neither create a candidate nor
    count as dispatch evidence."""
    source = _route_source("route_whatsapp_candidates")
    intake_guard = 'group_role\') == \'COMPANY_JOB_INTAKE\''
    assert intake_guard in source or '"COMPANY_JOB_INTAKE"' in source
    evidence = source[source.index("dispatched_by_ticket"):]
    assert "IGNORE" not in evidence, "IGNORE groups must not be dispatch evidence"


def test_ambiguous_origin_returns_human_review():
    assert base(group_confirmed=False)["status"] == dc.STATUS_MAPPING_REQUIRED
    assert base(ticket=None)["status"] == dc.STATUS_HUMAN_REVIEW
    assert base(eligible=[{"name": "A"}, {"name": "B"}])["status"] == dc.STATUS_HUMAN_REVIEW


def test_candidate_creation_still_mutates_nothing():
    source = _route_source("route_whatsapp_candidates")
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "commit()", "derive_job_status"):
        assert forbidden not in source
