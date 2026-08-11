"""Tests for scripts/apply_owner_mapping_batch_2.py -- always against a
tmp_path database, never the real one."""
import importlib.util
from pathlib import Path

import pytest

from dispatcher import storage

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "apply_owner_mapping_batch_2.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("apply_owner_mapping_batch_2", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def conn(tmp_path):
    c = storage.connect(tmp_path / "test.db")
    storage.add_technician(c, "Ziyadah", areas=["Raleigh"])
    storage.add_technician(c, "Falcon", areas=["Vancouver"])
    storage.add_technician(c, "Kimo", areas=["Greenville"])
    for gid, name, role in [
        (mod_group_ids()[0], "Ziyadah Dispatch/Sunday", "TECH_DISPATCH"),
        (mod_group_ids()[1], "Vancouver Dispatch", "TECH_DISPATCH"),
        (mod_group_ids()[2], "Kimo Alabama Dispatch", "TECH_DISPATCH"),
        (mod_group_ids()[3], "Kimo Alabama Closings", "TECH_CLOSINGS"),
    ]:
        storage.upsert_group_registry_entry(c, {
            "group_id": gid, "exact_name": name, "role": role,
            "is_active": True, "confirmed_by_owner": False, "source": "name_heuristic",
        })
    return c


def mod_group_ids():
    return (
        "120363421773052415@g.us", "120363424128482715@g.us",
        "120363411941241893@g.us", "120363408595340686@g.us",
    )


def test_confirms_all_four_groups_with_real_technician_ids(mod, conn):
    result = mod.run(conn)
    ziyadah_id = result["ziyadah"]["technician_id"]
    falcon_id = result["vancouver"]["technician_id"]
    kimo_id = result["kimo_dispatch"]["technician_id"]
    assert result["ziyadah"]["confirmed_by_owner"] is True
    assert result["vancouver"]["confirmed_by_owner"] is True
    assert result["kimo_dispatch"]["confirmed_by_owner"] is True
    assert result["kimo_closing"]["confirmed_by_owner"] is True
    assert ziyadah_id != falcon_id != kimo_id


def test_confirmed_groups_carry_the_correct_role_not_left_as_unmapped_work(mod, conn):
    result = mod.run(conn)
    assert result["ziyadah"]["role"] == "TECH_DISPATCH"
    assert result["vancouver"]["role"] == "TECH_DISPATCH"
    assert result["kimo_dispatch"]["role"] == "TECH_DISPATCH"
    assert result["kimo_closing"]["role"] == "TECH_CLOSINGS"


def test_does_not_create_a_second_vancouver_or_kimo_technician(mod, conn):
    mod.run(conn)
    names = [t["name"] for t in storage.list_technicians(conn)]
    assert names.count("Falcon") == 1
    assert names.count("Kimo") == 1
    assert "Vancouver" not in names


def test_kimo_area_reassigned_from_greenville_to_alabama(mod, conn):
    result = mod.run(conn)
    kimo_id = result["kimo_dispatch"]["technician_id"]
    tech = storage.get_technician(conn, kimo_id)
    assert tech["areas"] == ["Alabama"]
    assert result["kimo_areas_update"]["previous_areas"] == ["Greenville"]


def test_kimo_destinations_set_to_the_confirmed_alabama_groups(mod, conn):
    result = mod.run(conn)
    kimo_id = result["kimo_dispatch"]["technician_id"]
    tech = storage.get_technician(conn, kimo_id)
    assert tech["dispatch_group"] == "Kimo Alabama Dispatch"
    assert tech["closing_group"] == "Kimo Alabama Closings"


def test_never_touches_jobs_or_events_count_except_the_audit_rows(mod, conn):
    before_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    mod.run(conn)
    after_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert before_jobs == after_jobs == 0


def test_is_idempotent(mod, conn):
    first = mod.run(conn)
    second = mod.run(conn)
    assert first["kimo_dispatch"]["technician_id"] == second["kimo_dispatch"]["technician_id"]
    kimo_id = first["kimo_dispatch"]["technician_id"]
    assert storage.get_technician(conn, kimo_id)["areas"] == ["Alabama"]
