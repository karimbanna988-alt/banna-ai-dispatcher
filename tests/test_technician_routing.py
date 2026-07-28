"""Tests for storage.list_eligible_technicians_for() -- the read-only,
multi-candidate eligibility listing added for the real-roster routing
preview. All technician names here are fabricated (Tech A/B/C-style),
never the real roster names."""

from pathlib import Path

import pytest

from dispatcher import storage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


# -------------------------------------------------- Basic single-tech area
def test_single_eligible_technician_for_area_with_no_skills(conn):
    storage.add_technician(conn, name="Fabricated Tech One", areas=["Faketon"])
    eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon")
    assert [t["name"] for t in eligible] == ["Fabricated Tech One"]


def test_no_eligible_technicians_for_uncovered_area(conn):
    storage.add_technician(conn, name="Fabricated Tech One", areas=["Faketon"])
    eligible = storage.list_eligible_technicians_for(conn, area_label="Nowhereville")
    assert eligible == []


def test_inactive_technician_excluded_by_default(conn):
    tech_id = storage.add_technician(conn, name="Fabricated Tech One", areas=["Faketon"])
    storage.set_technician_active(conn, tech_id, False)
    eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon")
    assert eligible == []
    eligible_incl_inactive = storage.list_eligible_technicians_for(conn, area_label="Faketon", active_only=False)
    assert len(eligible_incl_inactive) == 1


# ------------------------------------------------------ Skill-based filtering
def test_service_category_skill_filters_correctly(conn):
    storage.add_technician(conn, name="Fabricated Lockout Tech", areas=["Faketon"], skills=["car lockout", "house lockout"])
    storage.add_technician(conn, name="Fabricated Key Tech", areas=["Faketon"], skills=["car keys", "house keys", "lock installation"])

    car_lockout_eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon", service_type="Car Lockout")
    assert [t["name"] for t in car_lockout_eligible] == ["Fabricated Lockout Tech"]

    house_keys_eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon", service_type="House Keys")
    assert [t["name"] for t in house_keys_eligible] == ["Fabricated Key Tech"]

    lock_install_eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon", service_type="Lock Installation")
    assert [t["name"] for t in lock_install_eligible] == ["Fabricated Key Tech"]


def test_technician_covering_both_categories_appears_for_either(conn):
    """A technician configured with all five categories must be eligible
    for both lockout and key-work service types (a fabricated stand-in
    for the real "covers both category groups" roster scenario)."""
    storage.add_technician(conn, name="Fabricated Lockout Tech", areas=["Faketon"], skills=["car lockout", "house lockout"])
    storage.add_technician(conn, name="Fabricated Key Tech", areas=["Faketon"], skills=["car keys", "house keys", "lock installation"])
    storage.add_technician(
        conn, name="Fabricated All-Rounder Tech", areas=["Faketon"],
        skills=["car lockout", "house lockout", "car keys", "house keys", "lock installation"],
    )

    car_lockout_eligible = {t["name"] for t in storage.list_eligible_technicians_for(conn, "Faketon", "Car Lockout")}
    assert car_lockout_eligible == {"Fabricated Lockout Tech", "Fabricated All-Rounder Tech"}

    house_keys_eligible = {t["name"] for t in storage.list_eligible_technicians_for(conn, "Faketon", "House Keys")}
    assert house_keys_eligible == {"Fabricated Key Tech", "Fabricated All-Rounder Tech"}


# --------------------------------------- Multiple eligible, no arbitrary order
def test_multiple_eligible_technicians_are_all_returned_not_reduced_to_one(conn):
    """Mirrors the real Savannah scenario: three technicians with no
    skill/vehicle restrictions covering the same area must ALL come back
    eligible -- never silently narrowed to one by insertion or id order."""
    storage.add_technician(conn, name="Fabricated Tech C", areas=["Faketon"])
    storage.add_technician(conn, name="Fabricated Tech A", areas=["Faketon"])
    storage.add_technician(conn, name="Fabricated Tech B", areas=["Faketon"])

    eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon")
    assert len(eligible) == 3
    # Returned in list_technicians()'s stable name-sorted order, not
    # insertion order -- proves the order is deterministic and NOT an
    # artifact of database row/id order (Tech C was inserted first).
    assert [t["name"] for t in eligible] == ["Fabricated Tech A", "Fabricated Tech B", "Fabricated Tech C"]


def test_eligibility_order_is_not_affected_by_technician_id_order(conn):
    """Insert in reverse-alphabetical id order, confirm the returned
    eligible list is still name-sorted, not id-sorted -- guards against a
    future regression that silently switches to `ORDER BY id`."""
    for name in ("Zeta Fabricated Tech", "Mid Fabricated Tech", "Alpha Fabricated Tech"):
        storage.add_technician(conn, name=name, areas=["Faketon"])
    eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon")
    assert [t["name"] for t in eligible] == ["Alpha Fabricated Tech", "Mid Fabricated Tech", "Zeta Fabricated Tech"]


# ---------------------------------------------------- Blank destinations
def test_blank_dispatch_and_closing_group_never_block_eligibility(conn):
    storage.add_technician(conn, name="Fabricated Tech One", areas=["Faketon"], dispatch_group=None, closing_group=None)
    eligible = storage.list_eligible_technicians_for(conn, area_label="Faketon")
    assert len(eligible) == 1
    assert eligible[0]["dispatch_group"] is None
    assert eligible[0]["closing_group"] is None
