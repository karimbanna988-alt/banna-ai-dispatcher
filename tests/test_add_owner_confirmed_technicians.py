"""Tests for scripts/add_owner_confirmed_technicians.py -- always
against a tmp_path database, never the real one."""
import importlib.util
from pathlib import Path

import pytest

from dispatcher import storage

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "add_owner_confirmed_technicians.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("add_owner_confirmed_technicians", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def conn(tmp_path):
    c = storage.connect(tmp_path / "test.db")
    for spec in [
        {"group_id": "120363407762864599@g.us", "exact_name": "Toto Greenville Dispatch", "role": "TECH_DISPATCH",
         "company": "Mike Jobs", "service_area": "Greenville", "technician_name_unconfirmed": "Toto",
         "is_active": True, "confirmed_by_owner": True, "source": "owner_manual"},
        {"group_id": "120363429123808793@g.us", "exact_name": "Toto Greenville Closings", "role": "TECH_CLOSINGS",
         "company": "Mike Jobs", "service_area": "Greenville", "technician_name_unconfirmed": "Toto",
         "is_active": True, "confirmed_by_owner": True, "source": "owner_manual"},
        {"group_id": "120363425260967940@g.us", "exact_name": "Joe Greenville Dispatch/Saturday", "role": "TECH_DISPATCH",
         "company": "Mike Jobs", "service_area": "Greenville", "technician_name_unconfirmed": "Joe",
         "is_active": True, "confirmed_by_owner": True, "source": "owner_manual"},
        {"group_id": "120363423478202519@g.us", "exact_name": "Joe Greenville Closings", "role": "TECH_CLOSINGS",
         "company": "Mike Jobs", "service_area": "Greenville", "technician_name_unconfirmed": "Joe",
         "is_active": True, "confirmed_by_owner": True, "source": "owner_manual"},
    ]:
        storage.upsert_group_registry_entry(c, spec)
    return c


def test_creates_both_technicians_with_only_confirmed_fields(mod, conn):
    result = mod.run(conn)
    assert {c["name"] for c in result["created"]} == {"Toto", "Joe"}
    for c in result["created"]:
        row = storage.get_technician(conn, c["id"])
        assert row["areas"] == ["Greenville"]
        assert row["skills"] == []
        assert row["vehicle_restrictions"] == []
        assert row["active"] is True


def test_sets_dispatch_and_closing_group_from_confirmed_registry_names(mod, conn):
    mod.run(conn)
    toto = next(t for t in [storage.get_technician(conn, i) for i in (1, 2)] if t and t["name"] == "Toto")
    assert toto["dispatch_group"] == "Toto Greenville Dispatch"
    assert toto["closing_group"] == "Toto Greenville Closings"


def test_updates_registry_rows_with_real_technician_id(mod, conn):
    mod.run(conn)
    toto_id = next(t["id"] for t in [storage.get_technician(conn, i) for i in (1, 2)] if t and t["name"] == "Toto")
    for gid in ("120363407762864599@g.us", "120363429123808793@g.us"):
        row = storage.get_group_registry_entry(conn, gid)
        assert row["technician_id"] == toto_id
        assert row["confirmed_by_owner"] is True  # unchanged, still owner-confirmed


def test_is_idempotent_never_creates_a_duplicate_technician(mod, conn):
    first = mod.run(conn)
    second = mod.run(conn)
    assert len(first["created"]) == 2
    assert len(second["created"]) == 0
    assert {t["name"] for t in second["already_existed"]} == {"Toto", "Joe"}
    all_names = [storage.get_technician(conn, i)["name"] for i in range(1, 10) if storage.get_technician(conn, i)]
    assert all_names.count("Toto") == 1
    assert all_names.count("Joe") == 1


def test_does_not_invent_skills_or_vehicle_restrictions(mod, conn):
    mod.run(conn)
    for i in (1, 2):
        row = storage.get_technician(conn, i)
        if row:
            assert row["skills"] == []
            assert row["vehicle_restrictions"] == []
