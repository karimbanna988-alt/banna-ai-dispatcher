"""Tests for luxor_ops_pages/routing_preview.py -- Technicians & Coverage.
Read-only: no assignment write, no Quo/WhatsApp send. Synthetic
technicians/messages only."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def make_page():
    at = AppTest.from_file("luxor_ops_pages/routing_preview.py", default_timeout=30)
    at.session_state["luxor_ops_authenticated"] = True
    at.run()
    assert len(at.exception) == 0
    return at


def seed_pending(db_path, message_id, raw_text, group_label="Main Leads 2", received_at="2026-07-29T10:00:00Z"):
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"c-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=received_at,
    )
    conn.close()


def main_leads_message(ticket, market_label, address, desc="Car Lockout"):
    return (
        f"Power By Ucallz App\n\n{market_label}\nTicket: {ticket}\nName: Test Person\n"
        f"Phone: (555) 010-2000\nAddress: {address}\nDesc: {desc}\n"
    )


def rendered_text(at):
    return "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption) + "\n".join(
        i.value for i in at.info
    )


def test_banner_states_no_automatic_assignment(app_env):
    at = make_page()
    assert any("no automatic assignment or message sending" in i.value for i in at.info)


def test_roster_grouped_by_canonical_area(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated Tech A", areas=["Faketon"])
    conn.close()
    at = make_page()
    assert "Faketon" in rendered_text(at)
    assert "Fabricated Tech A" in rendered_text(at)


def test_blank_destination_shows_not_configured_label(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated Tech A", areas=["Faketon"], dispatch_group=None)
    conn.close()
    at = make_page()
    assert "WhatsApp destination not configured" in rendered_text(at)


def test_atlantic_city_resolves_to_nj_never_atlanta(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated NJ Tech", areas=["NJ"])
    conn.close()
    seed_pending(
        app_env, "ML1",
        main_leads_message("FAKE9001", "Atlantic City Locksmith Pro", "Northfield, NJ 08225"),
    )
    at = make_page()
    text = rendered_text(at)
    assert "Canonical area: NJ" in text
    assert "Fabricated NJ Tech" in text
    assert "Atlanta" not in text


def test_generic_locksmith_pros_resolves_from_address_not_brand_text(app_env):
    """The generic "Locksmith Pros" label alone must never determine the
    area -- only the parsed address does."""
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated Chicago Tech", areas=["Chicago"])
    conn.close()
    seed_pending(app_env, "ML2", main_leads_message("FAKE9002", "Locksmith Pros", "100 Fake St, Chicago, IL 60601"))
    at = make_page()
    text = rendered_text(at)
    assert "Canonical area: Chicago" in text
    assert "Fabricated Chicago Tech" in text


def test_market_label_conflicting_with_address_is_needs_review(app_env):
    seed_pending(
        app_env, "ML3",
        main_leads_message("FAKE9003", "Atlantic City Locksmith Pro", "100 Fake St, Chicago, IL 60601"),
    )
    at = make_page()
    warnings = "\n".join(w.value for w in at.warning)
    assert "Needs Review" in warnings
    assert "conflicts" in warnings.lower()


def test_multiple_eligible_technicians_all_shown_not_auto_selected(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated Tech A", areas=["Savtown"])
    storage.add_technician(conn, name="Fabricated Tech B", areas=["Savtown"])
    storage.add_technician(conn, name="Fabricated Tech C", areas=["Savtown"])
    conn.close()
    seed_pending(app_env, "SAV1", main_leads_message("FAKE9004", "Locksmith Pros", "1 Fake Rd, Savtown, GA 31401"), group_label="AGN")
    # Note: area "Savtown" won't resolve against real rules/service_areas.yaml,
    # so this only exercises roster grouping/display, not live routing --
    # covered instead by test_technician_routing.py's Savannah-equivalent test
    # against the real matching engine. Kept here as a page-level smoke check.
    at = make_page()
    text = rendered_text(at)
    assert "Fabricated Tech A" in text
    assert "Fabricated Tech B" in text
    assert "Fabricated Tech C" in text


def test_ack_message_shows_no_luxor_preview(app_env):
    seed_pending(app_env, "ACK1", "k", group_label="AGN")
    at = make_page()
    text = rendered_text(at)
    assert "No Luxor preview" in text or "Not a complete job message" in text
    assert at.code == []


def test_valid_job_shows_luxor_preview_labeled_not_sent(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated NJ Tech", areas=["NJ"])
    conn.close()
    seed_pending(app_env, "ML5", main_leads_message("FAKE9005", "Atlantic City Locksmith Pro", "Northfield, NJ 08225"))
    at = make_page()
    assert len(at.code) >= 1
    assert any("Prepared preview" in c.value for c in at.caption)


def test_no_write_controls_on_this_page(app_env):
    conn = storage.connect(app_env)
    storage.add_technician(conn, name="Fabricated Tech A", areas=["Faketon"])
    conn.close()
    at = make_page()
    forbidden = {"Approve", "Assign", "Reject", "Send", "Mark Sent Manually", "Handle Manually"}
    button_labels = {b.label for b in at.button}
    assert button_labels.isdisjoint(forbidden)


def test_routing_preview_never_calls_write_functions():
    source = Path("luxor_ops_pages/routing_preview.py").read_text(encoding="utf-8")
    forbidden = ("save_audit_entry(", "add_technician(", "assign_technician(", "record_whatsapp_handoff(")
    for call in forbidden:
        assert call not in source


def test_routing_preview_no_write_http_verbs():
    source = Path("luxor_ops_pages/routing_preview.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source
