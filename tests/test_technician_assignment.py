"""Tests for the technician roster and per-job assignment layer.

No technician-identity concept existed anywhere in the codebase before
this -- confirmed by inspection of storage.py, rules/__init__.py,
rules/service_areas.py, and rules/daily.py, where the only prior notion
was an *area-level* default_technician_availability flag, never a named
technician. This file exercises the new roster (technicians table),
storage.assign_technician(), and its integration with
storage.derive_job_status().
"""

import json
from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.rules.service_areas import ServiceAreaRecord

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def confirmed_area(city="Faketown", state="NC"):
    """A synthetic, known, active service area -- areas no longer hold
    dispatch/closing group data at all (that's technician-owned, opaque
    data), so "confirmed" now depends only on being found and active."""
    return ServiceAreaRecord(city=city, state=state, default_technician_availability="available")


def make_job(conn, vehicle_rules, raw="agn_clean.txt"):
    job = extract_job(load(raw), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    return job_id


# ---------------------------------------------------------------- Schema ---
def test_technicians_table_and_jobs_column_created(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(technicians)")}
    assert columns == {
        "id", "name", "active", "areas_json", "skills_json", "vehicle_restrictions_json",
        "dispatch_group", "closing_group", "created_at", "updated_at",
    }
    jobs_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "assigned_technician_id" in jobs_columns


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "idem.db"
    conn1 = storage.connect(db_path)
    tech_id = storage.add_technician(conn1, "Alex", areas=["Faketown"])
    conn2 = storage.connect(db_path)  # re-run _ensure_schema against the same file
    assert storage.get_technician(conn2, tech_id)["name"] == "Alex"


def test_foreign_keys_reject_an_invalid_assigned_technician_id(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute(
        "INSERT INTO jobs (ticket_number, source, latest_fields_json, created_at, updated_at) "
        "VALUES ('FKTEST', 'AGN', '{}', '2026-07-28T00:00:00+00:00', '2026-07-28T00:00:00+00:00')"
    )
    job_id = conn.execute("SELECT id FROM jobs WHERE ticket_number = 'FKTEST'").fetchone()[0]
    with pytest.raises(Exception):
        conn.execute("UPDATE jobs SET assigned_technician_id = 9999 WHERE id = ?", (job_id,))
        conn.commit()


# --------------------------------------------------------- Roster CRUD -----
def test_add_and_list_technicians(conn):
    storage.add_technician(conn, "Alex", areas=["Faketown"], skills=["lockout"], vehicle_restrictions=["BMW"])
    storage.add_technician(conn, "Sam", areas=["OtherArea"])
    techs = storage.list_technicians(conn)
    names = {t["name"] for t in techs}
    assert names == {"Alex", "Sam"}
    alex = next(t for t in techs if t["name"] == "Alex")
    assert alex["active"] is True
    assert alex["areas"] == ["Faketown"]
    assert alex["skills"] == ["lockout"]
    assert alex["vehicle_restrictions"] == ["BMW"]


def test_duplicate_technician_name_rejected(conn):
    storage.add_technician(conn, "Alex", areas=["Faketown"])
    with pytest.raises(Exception):
        storage.add_technician(conn, "Alex", areas=["OtherArea"])


def test_set_technician_active_toggle(conn):
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    storage.set_technician_active(conn, tech_id, False)
    assert storage.get_technician(conn, tech_id)["active"] is False
    storage.set_technician_active(conn, tech_id, True)
    assert storage.get_technician(conn, tech_id)["active"] is True


def test_list_technicians_active_only_filter(conn):
    active_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    inactive_id = storage.add_technician(conn, "Sam", areas=["Faketown"])
    storage.set_technician_active(conn, inactive_id, False)
    active = storage.list_technicians(conn, active_only=True)
    assert [t["id"] for t in active] == [active_id]


# ------------------------------------------------------------ Assignment ---
def test_successful_assignment(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "assigned"
    assert result.technician_id == tech_id
    row = conn.execute("SELECT assigned_technician_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row[0] == tech_id


def test_assignment_is_recorded_in_the_audit_timeline(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    timeline = storage.get_audit_timeline_by_job_id(conn, job_id)
    assignment_events = [e for e in timeline if e["message_type"] == "ASSIGNMENT"]
    assert len(assignment_events) == 1
    assert assignment_events[0]["final_action"] == "Assign"
    assert "Alex" in assignment_events[0]["raw_message"]


def test_assignment_preserves_job_status(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)  # status becomes "Approved" via final_action="Approve"
    before = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])

    storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    after = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert before == after == "Approved"


def test_reassignment(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech1 = storage.add_technician(conn, "Alex", areas=["Faketown"])
    tech2 = storage.add_technician(conn, "Sam", areas=["Faketown"])
    storage.assign_technician(conn, job_id, tech1, [confirmed_area()])

    result = storage.assign_technician(conn, job_id, tech2, [confirmed_area()])

    assert result.outcome == "reassigned"
    row = conn.execute("SELECT assigned_technician_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row[0] == tech2


def test_removal(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    result = storage.assign_technician(conn, job_id, None, [confirmed_area()])

    assert result.outcome == "removed"
    assert result.technician_id is None
    row = conn.execute("SELECT assigned_technician_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row[0] is None


def test_assigning_a_nonexistent_job_raises(conn):
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    with pytest.raises(ValueError):
        storage.assign_technician(conn, 999999, tech_id, [confirmed_area()])


def test_assigning_a_nonexistent_technician_raises(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    with pytest.raises(ValueError):
        storage.assign_technician(conn, job_id, 999999, [confirmed_area()])


# ------------------------------------------------- Prevention (Rule #8) ----
def test_inactive_technician_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    storage.set_technician_active(conn, tech_id, False)

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "inactive" in result.reason
    row = conn.execute("SELECT assigned_technician_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row[0] is None


def test_area_mismatch_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["OtherArea"])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "does not cover service area" in result.reason


def test_technician_with_no_configured_areas_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=[])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "no configured coverage areas" in result.reason


def test_unresolvable_service_area_blocked(conn, vehicle_rules):
    """If the job's city/state doesn't resolve against the known service
    areas at all (e.g. rules/service_areas.yaml has no matching entry --
    matches today's real file for any city the simulator/fixtures use),
    assignment must block rather than guess. Areas no longer have an
    "unconfirmed" status of their own (see rules/service_areas.py) --
    the only way an area lookup can fail now is not being found/active."""
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])

    result = storage.assign_technician(conn, job_id, tech_id, [])  # no matching area at all

    assert result.outcome == "handle_manually"
    assert "job's service area is not confirmed" in result.reason


def test_missing_city_and_state_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    row = conn.execute("SELECT latest_fields_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    fields = json.loads(row[0])
    fields["city"] = None
    fields["state"] = None
    conn.execute("UPDATE jobs SET latest_fields_json = ? WHERE id = ?", (json.dumps(fields), job_id))
    conn.commit()
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "no city/state on record" in result.reason


def test_skills_configured_but_job_service_type_missing_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    row = conn.execute("SELECT latest_fields_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    fields = json.loads(row[0])
    fields["service_type"] = None
    conn.execute("UPDATE jobs SET latest_fields_json = ? WHERE id = ?", (json.dumps(fields), job_id))
    conn.commit()
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"], skills=["lockout"])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "cannot verify technician" in result.reason


def test_skills_mismatch_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)  # agn_clean.txt's service_type is a lockout-style job
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"], skills=["ignition repair only"])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "configured skills" in result.reason


def test_vehicle_restriction_missing_vehicle_info_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    row = conn.execute("SELECT latest_fields_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    fields = json.loads(row[0])
    fields["vehicle"] = None
    conn.execute("UPDATE jobs SET latest_fields_json = ? WHERE id = ?", (json.dumps(fields), job_id))
    conn.commit()
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"], vehicle_restrictions=["BMW"])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "cannot verify technician" in result.reason


def test_vehicle_restriction_match_blocked(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    row = conn.execute("SELECT latest_fields_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    fields = json.loads(row[0])
    fields["vehicle"] = "2015 BMW 328i"
    conn.execute("UPDATE jobs SET latest_fields_json = ? WHERE id = ?", (json.dumps(fields), job_id))
    conn.commit()
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"], vehicle_restrictions=["BMW"])

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "handle_manually"
    assert "explicitly restricted" in result.reason


def test_blocked_reassignment_does_not_clear_existing_assignment(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    good_tech = storage.add_technician(conn, "Alex", areas=["Faketown"])
    storage.assign_technician(conn, job_id, good_tech, [confirmed_area()])

    bad_tech = storage.add_technician(conn, "Sam", areas=["OtherArea"])
    result = storage.assign_technician(conn, job_id, bad_tech, [confirmed_area()])

    assert result.outcome == "handle_manually"
    row = conn.execute("SELECT assigned_technician_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row[0] == good_tech  # untouched by the blocked attempt


def test_blocked_assignment_still_records_a_handle_manually_event(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["OtherArea"])

    storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    timeline = storage.get_audit_timeline_by_job_id(conn, job_id)
    assignment_events = [e for e in timeline if e["message_type"] == "ASSIGNMENT"]
    assert len(assignment_events) == 1
    assert assignment_events[0]["final_action"] == "Handle Manually"


# --------------------------------------------------- derive_job_status -----
def test_derive_job_status_assignment_preserves_previous():
    for action in ("Assign", "Reassign", "Remove"):
        assert storage.derive_job_status("Approved", storage.MessageType.ASSIGNMENT.value, action) == "Approved"
        assert storage.derive_job_status("In Progress", storage.MessageType.ASSIGNMENT.value, action) == "In Progress"


def test_derive_job_status_assignment_handle_manually_wins():
    assert storage.derive_job_status("Approved", storage.MessageType.ASSIGNMENT.value, "Handle Manually") == "Handle Manually"


def test_derive_job_status_assignment_unknown_action_falls_back():
    assert storage.derive_job_status("Approved", storage.MessageType.ASSIGNMENT.value, "Bogus") == "Needs Review"


# ---------------------------------- Opaque dispatch_group/closing_group ----
def test_technician_dispatch_and_closing_group_are_stored_verbatim(conn):
    """These are opaque strings -- storage never parses or reasons about
    their wording, just stores and later surfaces them verbatim."""
    tech_id = storage.add_technician(
        conn, "Alex", areas=["Faketown"],
        dispatch_group="Fabricated Tech Dispatch Group", closing_group="Fabricated Tech Closing Group",
    )
    tech = storage.get_technician(conn, tech_id)
    assert tech["dispatch_group"] == "Fabricated Tech Dispatch Group"
    assert tech["closing_group"] == "Fabricated Tech Closing Group"


def test_technician_dispatch_and_closing_group_default_to_none(conn):
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    tech = storage.get_technician(conn, tech_id)
    assert tech["dispatch_group"] is None
    assert tech["closing_group"] is None


def test_successful_assignment_surfaces_technicians_opaque_groups(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(
        conn, "Alex", areas=["Faketown"],
        dispatch_group="Fabricated Tech Dispatch Group", closing_group="Fabricated Tech Closing Group",
    )

    result = storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    assert result.outcome == "assigned"
    assert result.dispatch_group == "Fabricated Tech Dispatch Group"
    assert result.closing_group == "Fabricated Tech Closing Group"


def test_removal_result_has_no_dispatch_or_closing_group(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"], dispatch_group="Fabricated Tech Dispatch Group")
    storage.assign_technician(conn, job_id, tech_id, [confirmed_area()])

    result = storage.assign_technician(conn, job_id, None, [confirmed_area()])

    assert result.dispatch_group is None
    assert result.closing_group is None


# --------------------------------- update_technician_destinations() ---


def test_update_technician_destinations_sets_both_fields(conn):
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])

    result = storage.update_technician_destinations(conn, tech_id, "New Dispatch Group", "New Closing Group")

    assert result["previous_dispatch_group"] is None
    assert result["previous_closing_group"] is None
    assert result["dispatch_group"] == "New Dispatch Group"
    assert result["closing_group"] == "New Closing Group"
    tech = storage.get_technician(conn, tech_id)
    assert tech["dispatch_group"] == "New Dispatch Group"
    assert tech["closing_group"] == "New Closing Group"


def test_update_technician_destinations_captures_previous_values(conn):
    tech_id = storage.add_technician(
        conn, "Alex", areas=["Faketown"], dispatch_group="Old Dispatch", closing_group="Old Closing",
    )

    result = storage.update_technician_destinations(conn, tech_id, "New Dispatch", "New Closing")

    assert result["previous_dispatch_group"] == "Old Dispatch"
    assert result["previous_closing_group"] == "Old Closing"


def test_update_technician_destinations_changes_only_the_two_fields(conn):
    tech_id = storage.add_technician(
        conn, "Alex", areas=["Faketown"], skills=["lockout"], vehicle_restrictions=["BMW"],
    )
    before = storage.get_technician(conn, tech_id)

    storage.update_technician_destinations(conn, tech_id, "New Dispatch", "New Closing")

    after = storage.get_technician(conn, tech_id)
    assert after["name"] == before["name"]
    assert after["areas"] == before["areas"]
    assert after["skills"] == before["skills"]
    assert after["vehicle_restrictions"] == before["vehicle_restrictions"]
    assert after["active"] == before["active"]


def test_update_technician_destinations_does_not_change_other_technicians(conn):
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    other_id = storage.add_technician(conn, "Sam", areas=["OtherArea"], dispatch_group="Sam Dispatch")

    storage.update_technician_destinations(conn, tech_id, "Alex Dispatch", "Alex Closing")

    other = storage.get_technician(conn, other_id)
    assert other["dispatch_group"] == "Sam Dispatch"
    assert other["closing_group"] is None


def test_update_technician_destinations_raises_for_unknown_technician(conn):
    with pytest.raises(ValueError):
        storage.update_technician_destinations(conn, 999999, "Dispatch", "Closing")


def test_update_technician_destinations_writes_non_job_audit_event(conn):
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])

    result = storage.update_technician_destinations(conn, tech_id, "New Dispatch", "New Closing")

    row = conn.execute(
        "SELECT job_id, ticket_number, message_type, final_action, raw_message FROM events WHERE id = ?",
        (result["audit_event_id"],),
    ).fetchone()
    assert row[0] is None
    assert row[1] is None
    assert row[2] == storage.MessageType.OPERATIONAL_NOTE.value
    assert row[3] == "Technician Configuration Updated"
    payload = json.loads(row[4])
    assert payload["technician_id"] == tech_id
    assert payload["new"]["dispatch_group"] == "New Dispatch"


def test_update_technician_destinations_does_not_create_or_touch_any_job(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    before = storage.get_job_by_id(conn, job_id)

    storage.update_technician_destinations(conn, tech_id, "New Dispatch", "New Closing")

    after = storage.get_job_by_id(conn, job_id)
    assert before == after
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


# --------------------------------------- update_technician_areas() ---

def test_update_technician_areas_replaces_the_area_list(conn):
    tech_id = storage.add_technician(conn, "Kimo", areas=["Greenville"])

    result = storage.update_technician_areas(conn, tech_id, ["Alabama"])

    assert result["previous_areas"] == ["Greenville"]
    assert result["areas"] == ["Alabama"]
    tech = storage.get_technician(conn, tech_id)
    assert tech["areas"] == ["Alabama"]


def test_update_technician_areas_changes_only_areas(conn):
    tech_id = storage.add_technician(
        conn, "Kimo", areas=["Greenville"], skills=["car lockout"],
        dispatch_group="Old Dispatch", closing_group="Old Closing",
    )
    before = storage.get_technician(conn, tech_id)

    storage.update_technician_areas(conn, tech_id, ["Alabama"])

    after = storage.get_technician(conn, tech_id)
    assert after["name"] == before["name"]
    assert after["skills"] == before["skills"]
    assert after["dispatch_group"] == before["dispatch_group"]
    assert after["closing_group"] == before["closing_group"]
    assert after["active"] == before["active"]


def test_update_technician_areas_raises_for_unknown_technician(conn):
    with pytest.raises(ValueError):
        storage.update_technician_areas(conn, 999999, ["Alabama"])


def test_update_technician_areas_writes_non_job_audit_event_preserving_previous_value(conn):
    tech_id = storage.add_technician(conn, "Kimo", areas=["Greenville"])

    result = storage.update_technician_areas(conn, tech_id, ["Alabama"])

    row = conn.execute(
        "SELECT job_id, message_type, final_action, raw_message FROM events WHERE id = ?",
        (result["audit_event_id"],),
    ).fetchone()
    assert row[0] is None
    assert row[1] == storage.MessageType.OPERATIONAL_NOTE.value
    assert row[2] == "Technician Configuration Updated"
    payload = json.loads(row[3])
    assert payload["technician_id"] == tech_id
    assert payload["previous"]["areas"] == ["Greenville"]
    assert payload["new"]["areas"] == ["Alabama"]


def test_update_technician_areas_does_not_touch_any_job_or_other_technician(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Kimo", areas=["Greenville"])
    other_id = storage.add_technician(conn, "Sam", areas=["OtherArea"])
    before_job = storage.get_job_by_id(conn, job_id)

    storage.update_technician_areas(conn, tech_id, ["Alabama"])

    assert storage.get_job_by_id(conn, job_id) == before_job
    assert storage.get_technician(conn, other_id)["areas"] == ["OtherArea"]
