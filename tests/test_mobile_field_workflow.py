"""Focused tests for Phase 4.1 (Mobile Field Operations): the workflow
strip, Needs action/Waiting/All queue views, one-primary-action-at-a-time
enforcement, the Active Jobs `Next:` banner and completed-step
collapsing, the Closing page's split amount/payment-method checks, and
the plain "Range" commercial label. Every job seeded here goes through
the real NEW-job pipeline or the real DispatcherService -- never a
fabricated runtime record. No real network, no real .env, no real
database writes -- see tests/test_synthetic_pilot_rehearsal.py and
tests/test_active_closings_history_ui.py for the same fixture pattern.
"""

from __future__ import annotations

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage
from dispatcher.application import DispatcherService
from dispatcher.parser import extract_job, load_vehicle_rules


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    db_path = tmp_path / "mobile.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def agn_message(ticket="MOBILE1", area="Charlotte, NC"):
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Fake St, {area}\nDesc: Car Lockout\n"


def commit_job(db_path, vehicle_rules, raw: str) -> int:
    conn = storage.connect(db_path)
    job = extract_job(raw, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, classify_result, final_action="Approve")
    conn.close()
    return job_id


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


def seed_kabaka(db_path) -> int:
    conn = storage.connect(db_path)
    tech_id = storage.add_technician(
        conn, name="Kabaka", areas=["Charlotte"],
        dispatch_group="Kabaka Dispatch", closing_group="Kabaka Closings",
    )
    conn.close()
    return tech_id


def app_for(page: str, db_path) -> AppTest:
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file(page, default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    return at


# ------------------------------------------------- One primary action ----
def test_whatsapp_button_replaced_by_confirm_after_opened(desk_env, vehicle_rules):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "MOB1", agn_message("MOB1TICKET"))
    at = app_for("luxor_desk_pages/dispatch.py", desk_env)
    at.button(key="desk_k_MOB1").click().run()
    at.button(key="desk_k_confirm_MOB1").click().run()
    assert any(b.key == "desk_wa_MOB1" for b in at.button)

    at.button(key="desk_wa_MOB1").click().run()
    assert len(at.exception) == 0
    # Only one primary action at a time: "Copy job for technician" is
    # gone, replaced by "Confirm sent in WhatsApp" -- never both at once.
    assert not any(b.key == "desk_wa_MOB1" for b in at.button)
    assert any(b.key == "desk_confirm_dispatch_MOB1" and b.label == "Confirm sent in WhatsApp" for b in at.button)

    at.button(key="desk_confirm_dispatch_MOB1").click().run()
    at.run()  # settle: AppTest's st.rerun()-inside-a-handler needs one more pass here
    assert len(at.exception) == 0
    # The only live job just got dispatched -- it leaves the live queue
    # entirely (no other job to auto-select), so the empty state shows.
    rendered = "\n".join(i.value for i in at.info)
    assert "No new live jobs need attention." in rendered
    assert not any(b.key == "desk_confirm_dispatch_MOB1" for b in at.button)


# --------------------------------------------------------- Workflow strip
# New Jobs deliberately does NOT get the workflow strip -- see
# tests/test_new_jobs_action_screen.py and docs/operator_ui_blueprint.md's
# "New Jobs mobile action screen" note for why this page was rebuilt as a
# minimal action screen instead. Active Jobs/Closings/History are
# unaffected by that rebuild and still carry the strip.
@pytest.mark.parametrize(
    "page,stage",
    [
        ("luxor_desk_pages/active_jobs.py", "Active"),
        ("luxor_desk_pages/closings.py", "Closing"),
        ("luxor_desk_pages/history.py", "History"),
    ],
)
def test_workflow_strip_present_on_every_operational_page(desk_env, page, stage):
    at = app_for(page, desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "desk-workflow-strip" in rendered
    assert f'desk-strip-current">{stage}' in rendered
    # All four stage names always render, regardless of which page.
    for name in ("New", "Active", "Closing", "History"):
        assert name in rendered


# --------------------------------------------- Needs action / Waiting / All
# New Jobs no longer has this view concept -- see
# tests/test_new_jobs_action_screen.py for its replacement (the 60-minute
# live-window / "Older unresolved jobs" split).
def test_active_jobs_view_pills_present(desk_env, vehicle_rules):
    commit_job(desk_env, vehicle_rules, agn_message("MOB4"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    pills = at.pills(key="desk_active_view")
    assert any(o.startswith("Needs action (") for o in pills.options)
    assert any(o.startswith("Waiting (") for o in pills.options)
    assert any(o.startswith("All (") for o in pills.options)


def test_closings_view_pills_present(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB5"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    conn.close()
    at = app_for("luxor_desk_pages/closings.py", desk_env)
    pills = at.pills(key="desk_closings_view")
    assert any(o.startswith("Needs action (") for o in pills.options)
    assert any(o.startswith("Waiting (") for o in pills.options)


def test_active_job_stays_selected_after_moving_to_waiting_bucket(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB6"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.selectbox(key=f"active_update_type_{job_id}").set_value("Going").run()
    at.text_area(key=f"active_raw_update_{job_id}").set_value("omw now").run()
    at.button(key=f"active_save_update_{job_id}").click().run()
    assert len(at.exception) == 0
    # This job just moved to "Waiting" (a normal update was recorded);
    # the default view is "Needs action" -- the workspace must still show it.
    rendered = "\n".join(m.value for m in at.markdown)
    assert "✓ Technician update saved: Going" in rendered


# Note: New Jobs no longer has a "Next job" button -- see
# tests/test_new_jobs_action_screen.py for its replacement (automatic
# reselection of the newest live job once the current one is dispatched).
# Active Jobs/Closings keep their own "Next job" button, untouched.


# ------------------------------------------------- Active Jobs Next: banner
def test_next_banner_prompts_paste_when_no_update_yet(desk_env, vehicle_rules):
    commit_job(desk_env, vehicle_rules, agn_message("MOB8"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    rendered = "\n".join(m.value for m in at.markdown)
    assert "Next: Paste the technician&#x27;s WhatsApp update." in rendered or "Next: Paste the technician's WhatsApp update." in rendered


def test_next_banner_prompts_closing_once_completed(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB9"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    conn.close()
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    rendered = "\n".join(m.value for m in at.markdown)
    assert "Next: Record the technician's closing." in rendered or "Next: Record the technician&#x27;s closing." in rendered


# --------------------------------------------- Completed steps collapse --
def test_technician_update_form_collapsed_after_saved(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB10"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Going", "omw")
    conn.close()
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    assert len(at.exception) == 0
    expander = next(e for e in at.expander if "Paste technician update" in (e.label or ""))
    assert expander.proto.expanded is False
    rendered = "\n".join(m.value for m in at.markdown)
    assert "✓ Technician update saved: Going" in rendered


def test_technician_update_form_expanded_when_nothing_recorded(desk_env, vehicle_rules):
    commit_job(desk_env, vehicle_rules, agn_message("MOB11"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    expander = next(e for e in at.expander if "Paste technician update" in (e.label or ""))
    assert expander.proto.expanded is True


def test_company_response_collapses_to_checked_line_once_sent(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB12"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Going", "omw")
    storage.mark_company_update_prepared(conn, job_id, "Going")
    storage.mark_company_update_sent_manually(conn, job_id, "Going")
    conn.close()
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "✓ Company response marked as sent" in rendered
    assert not any(b.key == f"active_prepare_update_{job_id}" for b in at.button)
    assert not any(b.key == f"active_sent_update_{job_id}" for b in at.button)


# ------------------------------------------- Commercial plain-label wording
def test_commercial_range_label_is_plain_not_price_range(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB13"))
    at = app_for("luxor_desk_pages/active_jobs.py", desk_env)
    at.text_area(key=f"commercial_raw_{job_id}").set_value("quoted between 100 and 150").run()
    assert len(at.exception) == 0
    select = at.selectbox(key=f"commercial_quote_type_{job_id}")
    assert "Range" in select.options
    assert "Price range" not in select.options


# ------------------------------------------------------- Closing checks --
def test_closing_shows_separate_amount_and_payment_method_checks(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB14"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    conn.close()
    at = app_for("luxor_desk_pages/closings.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "✓ Final amount: 150" in rendered
    assert "✓ Payment method: CC" in rendered


def test_closing_send_block_collapses_once_sent_manually(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB15"))
    conn = storage.connect(desk_env)
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    storage.record_job_closing(conn, job_id, "Closed $150 CC", final_amount="150", payment_method="CC")
    storage.mark_company_closing_prepared(conn, job_id, "Closed\n150 CC")
    storage.mark_company_closing_sent_manually(conn, job_id, "Closed\n150 CC")
    conn.close()
    at = app_for("luxor_desk_pages/closings.py", desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "✓ Sent manually in Quo" in rendered
    assert not any(b.key == f"closing_prepare_{job_id}" for b in at.button)
    assert not any(b.key == f"closing_sent_{job_id}" for b in at.button)


# --------------------------------------------------- Queue bucket helpers -
def test_active_job_bucket_pure_function():
    import luxor_desk_shared as shared_desk

    assert shared_desk.active_job_bucket({"latest_technician_update": None}) == "needs_action"
    assert shared_desk.active_job_bucket({"latest_technician_update": {"update_type": "Custom"}}) == "needs_action"
    assert shared_desk.active_job_bucket({"latest_technician_update": {"update_type": "Completed"}}) == "needs_action"
    assert shared_desk.active_job_bucket({"latest_technician_update": {"update_type": "Going"}}) == "waiting"


def test_closing_job_bucket_pure_function():
    import luxor_desk_shared as shared_desk

    assert shared_desk.closing_job_bucket({"status": "In Progress"}) == "waiting"
    assert shared_desk.closing_job_bucket({"status": "Closed"}) == "needs_action"


# ------------------------------------------------------ Mobile CSS present
def test_mobile_tap_target_and_stacking_css_present():
    import luxor_desk_shared as shared_desk

    assert "min-height: 44px" in shared_desk.DESK_CSS
    assert "max-width: 480px" in shared_desk.DESK_CSS


# ---------------------------------------------------- No page-render writes
def test_rendering_mobile_pages_never_writes(desk_env, vehicle_rules):
    job_id = commit_job(desk_env, vehicle_rules, agn_message("MOB16"))
    conn = storage.connect(desk_env)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    for page in ("luxor_desk_pages/active_jobs.py", "luxor_desk_pages/closings.py", "luxor_desk_pages/history.py"):
        at = app_for(page, desk_env)
        assert len(at.exception) == 0

    conn = storage.connect(desk_env)
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert before == after


# ------------------------------------------------------------- Password --
def test_password_never_appears_in_source_or_docs():
    import pathlib
    needle = "1212" + "1211"  # split so this guard's own source never matches itself
    this_file = pathlib.Path(__file__).resolve()
    for path in pathlib.Path(".").rglob("*.py"):
        if ".venv" in path.parts or "node_modules" in path.parts or path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert needle not in text, f"password literal found in {path}"
    for path in pathlib.Path("docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert needle not in text, f"password literal found in {path}"
