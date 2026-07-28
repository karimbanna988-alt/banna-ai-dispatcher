"""Tests for luxor_console.py -- the Operator Console. Unlike the
read-only demo/CRM candidates, this is a real write-capable tool: its
Acknowledge/Handle Manually/Reject actions call the exact same
storage.save_audit_entry()/mark_quo_pending_message_actioned() app.py's
own buttons already use. Tests here verify those writes happen correctly
AND that the job-transformation gate still blocks the Luxor formatter for
content-free messages exactly as it does in the read-only candidates."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import luxor_ops_shared
from dispatcher import storage
from dispatcher.parser import load_vehicle_rules

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = storage.connect(path)
    conn.close()
    return path


@pytest.fixture()
def app_env(db_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "")
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def seed_pending_message(db_path, message_id, raw_text, group_label="AGN", received_at="2026-07-29T10:00:00Z"):
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"conv-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=received_at,
    )
    conn.close()


def login(monkeypatch, password="pw"):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", password)
    at = AppTest.from_file("luxor_console.py", default_timeout=30)
    at.run()
    at.text_input(key="luxor_console_password_input").set_value(password).run()
    at.button[0].click().run()
    assert len(at.exception) == 0
    return at


# -------------------------------------------------------------- Auth gate
def test_no_password_denies_access(app_env):
    at = AppTest.from_file("luxor_console.py", default_timeout=30)
    at.run()
    assert len(at.exception) == 0
    assert any("LUXOR_DEMO_PASSWORD" in e.value for e in at.error)
    assert at.text_input == []


def test_correct_password_shows_console(app_env, monkeypatch):
    at = login(monkeypatch)
    assert any(h.value == "Next Up" or h.value == "Queue" for h in at.subheader)


def test_empty_queue_shows_no_attention_needed(app_env, monkeypatch):
    at = login(monkeypatch)
    assert any("No jobs need your attention" in s.value for s in at.success)


# ---------------------------------------------------- Acknowledge writes
def test_acknowledge_creates_job_and_event_and_marks_actioned(app_env, monkeypatch):
    seed_pending_message(app_env, "ACK_OK1", load("agn_clean.txt"))
    at = login(monkeypatch)
    assert len(at.exception) == 0

    at.button(key="luxor_console_acknowledge").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    jobs = conn.execute("SELECT ticket_number, status, source FROM jobs").fetchall()
    events = conn.execute("SELECT message_type, final_action FROM events").fetchall()
    pending_status = conn.execute(
        "SELECT status FROM quo_pending_messages WHERE message_id = 'ACK_OK1'"
    ).fetchone()[0]
    conn.close()

    assert jobs == [("FAKE1", "Approved", "AGN")]
    assert events == [("NEW", "Approve")]
    assert pending_status == "actioned"


def test_handle_manually_records_correct_final_action(app_env, monkeypatch):
    seed_pending_message(app_env, "HM1", load("agn_clean.txt"))
    at = login(monkeypatch)
    at.button(key="luxor_console_handle_manually").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    events = conn.execute("SELECT final_action FROM events").fetchall()
    jobs = conn.execute("SELECT status FROM jobs").fetchall()
    conn.close()
    assert events == [("Handle Manually",)]
    assert jobs == [("Handle Manually",)]


def test_reject_via_more_actions(app_env, monkeypatch):
    seed_pending_message(app_env, "REJ1", load("agn_clean.txt"))
    at = login(monkeypatch)
    at.expander[0].expanded = True
    at.run()
    at.button(key="luxor_console_reject").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    events = conn.execute("SELECT final_action FROM events").fetchall()
    conn.close()
    assert events == [("Reject",)]


# ------------------------------------------------- Gate: acks never format
def test_ack_message_never_calls_formatter(app_env, monkeypatch):
    seed_pending_message(app_env, "CONSOLE_ACK1", "k", group_label="AGN")

    calls = []
    monkeypatch.setattr(
        luxor_ops_shared.pipeline, "format_technician_message",
        lambda *a, **kw: calls.append(1) or "SHOULD NOT BE CALLED",
    )
    at = login(monkeypatch)
    assert len(at.exception) == 0
    assert calls == []
    # No Next Up card rendered for a pure ack -- only Other Messages.
    assert any("No jobs need your attention" in s.value for s in at.success)
    expander_labels = [e.label for e in at.expander]
    assert any("Other Messages (1)" in label for label in expander_labels)


# --------------------------------------------------------- Queue bucketing
def test_new_job_lands_in_new_valid_jobs_bucket(app_env, monkeypatch):
    seed_pending_message(app_env, "BUCKET_NEW1", load("agn_clean.txt"))
    at = login(monkeypatch)
    rendered = "\n".join(m.value for m in at.markdown)
    assert "New Valid Jobs" in rendered


def test_cancellation_lands_in_cancellations_bucket(app_env, monkeypatch):
    conn = storage.connect(app_env)
    vehicle_rules = load_vehicle_rules()
    from dispatcher.parser import extract_job
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    conn.close()

    seed_pending_message(app_env, "BUCKET_CANCEL1", "PDL: FAKE1\ncustomer wants to cancel the job", group_label="AGN")
    at = login(monkeypatch)
    rendered = "\n".join(m.value for m in at.markdown)
    assert "Cancellations" in rendered


# ------------------------------------------------------- Masking preserved
def test_extracted_fields_view_masks_phone(app_env, monkeypatch):
    seed_pending_message(app_env, "MASK1", load("agn_clean.txt"))
    at = login(monkeypatch)
    at.expander[0].expanded = True
    at.run()
    at.button(key="luxor_console_view_fields").click().run()
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "555-010-3000" not in rendered
    assert "[phone masked]" in rendered


# --------------------------------------------------- WhatsApp destination
def test_whatsapp_destination_unavailable_with_no_technician(app_env, monkeypatch):
    seed_pending_message(app_env, "WA1", load("agn_clean.txt"))
    at = login(monkeypatch)
    rendered = "\n".join(c.value for c in at.caption)
    assert "not available" in rendered
    assert not any(b.key == "luxor_console_mark_sent" for b in at.button)


# ------------------------------------------------- Structural safety net
def test_luxor_console_never_sends_to_quo():
    source = Path("luxor_console.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source, f"luxor_console.py must never call {forbidden!r}"


def test_luxor_console_only_uses_existing_write_functions():
    """Real writes happen -- but only via the exact same functions
    app.py's own Approve/Handle Manually/Reject buttons already use, plus
    the new (additive-only) whatsapp_handoffs recorder. No second
    persistence path."""
    source = Path("luxor_console.py").read_text(encoding="utf-8")
    assert "storage.save_audit_entry(" in source
    assert "storage.mark_quo_pending_message_actioned(" in source
    forbidden = ("assign_technician(", "add_technician(", "set_technician_active(")
    for call in forbidden:
        assert call not in source, f"luxor_console.py must not call {call!r} in this phase"
