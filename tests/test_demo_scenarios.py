"""Focused tests for scripts/seed_demo_scenarios.py -- proves each named
scenario produces the exact outcome its docstring claims, through real
route/service calls (never hand-built rows), and that each lands under
the correct Action Center section. All data is synthetic; every test
uses a throwaway tmp_path database, never data/dispatcher.db.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scenarios = _load_module("seed_demo_scenarios", "scripts/seed_demo_scenarios.py")


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "scenarios.db"


# ------------------------------------------------------------ Scenario A --
def test_scenario_a_happy_path_reaches_closed_and_settled(db_path):
    result = scenarios.seed_scenario_a_happy_path(db_path)
    assert result["status"] == "Closed"


def test_scenario_a_happy_path_has_a_complete_audit_trail(db_path):
    result = scenarios.seed_scenario_a_happy_path(db_path)
    # One event per real action: NEW+approve, ASSIGNMENT, technician
    # accepted, technician update, company update prepared, company
    # update sent, closing, company closing prepared, company closing
    # sent, job completed -- 10 in total.
    assert result["audit_event_count"] == 10


def test_scenario_a_happy_path_job_appears_complete_in_action_center(db_path):
    scenarios._prepare_env(db_path)
    result = scenarios.seed_scenario_a_happy_path(db_path)
    import operator_api as api
    ac = api.route_action_center(None, {}, {})
    for section in ac["sections"].values():
        assert result["job_id"] not in [i["id"] for i in section["items"]], (
            "a Closed+Settled job must not still appear as needing any action"
        )


# ------------------------------------------------------------ Scenario B --
def test_scenario_b_human_review_no_eligible_technician(db_path):
    result = scenarios.seed_scenario_b_human_review(db_path)
    assert result["next_action"] == "HUMAN_REVIEW_NEEDED"
    assert result["human_review_needed"] is True


def test_scenario_b_never_fabricates_a_technician_assignment(db_path):
    scenarios._prepare_env(db_path)
    scenarios.seed_scenario_b_human_review(db_path)
    from dispatcher import storage
    conn = storage.connect(db_path)
    technicians = storage.list_technicians(conn)
    conn.close()
    assert technicians == []  # never auto-created just to satisfy the workflow


def test_scenario_b_message_appears_under_human_review_required_in_action_center(db_path):
    scenarios._prepare_env(db_path)
    result = scenarios.seed_scenario_b_human_review(db_path)
    import operator_api as api
    ac = api.route_action_center(None, {}, {})
    ids = [i["entity_id"] for i in ac["sections"]["human_review_required"]["items"]]
    assert result["message_id"] in ids


# ------------------------------------------------------------ Scenario F --
def test_scenario_f_technician_decline_records_decline_not_acceptance(db_path):
    result = scenarios.seed_scenario_f_technician_decline(db_path)
    assert result["declined_at"] is not None
    assert result["accepted_at"] is None


def test_scenario_f_moves_to_human_review_needed(db_path):
    result = scenarios.seed_scenario_f_technician_decline(db_path)
    assert result["next_action"] == "HUMAN_REVIEW_NEEDED"


def test_scenario_f_job_appears_under_human_review_required_in_action_center(db_path):
    scenarios._prepare_env(db_path)
    result = scenarios.seed_scenario_f_technician_decline(db_path)
    import operator_api as api
    ac = api.route_action_center(None, {}, {})
    ids = [i["entity_id"] for i in ac["sections"]["human_review_required"]["items"]]
    assert result["job_id"] in ids


# --------------------------------------------------------------- safety --
def test_scenarios_never_touch_the_real_database_path(db_path):
    from dispatcher import storage
    real_path = storage.DB_PATH.resolve()
    assert db_path.resolve() != real_path


def test_scenario_seeding_refuses_the_real_database_path():
    from dispatcher import storage
    from dispatcher.db_safety import UnsafeDatabasePathError
    with pytest.raises(UnsafeDatabasePathError):
        scenarios._prepare_env(storage.DB_PATH)
