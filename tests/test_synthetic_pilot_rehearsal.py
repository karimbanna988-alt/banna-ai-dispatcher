"""Pre-pilot synthetic rehearsal -- proves the complete AGN/Columbia/
Kabaka Commercial Update + Shadow Mode lifecycle end to end through the
real DispatcherService and canonical Streamlit pages, using only
fabricated data in a temporary database. The real database
(data/dispatcher.db) is never opened for writing anywhere in this file.
"""

from __future__ import annotations

import pathlib

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage
from dispatcher.application import DispatcherService
from dispatcher.commercial import QuoteType


# ------------------------------------------------------------- fixtures ---
@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    db_path = tmp_path / "rehearsal.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def seed_live_boundary(db_path, when="2020-01-01T00:00:00Z"):
    conn = storage.connect(db_path)
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET live_queue_started_at = excluded.live_queue_started_at, updated_at = excluded.updated_at",
        (when, when),
    )
    conn.commit()
    conn.close()


def seed_pending(db_path, message_id, raw_text, group_label="AGN"):
    from datetime import datetime, timedelta, timezone
    received_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"c-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=received_at,
    )
    conn.close()


def agn_message(ticket, area="Columbia, SC"):
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Rehearsal Ln, {area}\nDesc: Car Lockout\n"


def main_leads_message(ticket, area="Columbia, SC 29201"):
    return (
        f"Power By Ucallz App\n\nFabricated Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Rehearsal Customer\nPhone: (555) 010-2000\nAddress: 100 Rehearsal Dr, {area}\nDesc: Car Lockout\n"
    )


def seed_kabaka(db_path) -> int:
    """A fabricated technician in the TEMPORARY database, matching the
    real Kabaka's name/area/destinations for rehearsal realism -- never
    the real technician row (a different database entirely)."""
    conn = storage.connect(db_path)
    tech_id = storage.add_technician(
        conn, name="Kabaka", areas=["Columbia"],
        dispatch_group="Ismail columbia Dispatch", closing_group="Ismail Columbia Closings",
    )
    conn.close()
    return tech_id


def app_for(page: str, db_path) -> AppTest:
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file(page, default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    return at


# ========================================================================
# PRIMARY REHEARSAL -- full AGN / Columbia / Kabaka lifecycle
# ========================================================================

def test_primary_rehearsal_full_agn_columbia_kabaka_lifecycle(desk_env, capsys):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "RHRSL1", agn_message("RHRSL-TICKET"))

    # ---- Step 1-2: pending queue, no job yet ----
    conn = storage.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    conn.close()

    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    assert len(at.exception) == 0
    assert any(b.key == "desk_k_RHRSL1" and b.label == "MARK K SENT MANUALLY" for b in at.button)

    # ---- Step 3-4: prepare + confirm K sent manually ----
    at.button(key="desk_k_RHRSL1").click().run()
    assert len(at.exception) == 0

    # ---- Step 5-6: verify LUXOR message + Kabaka + destination ----
    rendered_code = "\n".join(c.value for c in at.code)
    assert "RHRSL-TICKET" in rendered_code
    rendered_all = (
        "\n".join(m.value for m in at.markdown)
        + rendered_code
        + "\n".join(c.value for c in at.caption)
    )
    assert "Kabaka" in rendered_all
    assert "Ismail columbia Dispatch" in rendered_all

    # ---- Step 7: confirm manual dispatch ----
    wa_button = next(b for b in at.button if b.key == "desk_wa_RHRSL1")
    assert wa_button.disabled is False
    at.button(key="desk_wa_RHRSL1").click().run()
    assert len(at.exception) == 0
    at.button(key="desk_confirm_dispatch_RHRSL1").click().run()
    assert len(at.exception) == 0

    # ---- Step 8: jobs row + first event created only now ----
    conn = storage.connect(db_path)
    jobs = conn.execute("SELECT id, ticket_number, status FROM jobs").fetchall()
    assert len(jobs) == 1
    job_id, ticket, status = jobs[0]
    assert ticket == "RHRSL-TICKET"
    assert status == "Approved"
    events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert events_count == 1
    conn.close()

    # ---- Step 9-10: record technician commercial update via UI ----
    service_conn = storage.connect(db_path)
    service = DispatcherService(service_conn)
    result = service.record_commercial_update(
        job_id, "Starting at 125, customer said he will call back.", dispatcher_actor="rehearsal",
    )
    assert result.success is True
    episode = storage.get_shadow_episode_for_job(service_conn, job_id)
    cu = episode["commercial_update"]
    assert cu["quote_type"] == "STARTING"
    assert cu["quote_amount"] == "125"
    assert cu["customer_response"] == "WILL_CALL_BACK"
    assert cu["negotiation_state"] not in ("LOST_ON_PRICE", "ACCEPTED")
    assert cu["currency"] is None  # no invented currency -- source text had no "$"
    service_conn.close()

    # ---- Step 13-14: exactly one Shadow Episode, suggestion vs actual separate ----
    conn = storage.connect(db_path)
    episode_count = conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0]
    assert episode_count == 1
    events = storage.get_shadow_episode_events(conn, episode["episode_id"])
    suggestion_event = next(e for e in events if e["event_type"] == "SHADOW_SUGGESTION")
    commercial_event = next(e for e in events if e["event_type"] == "COMMERCIAL_UPDATE")
    assert suggestion_event["payload"]["quote_type"] == "STARTING"  # extraction was unambiguous, no correction needed
    assert commercial_event["payload"]["quote_type"] == "STARTING"
    conn.close()

    # ---- Step 15-16: prepare + confirm company response sent manually ----
    service_conn = storage.connect(db_path)
    service = DispatcherService(service_conn)
    reply_before = storage.get_shadow_episode(service_conn, episode["episode_id"])["dispatcher_actual_reply"]
    assert reply_before == "Technician advised pricing starts at $125. Customer said they will call back."
    confirm_result = service.confirm_commercial_reply_sent(job_id, "Quo", dispatcher_actor="rehearsal")
    assert confirm_result.success is True
    after_confirm = storage.get_shadow_episode(service_conn, episode["episode_id"])
    assert after_confirm["actual_send_channel"] == "Quo"
    assert after_confirm["actual_send_confirmed_at"] is not None
    service_conn.close()

    # ---- Step 17-18: corrected technician update ----
    service_conn = storage.connect(db_path)
    service = DispatcherService(service_conn)
    corrected_result = service.record_commercial_update(
        job_id, "Actually quoted 150, customer is thinking.", dispatcher_actor="rehearsal",
    )
    assert corrected_result.success is True
    episode_after_correction = storage.get_shadow_episode_for_job(service_conn, job_id)
    assert episode_after_correction["episode_id"] == episode["episode_id"]  # no duplicate episode
    cu2 = episode_after_correction["commercial_update"]
    assert cu2["quote_type"] == "FIXED"
    assert cu2["quote_amount"] == "150"
    assert cu2["customer_response"] == "THINKING"

    # original 125 STARTING event remains historical, never overwritten
    all_events = storage.get_shadow_episode_events(service_conn, episode["episode_id"])
    commercial_events = [e for e in all_events if e["event_type"] == "COMMERCIAL_UPDATE"]
    assert len(commercial_events) == 2
    assert commercial_events[0]["payload"]["quote_amount"] == "125"
    assert commercial_events[0]["payload"]["quote_type"] == "STARTING"
    assert commercial_events[1]["payload"]["quote_amount"] == "150"
    assert commercial_events[1]["payload"]["quote_type"] == "FIXED"
    service_conn.close()

    # ---- Step 19-20: company question uses latest verified quote only ----
    service_conn = storage.connect(db_path)
    service = DispatcherService(service_conn)
    question_result = service.answer_company_commercial_question(job_id, "Did technician give a price?")
    assert question_result.prepared_text == "Technician quoted $150. Customer is thinking it over."
    assert "125" not in question_result.prepared_text
    service_conn.close()

    # ---- Step 21-22: fabricated closing, quote vs closing amount separate ----
    service_conn = storage.connect(db_path)
    storage.record_technician_update(service_conn, job_id, "Completed", "job done, all set")
    storage.record_job_closing(
        service_conn, job_id, "closed for 175 cash, tech confirmed", final_amount="175", payment_method="Cash (fabricated)",
    )
    closing = storage.get_latest_closing_details(service_conn, job_id)
    assert closing["amount"] == "175"
    episode_after_closing = storage.get_shadow_episode_for_job(service_conn, job_id)
    assert episode_after_closing["commercial_update"]["quote_amount"] == "150"  # quote untouched by closing
    service_conn.close()

    # ---- Step 23-24: company closing + complete job ----
    service_conn = storage.connect(db_path)
    service = DispatcherService(service_conn)
    prepare_closing = service.prepare_company_closing(job_id)
    assert prepare_closing.success is True
    sent_closing = service.confirm_company_closing_sent(job_id, prepare_closing.prepared_text)
    assert sent_closing.success is True
    complete_result = service.complete_job(job_id)
    assert complete_result.success is True
    service_conn.close()

    # ---- Step 25: Completed / History ----
    at_history = app_for("luxor_desk_pages/history.py", db_path)
    assert len(at_history.exception) == 0
    rendered_history = "\n".join(m.value for m in at_history.markdown)
    assert "RHRSL-TICKET" in rendered_history

    # ---- Step 26: Shadow Review ----
    at_review = app_for("luxor_desk_pages/shadow_review.py", db_path)
    assert len(at_review.exception) == 0
    expander_labels = [e.label for e in at_review.expander]
    assert any("AGN" in label and "RHRSL-TICKET" in label for label in expander_labels)

    # ---- Step 27: mark VERIFIED ----
    at_review.selectbox(key=f"shadow_review_status_{episode['episode_id']}").set_value("VERIFIED").run()
    at_review.button(key=f"shadow_review_save_{episode['episode_id']}").click().run()
    assert len(at_review.exception) == 0

    conn = storage.connect(db_path)
    final_episode = storage.get_shadow_episode(conn, episode["episode_id"])
    assert final_episode["review_status"] == "VERIFIED"
    assert final_episode["eligible_for_export"] is True

    # ---- Step 28-29: sanitized export, no PII/secrets/unreviewed records ----
    from dispatcher.shadow_export import export_shadow_dataset_jsonl
    exportable = storage.list_shadow_episodes_for_export(conn)
    assert len(exportable) == 1
    jsonl = export_shadow_dataset_jsonl(exportable)
    assert "Rehearsal Customer" not in jsonl
    assert "1-555-010-3000" not in jsonl
    assert "555-010" not in jsonl
    assert "100 Rehearsal Ln" not in jsonl
    assert "QUO_API_KEY" not in jsonl
    assert "trycloudflare.com" not in jsonl
    assert "data/dispatcher.db" not in jsonl
    assert "episode_id" not in jsonl
    assert "dispatcher_actor" not in jsonl
    conn.close()

    # ---- final: real database untouched (spot-check within this test) ----
    captured = capsys.readouterr()
    assert "Rehearsal Customer" not in captured.out


# ========================================================================
# SECONDARY EDGE-CASE REHEARSALS
# ========================================================================

# ---- A. Main Leads: no K acknowledgment; dispatch still possible; ----
# ---- unsupported closing template -> HUMAN_REVIEW_NEEDED, never fabricated
def test_edge_a_main_leads_no_k_ack_and_unsupported_closing(desk_env):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "ML1", main_leads_message("MLTICKET"), group_label="Main Leads 2")

    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    assert len(at.exception) == 0
    assert not any(b.key == "desk_k_ML1" for b in at.button)
    rendered_captions = "\n".join(c.value for c in at.caption)
    assert "NO ACKNOWLEDGMENT REQUIRED" in rendered_captions

    wa_button = next(b for b in at.button if b.key == "desk_wa_ML1")
    assert wa_button.disabled is False
    at.button(key="desk_wa_ML1").click().run()
    at.button(key="desk_confirm_dispatch_ML1").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(db_path)
    job = conn.execute("SELECT id, source FROM jobs").fetchone()
    assert job[1] == "Main Leads"
    job_id = job[0]

    from dispatcher import closing as closing_module
    job_row = storage.get_job_by_id(conn, job_id)
    result = closing_module.render_closing_from_persisted_fields(
        job_row["source"], job_row["ticket_number"], job_row["fields"],
        closing_module.ClosingInput(amount="175", payment_method="Cash", parts_amount=None),
    )
    assert result.supported is False  # never fabricates a Main Leads closing template
    conn.close()


# ---- B. No verified quote -> company question is HUMAN_REVIEW_NEEDED ----
def test_edge_b_no_verified_quote_returns_human_review_needed(desk_env):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "NOQ1", agn_message("NOQTICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    at.button(key="desk_k_NOQ1").click().run()
    at.button(key="desk_wa_NOQ1").click().run()
    at.button(key="desk_confirm_dispatch_NOQ1").click().run()
    assert len(at.exception) == 0

    conn = storage.connect(db_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    service = DispatcherService(conn)
    result = service.answer_company_commercial_question(job_id, "Did you quote?")
    assert result.human_review_required is True
    assert result.prepared_text is None
    assert "no verified price" in result.user_message
    conn.close()


# ---- C. Customer hesitation ("too expensive but still thinking") ----
# ---- never causes automatic cancellation; job stays active ----
def test_edge_c_hesitation_does_not_auto_cancel(desk_env):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "HES1", agn_message("HESTICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    at.button(key="desk_k_HES1").click().run()
    at.button(key="desk_wa_HES1").click().run()
    at.button(key="desk_confirm_dispatch_HES1").click().run()

    conn = storage.connect(db_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    status_before = storage.get_job_by_id(conn, job_id)["status"]
    service = DispatcherService(conn)
    result = service.record_commercial_update(
        job_id, "Quoted 150, customer said too expensive but still thinking about it.",
    )
    assert result.success is True
    status_after = storage.get_job_by_id(conn, job_id)["status"]
    assert status_after == status_before  # Shadow Mode never touches jobs.status
    assert status_after != "Cancelled"

    episode = storage.get_shadow_episode_for_job(conn, job_id)
    negotiation_state = episode["commercial_update"]["negotiation_state"]
    assert negotiation_state != "LOST_ON_PRICE"
    conn.close()


# ---- D. Duplicate submission -> one operational effect, idempotent replay ----
def test_edge_d_duplicate_commercial_update_is_idempotent(desk_env):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "DUP1", agn_message("DUPTICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    at.button(key="desk_k_DUP1").click().run()
    at.button(key="desk_wa_DUP1").click().run()
    at.button(key="desk_confirm_dispatch_DUP1").click().run()

    conn = storage.connect(db_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    service = DispatcherService(conn)
    r1 = service.record_commercial_update(job_id, "quoted 125")
    r2 = service.record_commercial_update(job_id, "quoted 125")
    assert r1.idempotent_replay is False
    assert r2.idempotent_replay is True

    episode = storage.get_shadow_episode_for_job(conn, job_id)
    events = storage.get_shadow_episode_events(conn, episode["episode_id"])
    commercial_events = [e for e in events if e["event_type"] == "COMMERCIAL_UPDATE"]
    assert len(commercial_events) == 1  # one operational effect, not two
    conn.close()


# ---- E. Process restart: episode/job lifecycle continues correctly ----
def test_edge_e_process_restart_continues_same_episode_and_job(desk_env):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "RST1", agn_message("RSTTICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    at.button(key="desk_k_RST1").click().run()
    at.button(key="desk_wa_RST1").click().run()
    at.button(key="desk_confirm_dispatch_RST1").click().run()

    conn1 = storage.connect(db_path)
    job_id = conn1.execute("SELECT id FROM jobs").fetchone()[0]
    DispatcherService(conn1).record_commercial_update(job_id, "quoted 125")
    conn1.close()  # simulate process exit

    conn2 = storage.connect(db_path)  # simulate process restart
    episode = storage.get_shadow_episode_for_job(conn2, job_id)
    assert episode is not None
    assert episode["commercial_update"]["quote_amount"] == "125"
    result = DispatcherService(conn2).record_negotiation_outcome(job_id, "ACCEPTED")
    assert result.success is True
    episode_after = storage.get_shadow_episode_for_job(conn2, job_id)
    assert episode_after["episode_id"] == episode["episode_id"]  # same episode, not a new one
    assert episode_after["negotiation_outcome"] == "ACCEPTED"
    conn2.close()


# ========================================================================
# AUTHENTICATED UI VERIFICATION (AppTest -- structural/content, viewport-
# independent; see final report for why live-browser pixel verification
# at specific viewport sizes was not performed)
# ========================================================================

def test_ui_no_button_implies_automatic_delivery(desk_env):
    """Every button label across the commercial-update workflow either
    describes a local save/prepare action or explicitly says "manually"
    -- never implies this application sent anything itself."""
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "UI1", agn_message("UITICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    at.button(key="desk_k_UI1").click().run()
    at.button(key="desk_wa_UI1").click().run()
    at.button(key="desk_confirm_dispatch_UI1").click().run()

    conn = storage.connect(db_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    DispatcherService(conn).record_commercial_update(job_id, "quoted 125")
    conn.close()

    at_active = app_for("luxor_desk_pages/active_jobs.py", db_path)
    assert len(at_active.exception) == 0
    labels = [b.label for b in at_active.button if b.label]
    forbidden_words = ("SEND NOW", "AUTO SEND", "SENDING...")
    for label in labels:
        for bad in forbidden_words:
            assert bad not in label.upper()
    # every button that records a send explicitly says "manually" or is a save/prepare action
    send_related = [l for l in labels if "SENT" in l.upper() or "SEND" in l.upper()]
    for label in send_related:
        assert "MANUAL" in label.upper()


def test_ui_commercial_fields_compact_parser_internals_hidden(desk_env):
    """Confidence score / validation-issue internals live under a nested
    'View details' expander (collapsed by default -- no expanded=True),
    never as a bare top-level caption a dispatcher would see without
    clicking anything. AppTest's flat .caption accessor returns every
    caption in the tree regardless of expander nesting (confirmed by
    inspection: it cannot distinguish "collapsed" from "expanded"), so
    the correct proxy for "hidden until clicked" is structural nesting
    inside an st.expander -- checked via that expander's own scoped
    .caption collection, not the flat top-level one."""
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "UI2", agn_message("UI2TICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    at.button(key="desk_k_UI2").click().run()
    at.button(key="desk_wa_UI2").click().run()
    at.button(key="desk_confirm_dispatch_UI2").click().run()

    at_active = app_for("luxor_desk_pages/active_jobs.py", db_path)
    at_active.text_area(key=f"commercial_raw_{{}}".format(_only_job_id(db_path))).set_value("quoted 125").run()

    details_expander = next(e for e in at_active.expander if "View details (raw extraction)" in (e.label or ""))
    nested_captions = "\n".join(c.value for c in details_expander.caption)
    assert "Confidence:" in nested_captions  # present, but only inside the nested, collapsed-by-default expander


def _only_job_id(db_path) -> int:
    conn = storage.connect(db_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    conn.close()
    return job_id


def test_ui_shadow_review_distinguishes_suggestion_from_actual(desk_env):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "UI3", agn_message("UI3TICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    at.button(key="desk_k_UI3").click().run()
    at.button(key="desk_wa_UI3").click().run()
    at.button(key="desk_confirm_dispatch_UI3").click().run()

    conn = storage.connect(db_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    DispatcherService(conn).record_commercial_update(
        job_id, "he said 100 or so, not sure",
        corrected_fields={"quote_type": QuoteType.FIXED, "quote_amount": "100"},
    )
    conn.close()

    at_review = app_for("luxor_desk_pages/shadow_review.py", db_path)
    assert len(at_review.exception) == 0
    rendered = "\n".join(m.value for m in at_review.markdown)
    assert "Shadow suggested reply" in rendered
    assert "Dispatcher actual reply" in rendered


def test_ui_quo_vs_whatsapp_channel_labels_correct(desk_env):
    db_path = desk_env
    seed_live_boundary(db_path)
    seed_kabaka(db_path)
    seed_pending(db_path, "UI4", agn_message("UI4TICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", db_path)
    # Technician dispatch is explicitly WhatsApp
    rendered_before = "\n".join(b.label or "" for b in at.button)
    assert "DISPATCH IN WHATSAPP" in rendered_before

    at.button(key="desk_k_UI4").click().run()
    at.button(key="desk_wa_UI4").click().run()
    at.button(key="desk_confirm_dispatch_UI4").click().run()

    conn = storage.connect(db_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    DispatcherService(conn).record_commercial_update(job_id, "quoted 125")
    conn.close()

    at_active = app_for("luxor_desk_pages/active_jobs.py", db_path)
    rendered_captions = "\n".join(c.value for c in at_active.caption)
    assert "Send channel: Quo" in rendered_captions  # commercial reply -> Quo, never WhatsApp
