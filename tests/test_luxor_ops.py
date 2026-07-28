"""Tests for the luxor_ops.py / luxor_ops_pages/*.py Operations CRM
candidate -- the third and current Phase A review candidate (after
luxor_demo.py and luxor_demo_v2.py, both untouched and still serving on
their own ports). Reuses the exact same storage.py/quo_client.py/
quo_ingest.py/pipeline.py this whole suite already exercises.

Each page file is tested standalone via AppTest with
luxor_ops_authenticated pre-seeded in session_state -- AppTest.switch_page()
does not correctly propagate page identity to a subsequent .run() call in
the installed Streamlit version (confirmed by direct inspection of
streamlit.testing.v1.app_test.AppTest.switch_page /
ElementTree.run: only the very first script execution threads
`page_hash` through; every later .run() delegates to
ScriptRunner._run() with no page_hash parameter at all), so real
multi-page navigation cannot be exercised end-to-end through AppTest.
Instead: the main entry (luxor_ops.py) is tested for the auth gate and
the default page; every other page is tested standalone with
`luxor_ops_authenticated` pre-seeded, and the "Open Job" button's
`st.switch_page` call is monkeypatched to a no-op so the state-transfer
(session_state) it depends on can be verified independently of actual
page routing."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import luxor_ops_shared
from dispatcher import quo_client, storage
from dispatcher.formatter import format_technician_message
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.quo_client import Contact, QuoMessage
from dispatcher.quo_ingest import load_dispatch_groups

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
    # See tests/test_luxor_demo.py's app_env fixture for why this is
    # setenv("", ...) rather than delenv.
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "")
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def make_page(name: str):
    at = AppTest.from_file(f"luxor_ops_pages/{name}.py", default_timeout=30)
    at.session_state["luxor_ops_authenticated"] = True
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


def seed_lucky_job(db_path):
    conn = storage.connect(db_path)
    vehicle_rules = load_vehicle_rules()
    job = extract_job(load("lucky_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    conn.close()
    return job_id


def seed_pending_message(db_path, message_id, raw_text, group_label="AGN", received_at="2026-07-29T10:00:00Z"):
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"conv-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=received_at,
    )
    conn.close()


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


def _agn_contacts():
    real_config = load_dispatch_groups()
    agn_phone = "+15550109900"
    contacts_by_id = {
        g.quo_contact_id: Contact(
            id=g.quo_contact_id, first_name=g.label, last_name=None, company=None,
            phone_numbers=[agn_phone] if g.label == "AGN" else [],
        )
        for g in real_config.dispatch_groups
    }
    return real_config, agn_phone, contacts_by_id


# -------------------------------------------------------------- Auth gate
def test_no_password_env_var_denies_access(app_env):
    at = AppTest.from_file("luxor_ops.py", default_timeout=30)
    at.run()
    assert len(at.exception) == 0
    assert any("LUXOR_DEMO_PASSWORD" in e.value for e in at.error)
    assert at.text_input == []


def test_correct_password_shows_default_operations_page(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    at = AppTest.from_file("luxor_ops.py", default_timeout=30)
    at.run()
    at.text_input(key="luxor_ops_password_input").set_value("pw").run()
    at.button[0].click().run()
    assert len(at.exception) == 0
    assert any("Operational Streams" in h.value for h in at.subheader)


# ------------------------------------------------ 1/2. Four labeled streams
def test_exactly_four_streams_with_correct_labels():
    names = [s.display_name for s in luxor_ops_shared.STREAMS]
    assert len(names) == 4
    assert set(names) == {"AGN", "Lucky", "Main Leads", "Lucky — Closings & Replies"}


def test_operations_page_shows_four_stream_rows(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    at = make_page("operations")
    rendered = "\n".join(m.value for m in at.markdown)
    for name in ("AGN", "Lucky", "Main Leads", "Lucky — Closings & Replies"):
        assert name in rendered


# ------------------------------------------- 3. Pending vs CRM jobs distinct
def test_pending_message_not_treated_as_crm_job(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "SEP1", load("agn_clean.txt"))

    at = make_page("crm_jobs")
    rendered = "\n".join(m.value for m in at.markdown)
    assert "FAKE1" not in rendered  # agn_clean.txt's ticket -- not a committed job

    conn = storage.connect(app_env)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    conn.close()


# ------------------------------------------- 4/5/16. Ack messages never format
def test_ack_messages_never_call_formatter_and_land_in_other_messages(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "ACK1", "k", group_label="AGN")
    seed_pending_message(app_env, "ACK2", "ok", group_label="AGN")

    calls = []
    monkeypatch.setattr(
        luxor_ops_shared.pipeline, "format_technician_message",
        lambda *a, **kw: calls.append(1) or "SHOULD NOT BE CALLED",
    )

    at = make_page("company_inbox")
    sc = at.segmented_control(key="luxor_ops_selected_stream")
    sc.set_value("AGN").run()
    assert len(at.exception) == 0

    assert calls == []  # formatter never invoked for either ack message
    expander_labels = [e.label for e in at.expander]
    assert any("Other Messages (2)" in label for label in expander_labels)


def test_gate_failure_shows_exact_message_and_never_formats(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "ACK3", "k", group_label="AGN")

    calls = []
    monkeypatch.setattr(
        luxor_ops_shared.pipeline, "format_technician_message",
        lambda *a, **kw: calls.append(1) or "SHOULD NOT BE CALLED",
    )
    at = make_page("job_workspace")
    at.session_state["luxor_ops_selected_pending_message_id"] = "ACK3"
    at.run()
    assert len(at.exception) == 0
    assert calls == []
    assert any(luxor_ops_shared.GATE_FAILURE_MESSAGE in w.value for w in at.warning)


# ------------------------------------------- 6. Full valid-job transformation
def test_valid_new_job_shows_full_transformation(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    raw_text = load("agn_clean.txt")
    seed_pending_message(app_env, "VALID1", raw_text)

    at = make_page("job_workspace")
    at.session_state["luxor_ops_selected_pending_message_id"] = "VALID1"
    at.run()
    assert len(at.exception) == 0

    rendered = "\n".join(m.value for m in at.markdown)
    assert "Original source message" in rendered
    assert "Extracted canonical job" in rendered
    assert "Deterministic assessment" in rendered
    assert "Luxor technician-ready message" in rendered

    vehicle_rules = load_vehicle_rules()
    expected = format_technician_message(extract_job(raw_text, vehicle_rules))
    assert expected in [c.value for c in at.code]


# ------------------------------------------------- 7. Grouped by stream
def test_pending_messages_grouped_by_stream(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "AGNMSG", load("agn_clean.txt"), group_label="AGN")
    seed_pending_message(app_env, "LUCKYMSG", load("lucky_clean.txt"), group_label="Lucky")

    at = make_page("company_inbox")
    sc = at.segmented_control(key="luxor_ops_selected_stream")

    sc.set_value("AGN").run()
    agn_text = "\n".join(m.value for m in at.markdown)
    assert "FAKE1" in agn_text

    sc.set_value("Lucky").run()
    lucky_text = "\n".join(m.value for m in at.markdown)
    assert "FAKE1" not in lucky_text


# --------------------------------------------------- 8. CRM Jobs filtering
def test_crm_jobs_source_status_area_filtering(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)
    seed_lucky_job(app_env)

    at = make_page("crm_jobs")
    at.segmented_control(key="luxor_ops_crm_view").set_value("All").run()
    assert len(at.exception) == 0
    assert any(m.value == "FAKE1" for m in at.markdown)

    at.selectbox(key="luxor_ops_crm_source_filter").select("Lucky").run()
    assert not any(m.value == "FAKE1" for m in at.markdown)

    at.selectbox(key="luxor_ops_crm_source_filter").select("AGN").run()
    at.selectbox(key="luxor_ops_crm_status_filter").select("Approved").run()
    assert any(m.value == "FAKE1" for m in at.markdown)


# --------------------------------------------- 9/10. Human dates, no raw ISO
def test_no_raw_iso_timestamps_or_channel_codes_anywhere(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)
    seed_pending_message(app_env, "TSCHK1", load("lucky_clean.txt"), group_label="Lucky")

    import re
    iso_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    for page in ("operations", "company_inbox", "crm_jobs", "activity"):
        at = make_page(page)
        all_text = "\n".join(
            str(el.value) for el in list(at.markdown) + list(at.caption) + list(at.markdown)
        )
        assert not iso_pattern.search(all_text), f"{page} leaked a raw ISO timestamp"
        assert "shadow_mode_quo_live" not in all_text, f"{page} leaked an internal channel code"


def test_humanize_timestamp_never_raw_iso():
    result = luxor_ops_shared.humanize_timestamp("2026-07-28T20:41:55.839Z")
    assert "T" not in result
    assert result != ""


# ------------------------------------------------- 11. No wide table needed
def test_no_dataframe_used_anywhere(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)
    for page in ("operations", "company_inbox", "crm_jobs", "activity"):
        at = make_page(page)
        assert at.dataframe == []


# --------------------------------------------------- 12. No write controls
def test_no_mutation_controls_anywhere(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)
    seed_pending_message(app_env, "NOWRITE1", load("lucky_clean.txt"), group_label="Lucky")

    forbidden = {"Approve", "Edit", "Reject", "Assign", "Delete", "Close", "Send", "Apply assignment", "Add technician"}
    for page in ("operations", "company_inbox", "job_workspace", "crm_jobs", "activity"):
        at = make_page(page)
        button_labels = {b.label for b in at.button}
        assert button_labels.isdisjoint(forbidden)


# ----------------------------------------------------------- 13/14. Safety
def test_refresh_only_queues_incoming_never_touches_jobs_or_events(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    real_config, agn_phone, contacts_by_id = _agn_contacts()

    inbound = QuoMessage(
        id="OPSREFRESH1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: OPSREFRESH1\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONVOPS1", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    outbound = QuoMessage(
        id="OPSREFRESHOUT", to=agn_phone, from_number="PNus", text="reply",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONVOPS1", direction="outgoing",
        status="delivered", created_at="2026-07-29T10:01:00Z", updated_at="2026-07-29T10:01:00Z",
    )
    monkeypatch.setattr(
        quo_client, "QuoClient",
        _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound, outbound]}),
    )

    conn = storage.connect(app_env)
    jobs_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    at = make_page("operations")
    at.button(key="luxor_ops_refresh").click().run()
    assert len(at.exception) == 0
    assert any("Refreshed" in s.value for s in at.success)

    conn = storage.connect(app_env)
    jobs_after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'OPSREFRESH1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'OPSREFRESHOUT'").fetchone()[0] == 0
    conn.close()
    assert jobs_after == jobs_before
    assert events_after == events_before


# --------------------------------------------------------- 15. Verdict label
def test_handle_manually_displays_as_needs_review():
    assert luxor_ops_shared.verdict_label("HANDLE_MANUALLY") == "Needs Review"
    assert luxor_ops_shared.verdict_label("NEEDS_REVIEW") == "Needs Review"
    assert luxor_ops_shared.verdict_label("ACCEPT") == "Accept"
    assert luxor_ops_shared.verdict_label("PASS") == "Pass"


# ---------------------------------------------------------- Masking unit
def test_mask_sensitive_and_mask_customer_name():
    masked = luxor_ops_shared.mask_sensitive("Ph: 1-555-010-3000 (Job Ext: 123)")
    assert "555-010-3000" not in masked
    assert "[phone masked]" in masked
    assert "[extension masked]" in masked
    assert luxor_ops_shared.mask_customer_name("Jane Smith") == "Jane S."


# ------------------------------------------------- Structural safety net
def test_luxor_ops_source_contains_no_write_http_verbs():
    for path in ["luxor_ops.py", "luxor_ops_shared.py"] + [
        f"luxor_ops_pages/{name}.py" for name in
        ("operations", "company_inbox", "job_workspace", "crm_jobs", "activity")
    ]:
        source = Path(path).read_text(encoding="utf-8")
        for forbidden in (".post(", ".put(", ".delete(", ".patch("):
            assert forbidden not in source, f"{path} must never call {forbidden!r}"


def test_luxor_ops_source_never_calls_job_mutation_functions():
    forbidden_calls = (
        "save_audit_entry", "assign_technician", "add_technician", "set_technician_active",
        "mark_quo_pending_message_actioned", "mark_quo_pending_message_loaded",
    )
    for path in ["luxor_ops.py", "luxor_ops_shared.py"] + [
        f"luxor_ops_pages/{name}.py" for name in
        ("operations", "company_inbox", "job_workspace", "crm_jobs", "activity")
    ]:
        source = Path(path).read_text(encoding="utf-8")
        for forbidden in forbidden_calls:
            assert forbidden not in source, f"{path} must never reference {forbidden!r}"


def test_luxor_ops_source_never_references_the_api_key_value():
    for path in ["luxor_ops.py", "luxor_ops_pages/operations.py"]:
        source = Path(path).read_text(encoding="utf-8")
        assert "print(" not in source
        assert "st.write(os.environ" not in source
        assert "st.write(api_key" not in source and "st.text(api_key" not in source
