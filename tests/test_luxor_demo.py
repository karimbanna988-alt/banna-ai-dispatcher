"""Tests for luxor_demo.py -- the temporary, password-protected, read-only
LIVE Luxor Dispatcher Preview. Reuses the exact same storage.py/quo_client.py/quo_ingest.py
this whole suite already exercises; these tests focus on the new surface
area luxor_demo.py adds: the password gate, masking, absence of any
mutation control, and that the refresh action only ever touches the
existing durable pending-queue bookkeeping, never jobs/events directly."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import luxor_demo
from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.quo_client import Contact, QuoMessage
from dispatcher.quo_ingest import load_dispatch_groups

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = storage.connect(path)  # creates schema -- read-only mode can't create a new file
    conn.close()
    return path


@pytest.fixture()
def app_env(db_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    # Keep the real QUO_API_KEY out of the test process entirely -- same
    # reasoning as test_app_integration.py's own `app` fixture.
    monkeypatch.setenv("QUO_API_KEY", "")
    # Set (not delete) to empty: the real .env now legitimately carries a
    # generated LUXOR_DEMO_PASSWORD for the live demo. python-dotenv's
    # load_dotenv() (called at the top of luxor_demo.py, override=False)
    # only fills in a key that is *absent* from os.environ -- an empty
    # string already counts as present, so this keeps tests isolated from
    # whatever real value happens to be in .env.
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "")
    st.cache_resource.clear()
    return db_path


def make_app(password=None):
    at = AppTest.from_file("luxor_demo.py", default_timeout=30)
    at.run()
    assert len(at.exception) == 0
    return at


def seed_one_job(db_path, ticket="FAKE1"):
    conn = storage.connect(db_path)
    vehicle_rules = load_vehicle_rules()
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    conn.close()
    return job_id


# -------------------------------------------------------------- Auth gate
def test_no_password_env_var_denies_access(app_env):
    at = make_app()
    assert any("LUXOR_DEMO_PASSWORD" in e.value for e in at.error)
    assert at.text_input == []  # no password field shown, no dashboard
    assert at.table == []


def test_incorrect_password_rejected(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "correct-horse-battery-staple")
    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("wrong-password").run()
    at.button[0].click().run()
    assert any("Incorrect password" in e.value for e in at.error)
    assert at.table == []  # still no dashboard content


def test_correct_password_shows_dashboard(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "correct-horse-battery-staple")
    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("correct-horse-battery-staple").run()
    at.button[0].click().run()
    assert any("Jobs" == h.value for h in at.subheader)
    assert len(at.table) >= 1  # status counts + jobs tables rendered


# ------------------------------------------------------- No mutation UI --
def test_no_mutation_controls_appear(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)
    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("pw").run()
    at.button[0].click().run()

    button_labels = {b.label for b in at.button}
    forbidden = {"Approve", "Edit", "Reject", "Assign", "Delete", "Close", "Send", "Apply assignment", "Add technician"}
    assert button_labels.isdisjoint(forbidden)
    assert button_labels == {"Refresh live jobs from Quo", "Show timeline"}


# --------------------------------------------------------------- Masking
def test_sensitive_identifiers_are_masked_in_timeline(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)  # agn_clean.txt contains "1-555-010-3000 (Job Ext: 123)"

    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("pw").run()
    at.button[0].click().run()

    select_box = at.selectbox(key="luxor_demo_job_select")
    select_box.select(select_box.options[0]).run()
    at.button(key="luxor_demo_show_timeline").click().run()

    rendered_text = "\n".join(m.value for m in at.markdown)
    assert "555-010-3000" not in rendered_text
    assert "[phone masked]" in rendered_text
    assert "123" not in rendered_text or "[extension masked]" in rendered_text
    assert "[extension masked]" in rendered_text


def test_masking_function_directly():
    """Unit-level check of the masking helper itself, independent of the
    full app render."""
    masked = luxor_demo.mask_sensitive("Ph: 1-555-010-3000 (Job Ext: 123)")
    assert "555-010-3000" not in masked
    assert "123" not in masked or "[extension masked]" in masked
    assert "[phone masked]" in masked
    assert "[extension masked]" in masked


def test_masking_function_handles_none_and_empty():
    assert luxor_demo.mask_sensitive(None) is None
    assert luxor_demo.mask_sensitive("") == ""


# -------------------------------------------------- Real jobs, unchanged
def test_existing_jobs_remain_unchanged_after_viewing(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)

    conn = storage.connect(app_env)
    before_jobs = conn.execute("SELECT id, status, updated_at FROM jobs ORDER BY id").fetchall()
    before_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("pw").run()
    at.button[0].click().run()
    assert any("1" in m.value for m in at.metric)  # Total jobs metric rendered

    conn = storage.connect(app_env)
    after_jobs = conn.execute("SELECT id, status, updated_at FROM jobs ORDER BY id").fetchall()
    after_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    assert before_jobs == after_jobs
    assert before_events == after_events


# ------------------------------------------------------- Live GET-only refresh
def _fake_quo_client_factory(contacts_by_id, messages_by_phone):
    class _Stub:
        def get_contact(self, contact_id):
            return contacts_by_id[contact_id]

        def iter_messages(self, *, phone_number_id, participants, **filters):
            key = participants[0] if participants else None
            return iter(messages_by_phone.get(key, []))

    def factory(*args, **kwargs):
        return _Stub()

    return factory


def test_refresh_button_never_touches_jobs_or_events(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    seed_one_job(app_env)

    real_config = load_dispatch_groups()
    agn_phone = "+15550109900"
    contacts_by_id = {
        g.quo_contact_id: Contact(
            id=g.quo_contact_id, first_name=g.label, last_name=None, company=None,
            phone_numbers=[agn_phone] if g.label == "AGN" else [],
        )
        for g in real_config.dispatch_groups
    }
    inbound = QuoMessage(
        id="LUXORDEMO1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: LUXORDEMO1\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONVLD1", direction="incoming",
        status="delivered", created_at="2026-07-28T10:00:00Z", updated_at="2026-07-28T10:00:00Z",
    )
    monkeypatch.setattr(
        luxor_demo.quo_client, "QuoClient", _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound]}),
    )

    conn = storage.connect(app_env)
    jobs_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("pw").run()
    at.button[0].click().run()
    at.button(key="luxor_demo_refresh").click().run()
    assert len(at.exception) == 0
    assert any("Refreshed" in s.value for s in at.success)

    conn = storage.connect(app_env)
    jobs_after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    pending_after = conn.execute(
        "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded')"
    ).fetchone()[0]
    conn.close()

    assert jobs_after == jobs_before  # refresh never creates/modifies a job
    assert events_after == events_before  # refresh never writes an audit event
    assert pending_after == 1  # the new message landed in the durable pending queue only


def test_refresh_is_idempotent_on_repeated_click(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")

    real_config = load_dispatch_groups()
    agn_phone = "+15550109900"
    contacts_by_id = {
        g.quo_contact_id: Contact(
            id=g.quo_contact_id, first_name=g.label, last_name=None, company=None,
            phone_numbers=[agn_phone] if g.label == "AGN" else [],
        )
        for g in real_config.dispatch_groups
    }
    inbound = QuoMessage(
        id="LUXORDEMO2", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: LUXORDEMO2\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONVLD2", direction="incoming",
        status="delivered", created_at="2026-07-28T10:00:00Z", updated_at="2026-07-28T10:00:00Z",
    )
    monkeypatch.setattr(
        luxor_demo.quo_client, "QuoClient", _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound]}),
    )

    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("pw").run()
    at.button[0].click().run()
    at.button(key="luxor_demo_refresh").click().run()
    at.button(key="luxor_demo_refresh").click().run()  # same stub, same message id -- click again
    assert len(at.exception) == 0
    assert any("0 new message" in s.value for s in at.success)  # second click dedupes to zero

    conn = storage.connect(app_env)
    pending_count = conn.execute(
        "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded')"
    ).fetchone()[0]
    conn.close()
    assert pending_count == 1  # not duplicated


def test_refresh_shows_safe_error_when_api_key_missing(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "")  # explicitly unset for this test

    at = make_app()
    at.text_input(key="luxor_demo_password_input").set_value("pw").run()
    at.button[0].click().run()
    at.button(key="luxor_demo_refresh").click().run()

    assert len(at.exception) == 0
    assert any("not configured" in e.value for e in at.error)
    # Never leaks anything about the missing key beyond the generic message.
    assert not any("QUO_API_KEY" in e.value for e in at.error)


# ------------------------------------------------- Structural safety net
def test_luxor_demo_source_contains_no_write_http_verbs():
    source = Path("luxor_demo.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source, f"luxor_demo.py must never call {forbidden!r}"


def test_luxor_demo_source_never_calls_job_mutation_functions():
    source = Path("luxor_demo.py").read_text(encoding="utf-8")
    forbidden_calls = (
        "save_audit_entry", "assign_technician", "add_technician", "set_technician_active",
        "mark_quo_pending_message_actioned", "mark_quo_pending_message_loaded",
    )
    for forbidden in forbidden_calls:
        assert forbidden not in source, f"luxor_demo.py must never reference {forbidden!r}"


def test_luxor_demo_source_never_references_the_api_key_value():
    """The env var *name* is fine to reference (that's how os.environ.get
    reads it) -- this only guards against the raw key ever being printed,
    logged, or embedded as a literal."""
    source = Path("luxor_demo.py").read_text(encoding="utf-8")
    assert "print(" not in source
    assert "st.write(os.environ" not in source
    assert "st.write(api_key" not in source and "st.text(api_key" not in source
