"""Tests for scripts/register_demo_chats.py.

What matters: it must not accept a 1:1 chat, must not clobber the real
allowlist, and must not register the demo groups into the production
registry.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "register_demo_chats.py"


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("register_demo_chats", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "ALLOWLIST", tmp_path / "allowlist.json")
    monkeypatch.setattr(m, "DEMO_CONFIG", tmp_path / "demo_chats.json")
    return m


def test_rejects_a_one_to_one_chat_id(mod):
    """1:1 chats end in @c.us and are structurally unsupported by the
    bridge -- accepting one would silently produce a demo that captures
    nothing."""
    with pytest.raises(SystemExit) as exc:
        mod.register("123@c.us", "456@g.us", "A", "B")
    assert "@g.us" in str(exc.value)


def test_rejects_the_same_group_for_both_roles(mod):
    with pytest.raises(SystemExit):
        mod.register("111@g.us", "111@g.us", "A", "B")


def test_registers_both_chats_and_writes_the_demo_config(mod):
    mod.register("111@g.us", "222@g.us", "DEMO Company", "DEMO Tech")
    cfg = json.loads(mod.DEMO_CONFIG.read_text(encoding="utf-8"))
    assert cfg["company_chat_id"] == "111@g.us"
    assert cfg["technician_chat_id"] == "222@g.us"


def test_preserves_existing_allowlist_entries(mod):
    """The allowlist is the live production capture gate -- registering a
    demo chat must never drop a real group."""
    mod.ALLOWLIST.write_text(json.dumps({"groups": [
        {"id": "real@g.us", "name": "Real Production Group", "addedAt": "2026-01-01T00:00:00Z"},
    ]}), encoding="utf-8")
    mod.register("111@g.us", "222@g.us", "DEMO Company", "DEMO Tech")
    ids = {g["id"] for g in json.loads(mod.ALLOWLIST.read_text(encoding="utf-8"))["groups"]}
    assert "real@g.us" in ids
    assert {"111@g.us", "222@g.us"} <= ids


def test_is_idempotent_and_does_not_duplicate_entries(mod):
    mod.register("111@g.us", "222@g.us", "DEMO Company", "DEMO Tech")
    mod.register("111@g.us", "222@g.us", "DEMO Company", "DEMO Tech")
    groups = json.loads(mod.ALLOWLIST.read_text(encoding="utf-8"))["groups"]
    assert len([g for g in groups if g["id"] == "111@g.us"]) == 1


def test_never_writes_to_the_production_group_registry(mod):
    """Demo groups must stay absent from whatsapp_group_registry so
    production keeps treating them as unmapped and refuses to interpret
    them.

    Asserted as "this script has no database access at all", which is
    both simpler and stronger than grepping for registry mentions: with
    no sqlite3 and no storage import, there is no route by which it
    could write any table. (The docstring names the registry on purpose,
    to record the isolation decision.)"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in ("import sqlite3", "from dispatcher import storage",
                      "upsert_group_registry_entry", "INSERT ", "UPDATE ", "conn."):
        assert forbidden not in source, f"demo registration must not touch the DB -- found {forbidden!r}"


def test_never_creates_a_job_or_technician(mod):
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in ("add_technician", "save_audit_entry", "confirm_whatsapp_dispatch"):
        assert forbidden not in source
