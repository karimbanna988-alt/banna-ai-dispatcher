"""Tests for scripts/apply_owner_mapping_batch_3.py -- always against a
tmp_path database, never the real one."""
import importlib.util
from pathlib import Path

import pytest

from dispatcher import storage

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "apply_owner_mapping_batch_3.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("apply_owner_mapping_batch_3", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def conn(tmp_path):
    c = storage.connect(tmp_path / "test.db")
    storage.add_technician(c, "Abozeid", areas=["Arlington"])
    storage.add_technician(c, "Fares", areas=["Virginia Beach"])
    storage.add_technician(c, "Tarek", areas=["Savannah"])
    storage.add_technician(c, "Ziyadah", areas=["Raleigh"])
    for gid, name, role in [
        ("120363403321651687@g.us", "Abozeid Arlington Dispatch", "TECH_DISPATCH"),
        ("120363422531151840@g.us", "Abozeid Arlington Closings", "TECH_CLOSINGS"),
        ("120363406134197775@g.us", "Fares Virginia Beach Dispatch", "TECH_DISPATCH"),
        ("120363408179176581@g.us", "Fares Virginia Beach Closings", "TECH_CLOSINGS"),
        ("120363425255488739@g.us", "Tarek columbia dispatch", "TECH_DISPATCH"),
        ("120363425277781798@g.us", "Tarek columbia closings", "TECH_CLOSINGS"),
        ("120363403736659314@g.us", "Osos NJ dispatch", "TECH_DISPATCH"),
        ("120363420900690444@g.us", "Ziyadah closings", "TECH_CLOSINGS"),
        ("120363421773052415@g.us", "Ziyadah Dispatch/Sunday", "TECH_DISPATCH"),
    ]:
        storage.upsert_group_registry_entry(c, {
            "group_id": gid, "exact_name": name, "role": role,
            "is_active": True, "confirmed_by_owner": False, "source": "name_heuristic",
        })
    # Ziyadah's dispatch group was already confirmed in a prior batch (real
    # scenario), the closing group is what THIS batch newly confirms.
    ziyadah_id = next(r[0] for r in c.execute("SELECT id FROM technicians WHERE name='Ziyadah'"))
    dispatch_row = storage.get_group_registry_entry(c, "120363421773052415@g.us")
    storage.upsert_group_registry_entry(c, {
        **dispatch_row, "technician_id": ziyadah_id, "confirmed_by_owner": True, "source": "owner_manual",
    })
    return c


def test_creates_osos_and_only_osos(mod, conn):
    result = mod.run(conn)
    assert [t["name"] for t in result["technicians_created"]] == ["Osos"]
    names = [t["name"] for t in storage.list_technicians(conn)]
    assert names.count("Osos") == 1
    assert names.count("Abozeid") == 1  # not duplicated
    assert names.count("Fares") == 1
    assert names.count("Tarek") == 1


def test_confirms_all_eight_groups_with_correct_roles_and_technicians(mod, conn):
    result = mod.run(conn)
    by_id = {c["group_id"]: c for c in result["confirmed"]}
    assert by_id["120363403321651687@g.us"]["role"] == "TECH_DISPATCH"
    assert by_id["120363422531151840@g.us"]["role"] == "TECH_CLOSINGS"
    assert by_id["120363425255488739@g.us"]["service_area"] == "Columbia"
    assert by_id["120363403736659314@g.us"]["service_area"] == "NJ"
    for row in result["confirmed"]:
        assert row["confirmed_by_owner"] is True


def test_tarek_permanent_area_is_never_changed(mod, conn):
    tarek_id = next(r[0] for r in conn.execute("SELECT id FROM technicians WHERE name='Tarek'"))
    mod.run(conn)
    tech = storage.get_technician(conn, tarek_id)
    assert tech["areas"] == ["Savannah"]  # unchanged, still permanent Savannah
    assert tech["dispatch_group"] is None  # never given Columbia destinations
    assert tech["closing_group"] is None


def test_tarek_columbia_note_explains_temporary_coverage(mod, conn):
    result = mod.run(conn)
    row = next(c for c in result["confirmed"] if c["group_id"] == "120363425255488739@g.us")
    assert "TEMPORARY" in row["notes"]
    assert "Savannah" in row["notes"]
    assert "Ismail" in row["notes"]


def test_osos_is_not_mapped_to_mohamed_osama(mod, conn):
    storage.add_technician(conn, "Mohamed Osama", areas=["NJ"])
    result = mod.run(conn)
    osos_id = result["technicians_created"][0]["id"]
    osos = storage.get_technician(conn, osos_id)
    assert osos["name"] == "Osos"
    mohamed = next(t for t in storage.list_technicians(conn) if t["name"] == "Mohamed Osama")
    assert mohamed["id"] != osos_id


def test_osos_closings_group_is_not_touched_by_this_batch(mod, conn):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "120363420633655712@g.us", "exact_name": "Osos nj Closings", "role": "UNMAPPED_WORK",
        "is_active": True, "confirmed_by_owner": False, "source": "name_heuristic",
    })
    mod.run(conn)
    row = storage.get_group_registry_entry(conn, "120363420633655712@g.us")
    assert row["confirmed_by_owner"] is False  # explicitly not auto-confirmed
    assert row["role"] == "UNMAPPED_WORK"


def test_abozeid_and_fares_destinations_are_backfilled(mod, conn):
    result = mod.run(conn)
    abozeid_id = next(r[0] for r in conn.execute("SELECT id FROM technicians WHERE name='Abozeid'"))
    fares_id = next(r[0] for r in conn.execute("SELECT id FROM technicians WHERE name='Fares'"))
    abozeid = storage.get_technician(conn, abozeid_id)
    fares = storage.get_technician(conn, fares_id)
    assert abozeid["dispatch_group"] == "Abozeid Arlington Dispatch"
    assert abozeid["closing_group"] == "Abozeid Arlington Closings"
    assert fares["dispatch_group"] == "Fares Virginia Beach Dispatch"
    assert fares["closing_group"] == "Fares Virginia Beach Closings"


def test_ziyadah_closing_destination_is_backfilled_alongside_existing_dispatch(mod, conn):
    result = mod.run(conn)
    ziyadah_id = next(r[0] for r in conn.execute("SELECT id FROM technicians WHERE name='Ziyadah'"))
    ziyadah = storage.get_technician(conn, ziyadah_id)
    assert ziyadah["dispatch_group"] == "Ziyadah Dispatch/Sunday"
    assert ziyadah["closing_group"] == "Ziyadah closings"


def test_never_touches_jobs(mod, conn):
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    mod.run(conn)
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert before == after == 0


def test_is_idempotent(mod, conn):
    first = mod.run(conn)
    second = mod.run(conn)
    assert len(first["technicians_created"]) == 1
    assert len(second["technicians_created"]) == 0
    names = [t["name"] for t in storage.list_technicians(conn)]
    assert names.count("Osos") == 1
