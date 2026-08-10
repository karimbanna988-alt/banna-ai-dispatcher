"""Tests for scripts/seed_whatsapp_group_registry.py -- run against a
throwaway tmp_path database, never the real one."""
import importlib.util
import sys
from pathlib import Path

import pytest

from dispatcher import storage

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "seed_whatsapp_group_registry.py"


@pytest.fixture(scope="module")
def seed_mod():
    spec = importlib.util.spec_from_file_location("seed_whatsapp_group_registry", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "seed_test.db")


def test_seed_is_idempotent(seed_mod, conn):
    first = seed_mod.seed(conn)
    second = seed_mod.seed(conn)
    assert first == second
    rows = storage.list_group_registry(conn)
    ids = [r["group_id"] for r in rows]
    assert len(ids) == len(set(ids))  # no duplicate rows from re-running


def test_no_group_id_appears_in_both_confirmed_and_unmapped_buckets(seed_mod):
    confirmed_ids = {c["group_id"] for c in seed_mod.CONFIRMED}
    unmapped = seed_mod._load_unmapped_candidates(confirmed_ids)
    unmapped_ids = {u["group_id"] for u in unmapped}
    assert confirmed_ids.isdisjoint(unmapped_ids)


def test_all_confirmed_entries_have_confirmed_by_owner_true_and_are_active(seed_mod, conn):
    seed_mod.seed(conn)
    for c in seed_mod.CONFIRMED:
        row = storage.get_group_registry_entry(conn, c["group_id"])
        assert row is not None
        assert row["confirmed_by_owner"] is True
        assert row["is_active"] is True


def test_all_unmapped_entries_are_not_confirmed_and_have_role_unmapped_work(seed_mod, conn):
    seed_mod.seed(conn)
    rows = storage.list_group_registry(conn)
    unmapped = [r for r in rows if r["source"] == "name_heuristic"]
    assert len(unmapped) > 0
    for r in unmapped:
        assert r["confirmed_by_owner"] is False
        assert r["role"] == "UNMAPPED_WORK"


def test_seed_never_includes_the_confirmed_non_work_or_unknown_buckets(seed_mod, conn):
    """No group whose offline classification was UNKNOWN or NON_WORK/IGNORE
    should ever be seeded -- only owner-confirmed facts and the
    mechanically work-shaped bucket."""
    seed_mod.seed(conn)
    rows = storage.list_group_registry(conn)
    assert all(r["role"] in ("COMPANY_JOB_INTAKE", "COMPANY_CLOSINGS", "TECH_DISPATCH", "TECH_CLOSINGS", "UNMAPPED_WORK") for r in rows)


def test_seed_produces_the_expected_totals(seed_mod, conn):
    result = seed_mod.seed(conn)
    assert result["confirmed"] == len(seed_mod.CONFIRMED)
    assert result["unmapped_work"] > 0
    rows = storage.list_group_registry(conn)
    assert len(rows) == result["confirmed"] + result["unmapped_work"]
