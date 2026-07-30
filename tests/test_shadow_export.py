"""Tests for src/dispatcher/shadow_export.py -- sanitized JSONL export.
Pure-function tests against fabricated episode dicts; no database
required."""

from __future__ import annotations

import json

from dispatcher.shadow_export import (
    _EXCLUDED_EPISODE_FIELDS,
    export_shadow_dataset_jsonl,
    sanitize_episode_for_export,
    sanitize_text,
)


def _fake_episode(**overrides) -> dict:
    base = {
        "episode_id": "abc123",
        "dispatcher_actor": "alex",
        "technician_id": 6,
        "job_id": 1,
        "source_message_id": "M1",
        "pending_message_id": "P1",
        "source": "AGN",
        "channel": "WHATSAPP_TECHNICIAN",
        "ticket_or_pdl": "T123",
        "shadow_suggested_reply": "Technician quoted $125.",
        "dispatcher_actual_reply": "Technician quoted $125.",
        "commercial_update": {"raw_technician_text": "quoted 125", "quote_type": "FIXED"},
        "review_status": "VERIFIED",
        "eligible_for_export": True,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_phone_number_redacted():
    assert "[phone redacted]" in sanitize_text("call at 555-123-4567")
    assert "555-123-4567" not in sanitize_text("call at 555-123-4567")


def test_phone_number_without_dashes_redacted():
    assert "[phone redacted]" in sanitize_text("call 5551234567")


def test_address_redacted():
    result = sanitize_text("service at 123 Main St, Columbia")
    assert "[address redacted]" in result
    assert "123 Main St" not in result


def test_blank_text_unchanged():
    assert sanitize_text("") == ""
    assert sanitize_text(None) is None


def test_excludes_identity_and_internal_id_fields():
    episode = _fake_episode()
    record = sanitize_episode_for_export(episode)
    for field in ("episode_id", "dispatcher_actor", "technician_id", "job_id", "source_message_id", "pending_message_id"):
        assert field not in record, f"{field} should never appear in an exported record"


def test_ticket_replaced_with_fingerprint_not_raw_value():
    episode = _fake_episode(ticket_or_pdl="T123")
    record = sanitize_episode_for_export(episode)
    assert "ticket_or_pdl" not in record
    assert record["ticket_or_pdl_fingerprint"] != "T123"
    assert len(record["ticket_or_pdl_fingerprint"]) == 16


def test_same_ticket_produces_same_fingerprint():
    r1 = sanitize_episode_for_export(_fake_episode(ticket_or_pdl="SAME"))
    r2 = sanitize_episode_for_export(_fake_episode(ticket_or_pdl="SAME"))
    assert r1["ticket_or_pdl_fingerprint"] == r2["ticket_or_pdl_fingerprint"]


def test_free_text_reply_fields_sanitized():
    episode = _fake_episode(dispatcher_actual_reply="Call customer at 555-123-4567 for quote $125.")
    record = sanitize_episode_for_export(episode)
    assert "555-123-4567" not in record["dispatcher_actual_reply"]
    assert "$125" in record["dispatcher_actual_reply"]


def test_commercial_update_raw_text_sanitized():
    episode = _fake_episode(commercial_update={"raw_technician_text": "quoted 125, call 555-123-4567", "quote_type": "FIXED"})
    record = sanitize_episode_for_export(episode)
    assert "555-123-4567" not in record["commercial_update"]["raw_technician_text"]


def test_commercial_update_none_stays_none():
    episode = _fake_episode(commercial_update=None)
    record = sanitize_episode_for_export(episode)
    assert record["commercial_update"] is None


def test_export_jsonl_one_line_per_episode():
    episodes = [_fake_episode(episode_id="e1"), _fake_episode(episode_id="e2")]
    jsonl = export_shadow_dataset_jsonl(episodes)
    lines = jsonl.strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert "episode_id" not in parsed


def test_export_jsonl_empty_list_produces_empty_string():
    assert export_shadow_dataset_jsonl([]) == ""


def test_export_never_receives_or_relies_on_review_status_filtering_itself():
    """This module is redaction only -- review-gating is
    storage.list_shadow_episodes_for_export()'s job. Confirmed here: an
    UNREVIEWED episode passed in directly is still sanitized (this
    module trusts its caller to have already filtered), so the real
    safety property is that callers always source episodes from
    list_shadow_episodes_for_export(), never from list_shadow_episodes()."""
    episode = _fake_episode(review_status="UNREVIEWED")
    record = sanitize_episode_for_export(episode)
    assert record["review_status"] == "UNREVIEWED"
