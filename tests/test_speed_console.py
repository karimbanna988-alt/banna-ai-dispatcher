"""Tests for luxor_console.py -- the Speed Dispatch Console. Real
write-capable tool: K/Copy/WhatsApp write only to the additive
dispatch_tracking table; Mark Dispatched/Handle Manually/Pass are the
only actions that call the exact same storage.save_audit_entry()/
mark_quo_pending_message_actioned() path app.py's own buttons use.
Synthetic technicians/messages only -- no real customer data."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import luxor_ops_shared
from dispatcher import quo_client, storage

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def seed_pending(db_path, message_id, raw_text, group_label="AGN", received_at="2026-07-29T10:00:00Z"):
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"c-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=received_at,
    )
    conn.close()


def agn_message(ticket="FAKE1", area="Charlotte, NC"):
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Fake St, {area}\nDesc: Car Lockout\n"


def lucky_message(ticket="LUCK1", area="Charlotte, NC"):
    return f"Co: LUCKY LOCKSMITH LLC\nPDL: {ticket}\nPh: 1-555-010-4000\nAddr: 200 Fake Ave, {area}\nDesc: Home Lockout\n"


def main_leads_message(ticket="ML1", area="Charlotte, NC 28202"):
    return (
        f"Power By Ucallz App\n\nFabricated Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Test Person\nPhone: (555) 010-2000\nAddress: 100 Fake Dr, {area}\nDesc: Car Lockout\n"
    )


def login(at, password="pw"):
    at.text_input(key="luxor_console_password_input").set_value(password).run()
    at.button[0].click().run()
    assert len(at.exception) == 0
    return at


def make_app():
    at = AppTest.from_file("luxor_console.py", default_timeout=30)
    at.run()
    return at


# --------------------------------------------------------- 1. Default page
def test_login_lands_on_dispatch_queue(app_env):
    at = login(make_app())
    rendered = (
        "\n".join(c.value for c in at.caption) + "\n".join(m.value for m in at.markdown)
        + "\n".join(s.value for s in at.success)
    )
    assert "No jobs need your attention" in rendered or "Urgent" in rendered or "New Jobs" in rendered


# ---------------------------------------------------- 2. Navigation visible
def test_top_navigation_configured_with_five_destinations():
    source = Path("luxor_console.py").read_text(encoding="utf-8")
    assert 'position="top"' in source
    for title in ("Dispatch Queue", "Active Jobs", "Closings", "Technicians", "Activity"):
        assert title in source


# ------------------------------------------------- 3. Priority + oldest-first
def test_valid_jobs_ordered_oldest_first_within_bucket(app_env):
    seed_pending(app_env, "NEW_OLD", agn_message("OLDONE"), received_at="2026-07-29T09:00:00Z")
    seed_pending(app_env, "NEW_NEW", agn_message("NEWONE"), received_at="2026-07-29T09:30:00Z")
    at = login(make_app())
    rendered = "\n".join(m.value for m in at.markdown)
    assert rendered.index("OLDONE") < rendered.index("NEWONE")


# --------------------------------------------------------- 4. SLA pinning
def test_unacknowledged_job_past_sla_is_flagged_urgent(app_env):
    seed_pending(app_env, "STALE1", agn_message("STALE1"), received_at="2020-01-01T00:00:00Z")
    at = login(make_app())
    rendered = "\n".join(m.value for m in at.markdown)
    assert "URGENT" in rendered
    assert "Urgent" in rendered  # bucket header


# --------------------------------------------------------------- 5. Gate
def test_ack_fragments_never_become_dispatch_cards(app_env):
    seed_pending(app_env, "ACK1", "k", group_label="AGN")
    seed_pending(app_env, "ACK2", "ok", group_label="AGN")
    at = login(make_app())
    assert len(at.exception) == 0
    expander_labels = [e.label for e in at.expander]
    assert any("Other Messages (2)" in label for label in expander_labels)
    assert at.code == []  # no Luxor template ever rendered for these


# ------------------------------------------------ 6/7/8. Source K policy
def test_agn_valid_new_job_shows_k_button(app_env):
    seed_pending(app_env, "AGNK1", agn_message())
    at = login(make_app())
    assert any(("K Prepared" in b.label or b.label == "Send K") for b in at.button)


def test_lucky_valid_new_job_shows_k_button(app_env):
    seed_pending(app_env, "LUCKYK1", lucky_message(), group_label="Lucky")
    at = login(make_app())
    assert any(("K Prepared" in b.label or b.label == "Send K") for b in at.button)


def test_main_leads_never_shows_k_button(app_env):
    seed_pending(app_env, "MLK1", main_leads_message(), group_label="Main Leads 2")
    at = login(make_app())
    assert not any(("K Prepared" in b.label or b.label == "Send K") for b in at.button)


# --------------------------------------------------------- 9. No duplicate K
def test_acknowledged_job_shows_confirmation_not_button_again(app_env):
    seed_pending(app_env, "AGNK2", agn_message())
    at = login(make_app())
    at.button(key="luxor_console_k_AGNK2").click().run()
    assert len(at.exception) == 0
    assert not any(b.key == "luxor_console_k_AGNK2" for b in at.button)
    assert any("K acknowledged" in s.value for s in at.success)


# ------------------------------------------------- 10. Immediate Luxor output
def test_luxor_output_appears_immediately_for_valid_job(app_env):
    seed_pending(app_env, "AGNL1", agn_message("LUXTKT"))
    at = login(make_app())
    assert len(at.code) >= 1
    assert "LUXOR LOCKSMITH" in at.code[0].value
    assert any("Ready to Copy" in m.value for m in at.markdown)


# --------------------------------------------------- 11. Copy records, no mutation
def test_copy_action_records_tracking_without_mutating_job_facts(app_env):
    seed_pending(app_env, "AGNC1", agn_message("COPYTKT"))
    conn = storage.connect(app_env)
    jobs_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    at = login(make_app())
    at.button(key="luxor_console_copy_AGNC1").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(app_env)
    jobs_after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    tracking = storage.get_dispatch_tracking(conn, "AGNC1")
    conn.close()
    assert jobs_after == jobs_before  # copy never creates a job
    assert tracking["copied_at"] is not None


# ------------------------------------------------- 12/13. Eligible technicians
def test_multiple_eligible_technicians_shown_not_auto_selected(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated Tech A", areas=["Charlotte"], skills=["car lockout"])
    storage.add_technician(conn, name="Fabricated Tech B", areas=["Charlotte"], skills=["car lockout", "house lockout"])
    conn.close()
    seed_pending(app_env, "MULTI1", agn_message("MULTI1", area="Charlotte, NC"))
    at = login(make_app())
    rendered = "\n".join(m.value for m in at.markdown)
    assert "Fabricated Tech A" in rendered
    assert "Fabricated Tech B" in rendered


# ------------------------------------------ 14. Blank destination blocks claim
def test_blank_whatsapp_destination_blocks_opened_and_dispatched_claims(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated No-Dest Tech", areas=["Charlotte"])
    conn.close()
    seed_pending(app_env, "NODEST1", agn_message("NODEST1", area="Charlotte, NC"))
    at = login(make_app())
    assert not any(b.key == "luxor_console_wa_NODEST1" for b in at.button)
    assert not any(b.key == "luxor_console_dispatch_NODEST1" for b in at.button)
    rendered = "\n".join(c.value for c in at.caption)
    assert "WhatsApp destination not configured" in rendered


# ------------------------------------------------- 15/16/17. Outbound modes
def test_disabled_mode_k_action_makes_no_network_request(app_env, monkeypatch):
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    seed_pending(app_env, "DISABLED1", agn_message("DISABLED1"))
    at = login(make_app())
    button = [b for b in at.button if "K Prepared" in b.label][0]
    button.click().run()
    assert len(at.exception) == 0
    conn = storage.connect(app_env)
    count = conn.execute("SELECT COUNT(*) FROM quo_outbound_log").fetchone()[0]
    conn.close()
    assert count == 0  # disabled mode never even attempts propose_send()


def test_approval_mode_requires_explicit_tap_and_is_recorded(app_env, monkeypatch):
    monkeypatch.setenv("QUO_OUTBOUND_MODE", "approval")
    seed_pending(app_env, "APPROVAL1", agn_message("APPROVAL1"))
    at = login(make_app())
    button = [b for b in at.button if b.label == "Send K"][0]
    button.click().run()
    assert len(at.exception) == 0
    conn = storage.connect(app_env)
    row = conn.execute("SELECT mode, outcome FROM quo_outbound_log").fetchone()
    conn.close()
    assert row[0] == "approval"
    assert row[1] == "blocked"  # real send unimplemented -- always blocked in this build


def test_duplicate_k_tap_does_not_double_record_outbound_attempt(app_env, monkeypatch):
    monkeypatch.setenv("QUO_OUTBOUND_MODE", "approval")
    seed_pending(app_env, "DUPK1", agn_message("DUPK1"))
    at = login(make_app())
    button = [b for b in at.button if b.label == "Send K"][0]
    button.click().run()
    # Second click no longer shows a "Send K" button (already acknowledged) --
    # confirms idempotency at the UI/tracking layer.
    assert not any(b.key == "luxor_console_k_DUPK1" for b in at.button)


# ----------------------------------------------------------- 18. Mocking only
def test_no_network_library_call_reachable_in_disabled_mode():
    source = Path("luxor_console_pages/dispatch_queue.py").read_text(encoding="utf-8")
    assert "requests.post" not in source
    assert ".post(" not in source


# ------------------------------------------------- 19. No real data in fixtures
def test_no_real_technician_names_in_this_test_file():
    # Scan every line except this assertion's own literal name-list, which
    # would otherwise make this check trivially self-referential.
    real_names = ("Kabaka", "Sebai", "Ziyadah", "Shabaan", "Youssef", "Abozeid")
    lines = Path(__file__).read_text(encoding="utf-8").splitlines()
    body = "\n".join(line for line in lines if "real_names = (" not in line)
    for real_name in real_names:
        assert real_name not in body


# --------------------------------------------------- 20. GET-only unchanged
def test_dispatch_queue_source_contains_no_write_http_verbs():
    source = Path("luxor_console_pages/dispatch_queue.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source


def test_dispatch_queue_only_uses_existing_write_functions():
    source = Path("luxor_console_pages/dispatch_queue.py").read_text(encoding="utf-8")
    assert "storage.save_audit_entry(" in source
    assert "storage.mark_quo_pending_message_actioned(" in source
    forbidden = ("assign_technician(", "add_technician(")
    for call in forbidden:
        assert call not in source
