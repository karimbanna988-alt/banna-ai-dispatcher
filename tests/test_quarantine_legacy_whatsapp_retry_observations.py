"""Tests for scripts/quarantine_legacy_whatsapp_retry_observations.py --
always against tmp_path copies, never the real .runtime/ files."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "quarantine_legacy_whatsapp_retry_observations.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("quarantine_legacy_whatsapp_retry_observations", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def valid_obs(**overrides):
    obs = {
        "messageId": "m1", "groupId": "g@g.us", "groupName": "G", "sender": "s",
        "timestamp": 1700000000, "text": "k", "extractedTicket": None, "quotedMessageId": None,
    }
    obs.update(overrides)
    return obs


def test_valid_observation_is_kept_not_quarantined(mod):
    assert mod.classify(valid_obs()) == "keep"


def test_empty_text_is_quarantined(mod):
    assert mod.classify(valid_obs(text="")) == "quarantine"


def test_missing_timestamp_is_quarantined(mod):
    obs = valid_obs()
    del obs["timestamp"]
    assert mod.classify(obs) == "quarantine"


def test_missing_group_name_is_quarantined(mod):
    assert mod.classify(valid_obs(groupName="")) == "quarantine"


def test_run_splits_queue_correctly_and_never_deletes_data(mod, tmp_path):
    retry_file = tmp_path / "pending-delivery.json"
    quarantine_file = tmp_path / "legacy-quarantine.json"
    retry_file.write_text(json.dumps({"queued": [
        valid_obs(messageId="ok1", text="k"),
        valid_obs(messageId="bad1", text=""),
        valid_obs(messageId="bad2", text=""),
    ]}), encoding="utf-8")

    result = mod.run(retry_file, quarantine_file)
    assert result == {"kept": 1, "quarantined": 2}

    remaining = json.loads(retry_file.read_text(encoding="utf-8"))
    assert [o["messageId"] for o in remaining["queued"]] == ["ok1"]

    quarantined = json.loads(quarantine_file.read_text(encoding="utf-8"))
    quarantined_ids = {o["messageId"] for o in quarantined["quarantined"]}
    assert quarantined_ids == {"bad1", "bad2"}
    for o in quarantined["quarantined"]:
        assert o["quarantine_reason"].startswith("LEGACY_REVIEW_REQUIRED")
        assert o["groupId"] == "g@g.us"  # original metadata preserved, not discarded


def test_run_is_idempotent_and_never_duplicates_quarantine_entries(mod, tmp_path):
    retry_file = tmp_path / "pending-delivery.json"
    quarantine_file = tmp_path / "legacy-quarantine.json"
    retry_file.write_text(json.dumps({"queued": [valid_obs(messageId="bad1", text="")]}), encoding="utf-8")

    mod.run(retry_file, quarantine_file)
    # Second run: retry file now empty, quarantine file already has bad1
    mod.run(retry_file, quarantine_file)

    quarantined = json.loads(quarantine_file.read_text(encoding="utf-8"))
    assert len(quarantined["quarantined"]) == 1


def test_run_on_missing_retry_file_is_a_safe_noop(mod, tmp_path):
    result = mod.run(tmp_path / "does-not-exist.json", tmp_path / "quarantine.json")
    assert result == {"kept": 0, "quarantined": 0}
