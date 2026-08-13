"""Tests for scripts/link_confirmed_groups_to_technician_records.py --
always against a tmp_path database, never the real one.

The whole value of this script is that it refuses to guess, so most of
these tests are about what it must NOT link.
"""
import importlib.util
from pathlib import Path

import pytest

from dispatcher import storage

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "link_confirmed_groups_to_technician_records.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("link_confirmed_groups", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _reg(conn, group_id, name, role, **kw):
    storage.upsert_group_registry_entry(conn, {
        "group_id": group_id, "exact_name": name, "role": role,
        "is_active": True, "confirmed_by_owner": kw.pop("confirmed", True),
        "source": "owner_manual", **kw,
    })


@pytest.fixture()
def conn(tmp_path):
    c = storage.connect(tmp_path / "test.db")
    storage.add_technician(c, "Ashraf", areas=["Charlotte"],
                           dispatch_group="Ashraf Charlotte Dispatch/ Saturday",
                           closing_group="Ashraf Charlotte Closings")
    _reg(c, "closings@g.us", "Ashraf Charlotte Closings", "TECH_CLOSINGS",
         technician_name_unconfirmed="Ashraf")
    return c


def test_links_a_confirmed_group_whose_technician_record_points_back_at_it(mod, conn):
    result = mod.run(conn)
    assert len(result["linked"]) == 1
    linked = result["linked"][0]
    assert linked["technician_name"] == "Ashraf"
    assert linked["proved_via"] == "technicians.closing_group"
    row = storage.get_group_registry_entry(conn, "closings@g.us")
    assert row["technician_id"] is not None


def test_does_not_touch_a_group_no_technician_record_points_at(mod, conn):
    """The real excluded case: a COMPANY_JOB_INTAKE group naming a
    technician records a routing fact, not group ownership -- and no
    technician's dispatch/closing group points at it."""
    _reg(conn, "company@g.us", "Augusta - Adam", "COMPANY_JOB_INTAKE",
         technician_name_unconfirmed="Ashraf")
    mod.run(conn)
    row = storage.get_group_registry_entry(conn, "company@g.us")
    assert row["technician_id"] is None
    assert row["role"] == "COMPANY_JOB_INTAKE"


def test_does_not_touch_an_unconfirmed_group_even_if_everything_else_lines_up(mod, conn):
    storage.add_technician(conn, "Sebai", areas=["Augusta"], dispatch_group="Sebai Augusta Dispatch")
    _reg(conn, "unconf@g.us", "Sebai Augusta Dispatch", "TECH_DISPATCH",
         technician_name_unconfirmed="Sebai", confirmed=False)
    mod.run(conn)
    row = storage.get_group_registry_entry(conn, "unconf@g.us")
    assert row["technician_id"] is None


def test_does_not_link_when_the_named_technician_does_not_exist(mod, conn):
    _reg(conn, "ghost@g.us", "Ghost Dispatch", "TECH_DISPATCH",
         technician_name_unconfirmed="NoSuchPerson")
    mod.run(conn)
    assert storage.get_group_registry_entry(conn, "ghost@g.us")["technician_id"] is None


def test_does_not_link_when_the_group_name_only_resembles_the_technician(mod, conn):
    """Name similarity alone must never be enough -- the technician's own
    record has to point at this exact group name."""
    _reg(conn, "similar@g.us", "Ashraf Charlotte Dispatch", "TECH_DISPATCH",
         technician_name_unconfirmed="Ashraf")
    mod.run(conn)
    row = storage.get_group_registry_entry(conn, "similar@g.us")
    assert row["technician_id"] is None, (
        "Ashraf's record points at 'Ashraf Charlotte Dispatch/ Saturday', not this group -- "
        "the near-miss must stay unlinked for the owner to resolve"
    )


def test_preserves_role_confirmation_and_every_other_field(mod, conn):
    before = storage.get_group_registry_entry(conn, "closings@g.us")
    mod.run(conn)
    after = storage.get_group_registry_entry(conn, "closings@g.us")
    assert after["role"] == before["role"]
    assert after["confirmed_by_owner"] == before["confirmed_by_owner"]
    assert after["exact_name"] == before["exact_name"]
    assert after["source"] == before["source"]


def test_never_modifies_the_technician_record_itself(mod, conn):
    tech_id = next(r[0] for r in conn.execute("SELECT id FROM technicians WHERE name='Ashraf'"))
    before = storage.get_technician(conn, tech_id)
    mod.run(conn)
    after = storage.get_technician(conn, tech_id)
    assert after["dispatch_group"] == before["dispatch_group"]
    assert after["closing_group"] == before["closing_group"]
    assert after["areas"] == before["areas"]


def test_never_touches_jobs_or_events(mod, conn):
    before_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    before_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    mod.run(conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before_jobs
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before_events


def test_is_idempotent(mod, conn):
    first = mod.run(conn)
    second = mod.run(conn)
    assert len(first["linked"]) == 1
    assert len(second["linked"]) == 0  # already linked, nothing left to do


def test_find_deterministic_links_is_a_safe_dry_run(mod, conn):
    found = mod.find_deterministic_links(conn)
    assert len(found) == 1
    assert storage.get_group_registry_entry(conn, "closings@g.us")["technician_id"] is None
