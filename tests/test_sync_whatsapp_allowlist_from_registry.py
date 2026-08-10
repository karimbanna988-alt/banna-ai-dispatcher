"""Tests for scripts/sync_whatsapp_allowlist_from_registry.py -- always
against a tmp_path allowlist file and tmp_path database, never the real
runtime allowlist."""
import importlib.util
import json
from pathlib import Path

import pytest

from dispatcher import storage

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sync_whatsapp_allowlist_from_registry.py"


@pytest.fixture(scope="module")
def sync_mod():
    spec = importlib.util.spec_from_file_location("sync_whatsapp_allowlist_from_registry", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "sync_test.db")


def test_sync_adds_active_registry_groups_to_a_fresh_allowlist(sync_mod, conn, tmp_path):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "Sebai Augusta Dispatch", "role": "TECH_DISPATCH",
        "is_active": True, "confirmed_by_owner": True,
    })
    storage.upsert_group_registry_entry(conn, {
        "group_id": "222@g.us", "exact_name": "Inactive Group", "role": "UNMAPPED_WORK",
        "is_active": False, "confirmed_by_owner": False,
    })
    allowlist_file = tmp_path / "allowlist.json"
    result = sync_mod.sync(conn, allowlist_file)
    assert result["total"] == 1
    assert result["added"] == 1
    data = json.loads(allowlist_file.read_text(encoding="utf-8"))
    assert [g["id"] for g in data["groups"]] == ["111@g.us"]


def test_sync_preserves_an_existing_entrys_addedat_timestamp(sync_mod, conn, tmp_path):
    allowlist_file = tmp_path / "allowlist.json"
    allowlist_file.write_text(json.dumps({
        "groups": [{"id": "111@g.us", "name": "Sebai Augusta Dispatch", "addedAt": "2026-08-01T13:47:12.528Z"}]
    }), encoding="utf-8")
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "Sebai Augusta Dispatch", "role": "TECH_DISPATCH",
        "is_active": True, "confirmed_by_owner": True,
    })
    result = sync_mod.sync(conn, allowlist_file)
    assert result["added"] == 0  # already present, untouched
    data = json.loads(allowlist_file.read_text(encoding="utf-8"))
    assert data["groups"][0]["addedAt"] == "2026-08-01T13:47:12.528Z"


def test_sync_never_removes_an_existing_entry_not_in_the_registry(sync_mod, conn, tmp_path):
    allowlist_file = tmp_path / "allowlist.json"
    allowlist_file.write_text(json.dumps({
        "groups": [{"id": "999@g.us", "name": "Some Other Entry", "addedAt": "2026-01-01T00:00:00Z"}]
    }), encoding="utf-8")
    result = sync_mod.sync(conn, allowlist_file)
    assert result["total"] == 1  # untouched, survives even with zero matching registry rows
    data = json.loads(allowlist_file.read_text(encoding="utf-8"))
    assert data["groups"][0]["id"] == "999@g.us"


def test_sync_is_idempotent(sync_mod, conn, tmp_path):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "111@g.us", "exact_name": "A", "role": "TECH_DISPATCH",
        "is_active": True, "confirmed_by_owner": True,
    })
    allowlist_file = tmp_path / "allowlist.json"
    sync_mod.sync(conn, allowlist_file)
    second = sync_mod.sync(conn, allowlist_file)
    assert second["added"] == 0
    data = json.loads(allowlist_file.read_text(encoding="utf-8"))
    assert len(data["groups"]) == 1


def test_sync_never_adds_an_inactive_or_unregistered_group(sync_mod, conn, tmp_path):
    storage.upsert_group_registry_entry(conn, {
        "group_id": "333@g.us", "exact_name": "Ignored Group", "role": "IGNORE",
        "is_active": False, "confirmed_by_owner": False,
    })
    allowlist_file = tmp_path / "allowlist.json"
    result = sync_mod.sync(conn, allowlist_file)
    assert result["total"] == 0
