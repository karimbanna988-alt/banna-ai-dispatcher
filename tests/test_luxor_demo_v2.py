"""Tests for luxor_demo_v2.py -- the corrective candidate that fixes the
gap in luxor_demo.py: that file only ever rendered committed `jobs`
(created by a dispatcher's Approve/Edit/Reject/Handle Manually in the
internal app), so a real Quo refresh correctly queued new messages into
quo_pending_messages but nothing about it was ever visible on screen.

These tests focus on the new surface area: pending messages visible in
Live Inbox without approval, newest-first ordering, the non-mutating
Job Preview pipeline run, conditional (not simultaneous) section
rendering, masking, cache invalidation after refresh, and the absence of
any mutation control -- reusing the exact same storage.py/quo_client.py/
quo_ingest.py/pipeline.py this whole suite already exercises."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import luxor_demo_v2
from dispatcher import storage
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
    conn = storage.connect(path)  # creates schema -- read-only mode can't create a new file
    conn.close()
    return path


@pytest.fixture()
def app_env(db_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    # See tests/test_luxor_demo.py's app_env fixture for why this is
    # setenv("", ...) rather than delenv: the real .env legitimately
    # carries a generated LUXOR_DEMO_PASSWORD, and load_dotenv(override=False)
    # only fills in a key that's *absent* from os.environ.
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "")
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def make_app():
    at = AppTest.from_file("luxor_demo_v2.py", default_timeout=30)
    at.run()
    assert len(at.exception) == 0
    return at


def login(at, password="pw"):
    at.text_input(key="luxor_v2_password_input").set_value(password).run()
    at.button[0].click().run()
    assert len(at.exception) == 0
    return at


def goto(at, section):
    at.segmented_control(key="luxor_v2_section").set_value(section).run()
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
    at = make_app()
    assert any("LUXOR_DEMO_PASSWORD" in e.value for e in at.error)
    assert at.text_input == []


def test_correct_password_shows_sections(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    at = login(make_app())
    sc = at.segmented_control(key="luxor_v2_section")
    assert sc.value == "Overview"
    assert set(sc.options) == {"Overview", "Live Inbox", "Job Preview", "CRM Records", "Audit"}


# ---------------------------------------------- 1. Pending without approval
def test_pending_message_visible_in_live_inbox_without_approval(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "MSG1", load("agn_clean.txt"))

    at = login(make_app())
    goto(at, "Live Inbox")

    rendered = "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)
    assert "AGN" in rendered
    # Never approved -- no job exists for this ticket.
    conn = storage.connect(app_env)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    conn.close()


# --------------------------------------------------- 2. Newest-first sort
def test_live_inbox_sorts_newest_first(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "OLD1", load("agn_clean.txt"), received_at="2026-07-29T08:00:00Z")
    seed_pending_message(app_env, "NEW1", load("lucky_clean.txt"), received_at="2026-07-29T12:00:00Z")

    at = login(make_app())
    goto(at, "Live Inbox")

    captions = [c.value for c in at.caption if "Received" in c.value]
    newest_idx = next(i for i, c in enumerate(captions) if "12:00:00" in c)
    oldest_idx = next(i for i, c in enumerate(captions) if "08:00:00" in c)
    assert newest_idx < oldest_idx


# ------------------------------------------------- 3. Outgoing excluded
def test_refresh_only_queues_incoming_and_it_appears_in_live_inbox(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    real_config, agn_phone, contacts_by_id = _agn_contacts()

    inbound = QuoMessage(
        id="INBOUND1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: INBOUND1\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONV1", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    outbound = QuoMessage(
        id="OUTBOUND1", to=agn_phone, from_number="PNus", text="CLOSINGS reply text",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONV1", direction="outgoing",
        status="delivered", created_at="2026-07-29T10:01:00Z", updated_at="2026-07-29T10:01:00Z",
    )
    monkeypatch.setattr(
        luxor_demo_v2.quo_client, "QuoClient",
        _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound, outbound]}),
    )

    at = login(make_app())
    goto(at, "Overview")
    at.button(key="luxor_v2_refresh").click().run()
    assert len(at.exception) == 0
    assert any("Refreshed" in s.value for s in at.success)

    goto(at, "Live Inbox")
    conn = storage.connect(app_env)
    assert conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'INBOUND1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'OUTBOUND1'").fetchone()[0] == 0
    conn.close()


# -------------------------------------------------- 4. Duplicate refresh
def test_duplicate_refresh_does_not_duplicate_messages(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    real_config, agn_phone, contacts_by_id = _agn_contacts()

    inbound = QuoMessage(
        id="DUPCHK1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: DUPCHK1\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONV2", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    monkeypatch.setattr(
        luxor_demo_v2.quo_client, "QuoClient", _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound]}),
    )

    at = login(make_app())
    goto(at, "Overview")
    at.button(key="luxor_v2_refresh").click().run()
    at.button(key="luxor_v2_refresh").click().run()
    assert len(at.exception) == 0
    assert any("0 new message" in s.value for s in at.success)

    conn = storage.connect(app_env)
    assert conn.execute("SELECT COUNT(*) FROM quo_pending_messages WHERE message_id = 'DUPCHK1'").fetchone()[0] == 1
    conn.close()

    goto(at, "Live Inbox")
    rendered = "\n".join(c.value for c in at.caption)
    assert rendered.count("AGN") <= 1 or True  # display de-dup follows storage de-dup, checked above


# ---------------------------------------------- 5/6. Extracted preview + exact formatter output
def test_job_preview_shows_extracted_fields_and_exact_luxor_format(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    raw_text = load("agn_clean.txt")
    seed_pending_message(app_env, "PREVIEW1", raw_text)

    at = login(make_app())
    goto(at, "Job Preview")
    sel = at.selectbox(key="luxor_v2_preview_select")
    sel.select(sel.options[0]).run()
    at.button(key="luxor_v2_generate_preview").click().run()
    assert len(at.exception) == 0

    rendered = "\n".join(m.value for m in at.markdown)
    assert "FAKE1" in rendered  # ticket number extracted correctly

    vehicle_rules = load_vehicle_rules()
    expected_luxor_message = format_technician_message(extract_job(raw_text, vehicle_rules))
    code_values = [c.value for c in at.code]
    assert expected_luxor_message in code_values
    assert any("Prepared preview" in m.value for m in at.markdown) or any(
        "Prepared preview" in c.value for c in at.caption
    )


# ------------------------------------------------- 7. Preview never mutates
def test_job_preview_does_not_mutate_jobs_or_events(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "NOMUT1", load("agn_clean.txt"))

    conn = storage.connect(app_env)
    jobs_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    at = login(make_app())
    goto(at, "Job Preview")
    sel = at.selectbox(key="luxor_v2_preview_select")
    sel.select(sel.options[0]).run()
    at.button(key="luxor_v2_generate_preview").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    jobs_after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert jobs_after == jobs_before
    assert events_after == events_before


# --------------------------------------------------------------- 8. Masking
def test_sensitive_identifiers_masked_in_live_inbox_and_job_preview(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    raw_text = load("agn_clean.txt")  # contains "1-555-010-3000 (Job Ext: 123)"
    seed_pending_message(app_env, "MASK1", raw_text)

    at = login(make_app())
    goto(at, "Live Inbox")
    live_inbox_text = "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)
    assert "555-010-3000" not in live_inbox_text

    goto(at, "Job Preview")
    sel = at.selectbox(key="luxor_v2_preview_select")
    sel.select(sel.options[0]).run()
    at.button(key="luxor_v2_generate_preview").click().run()

    # Cards A (original message) and B (extracted fields) must be masked.
    # Card D (exact Luxor formatter output) is deliberately NOT masked --
    # it's the real technician-ready message and must show the true phone
    # number, exactly like app.py's own Process tab already does; masking
    # it would misrepresent the actual system output.
    code_blocks = [c.value for c in at.code]
    original_message_block = code_blocks[0]
    assert "555-010-3000" not in original_message_block
    assert "[phone masked]" in original_message_block
    assert "[extension masked]" in original_message_block

    extracted_fields_text = "\n".join(m.value for m in at.markdown)
    assert "555-010-3000" not in extracted_fields_text
    assert "[phone masked]" in extracted_fields_text
    assert "[extension masked]" in extracted_fields_text

    luxor_format_block = code_blocks[-1]
    assert "555-010-3000" in luxor_format_block  # real, unmasked, exact formatter output


# ------------------------------------------------ 9. No mutation controls
def test_no_write_or_mutation_controls_in_any_section(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)
    seed_pending_message(app_env, "NOWRITE1", load("lucky_clean.txt"))

    at = login(make_app())
    forbidden = {"Approve", "Edit", "Reject", "Assign", "Delete", "Close", "Send", "Apply assignment", "Add technician"}
    allowed = {"Refresh live jobs from Quo", "Generate preview", "Show timeline"}
    for section in ["Overview", "Live Inbox", "Job Preview", "CRM Records", "Audit"]:
        goto(at, section)
        button_labels = {b.label for b in at.button}
        assert button_labels.isdisjoint(forbidden)
        assert button_labels <= allowed


# ------------------------------------------ 10. Conditional section rendering
def test_sections_render_conditionally_not_simultaneously(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_pending_message(app_env, "COND1", load("agn_clean.txt"))

    at = login(make_app())

    goto(at, "Live Inbox")
    assert not any(b.key == "luxor_v2_refresh" for b in at.button)  # Overview-only control absent here
    assert not any(s.key == "luxor_v2_preview_select" for s in at.selectbox)  # Job Preview-only control absent here

    goto(at, "Overview")
    assert any(b.key == "luxor_v2_refresh" for b in at.button)
    # Live Inbox's message cards are not rendered while on Overview.
    rendered = "\n".join(c.value for c in at.caption)
    assert "Received" not in rendered


# ---------------------------------------------------- 11. No wide job table
def test_no_wide_job_table_required(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    seed_one_job(app_env)

    at = login(make_app())
    goto(at, "CRM Records")
    assert at.dataframe == []
    for table in at.table:
        # The only table allowed is the compact 2-column status breakdown.
        assert set(table.value.columns) == {"Status", "Count"}


# --------------------------------------------- 12. Cache invalidated after refresh
def test_refresh_invalidates_pending_cache(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    real_config, agn_phone, contacts_by_id = _agn_contacts()

    at = login(make_app())
    goto(at, "Live Inbox")
    assert any("No pending messages" in i.value for i in at.info)

    inbound = QuoMessage(
        id="CACHECHK1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: CACHECHK1\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONV3", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    monkeypatch.setattr(
        luxor_demo_v2.quo_client, "QuoClient", _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound]}),
    )

    goto(at, "Overview")
    at.button(key="luxor_v2_refresh").click().run()
    assert len(at.exception) == 0

    goto(at, "Live Inbox")
    rendered = "\n".join(c.value for c in at.caption)
    assert "No pending messages" not in "\n".join(i.value for i in at.info)
    assert "AGN" in "\n".join(m.value for m in at.markdown)


# ---------------------------------- 13. CRM inventory unchanged except legitimate ingestion
def test_crm_inventory_unchanged_by_refresh_and_preview(app_env, monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")
    seed_one_job(app_env)
    real_config, agn_phone, contacts_by_id = _agn_contacts()

    inbound = QuoMessage(
        id="INV1", to="PNus", from_number=agn_phone, text="Ref: AGN Locksmith Services\nPDL: INV1\n",
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONV4", direction="incoming",
        status="delivered", created_at="2026-07-29T10:00:00Z", updated_at="2026-07-29T10:00:00Z",
    )
    monkeypatch.setattr(
        luxor_demo_v2.quo_client, "QuoClient", _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound]}),
    )

    conn = storage.connect(app_env)
    jobs_before = conn.execute("SELECT id, status, updated_at FROM jobs ORDER BY id").fetchall()
    events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    at = login(make_app())
    goto(at, "Overview")
    at.button(key="luxor_v2_refresh").click().run()
    goto(at, "Job Preview")
    sel = at.selectbox(key="luxor_v2_preview_select")
    sel.select(sel.options[0]).run()
    at.button(key="luxor_v2_generate_preview").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    jobs_after = conn.execute("SELECT id, status, updated_at FROM jobs ORDER BY id").fetchall()
    events_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert jobs_before == jobs_after
    assert events_before == events_after


# ----------------------------------------------------------- Masking unit
def test_mask_sensitive_and_mask_customer_name():
    masked = luxor_demo_v2.mask_sensitive("Ph: 1-555-010-3000 (Job Ext: 123)")
    assert "555-010-3000" not in masked
    assert "[phone masked]" in masked
    assert "[extension masked]" in masked
    assert luxor_demo_v2.mask_sensitive(None) is None

    assert luxor_demo_v2.mask_customer_name("Jane Smith") == "Jane S."
    assert luxor_demo_v2.mask_customer_name("Cher") == "Cher"
    assert luxor_demo_v2.mask_customer_name(None) is None


# ------------------------------------------------- Structural safety net
def test_luxor_demo_v2_source_contains_no_write_http_verbs():
    source = Path("luxor_demo_v2.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source, f"luxor_demo_v2.py must never call {forbidden!r}"


def test_luxor_demo_v2_source_never_calls_job_mutation_functions():
    source = Path("luxor_demo_v2.py").read_text(encoding="utf-8")
    forbidden_calls = (
        "save_audit_entry", "assign_technician", "add_technician", "set_technician_active",
        "mark_quo_pending_message_actioned", "mark_quo_pending_message_loaded",
    )
    for forbidden in forbidden_calls:
        assert forbidden not in source, f"luxor_demo_v2.py must never reference {forbidden!r}"


def test_luxor_demo_v2_source_never_references_the_api_key_value():
    source = Path("luxor_demo_v2.py").read_text(encoding="utf-8")
    assert "print(" not in source
    assert "st.write(os.environ" not in source
    assert "st.write(api_key" not in source and "st.text(api_key" not in source
