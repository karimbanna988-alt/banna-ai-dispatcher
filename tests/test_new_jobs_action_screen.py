"""Focused tests for the New Jobs mobile action screen rebuild
(luxor_desk_pages/dispatch.py + the New-Jobs-only rendering functions in
luxor_desk_shared.py: render_compact_new_jobs_header, render_queue_row,
render_job_workspace, _render_job_details). This is a minimal dispatch
action screen, not a CRM dashboard -- see docs/operator_ui_blueprint.md's
"New Jobs mobile action screen" note for the full rationale.

Every job seeded here goes through the real live-dispatch-queue pipeline
(quo_pending_messages + dispatch_tracking) -- never a fabricated runtime
record. No real network, no real .env, no real database writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    db_path = tmp_path / "new_jobs.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    monkeypatch.setenv("QUO_API_KEY", "")
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
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


def agn_message(ticket, area="Charlotte, NC"):
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Fake St, {area}\nDesc: Car Lockout\n"


def main_leads_message(ticket, area="Charlotte, NC 28202"):
    return (
        f"Power By Ucallz App\n\nFabricated Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Fake Customer\nPhone: (555) 010-2000\nAddress: 100 Fake Dr, {area}\nDesc: Car Lockout\n"
    )


def seed_pending(db_path, message_id, raw_text, group_label="AGN", minutes_ago=None):
    """minutes_ago=None means "just now" (a few minutes in the future,
    matching this suite's existing seed-fixture convention for clock-skew
    tolerance). minutes_ago=N seeds a message received N minutes in the
    past -- used to exercise the 60-minute live-window cutoff."""
    if minutes_ago is None:
        received_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    else:
        received_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"c-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=received_at,
    )
    conn.close()


def seed_kabaka(db_path, dispatch_group="Kabaka Dispatch") -> int:
    conn = storage.connect(db_path)
    tech_id = storage.add_technician(
        conn, name="Kabaka", areas=["Charlotte"],
        dispatch_group=dispatch_group, closing_group="Kabaka Closings",
    )
    conn.close()
    return tech_id


def app_for(page: str, db_path) -> AppTest:
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file(page, default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    return at


def markdown_outside_any_expander(at) -> str:
    """AppTest's top-level .markdown accessor is flat -- it includes
    content from collapsed expanders too (confirmed by direct inspection
    this session), so it cannot be used on its own to prove something is
    hidden until expanded. This returns only the markdown values that
    aren't owned by any expander's own scoped .markdown collection --
    the correct proxy for "visible in the primary view"."""
    in_expander_ids = set()
    for e in at.expander:
        for m in e.markdown:
            in_expander_ids.add(id(m))
    return "\n".join(m.value for m in at.markdown if id(m) not in in_expander_ids)


PAGE = "luxor_desk_pages/dispatch.py"


# --------------------------------------------------------- Compact header
def test_compact_header_no_full_width_sync_or_logout(desk_env):
    """Sync and Log out are small, content-width controls (Log out lives
    inside a popover menu) -- neither uses width="stretch"."""
    seed_live_boundary(desk_env)
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    sync_btn = next(b for b in at.button if b.key == "desk_compact_sync")
    assert sync_btn.label == "Sync"
    assert not any(b.key in ("desk_header_sync", "desk_header_logout") for b in at.button)  # old full-width header not used here


def test_no_workflow_strip_or_stage_pills_on_new_jobs(desk_env):
    seed_live_boundary(desk_env)
    at = app_for(PAGE, desk_env)
    rendered = "\n".join(m.value for m in at.markdown)
    assert "desk-workflow-strip" not in rendered
    assert "desk-strip-current" not in rendered


def test_no_view_pills_or_next_job_button_on_new_jobs(desk_env):
    seed_live_boundary(desk_env)
    at = app_for(PAGE, desk_env)
    assert not any(getattr(p, "key", None) == "desk_dispatch_view" for p in at.pills)
    assert not any(b.key == "desk_dispatch_next" for b in at.button)


# --------------------------------------------------------- Live window ---
def test_live_job_appears_in_default_queue(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "FRESH1", agn_message("FRESHTICKET"))
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "Live queue" in rendered
    assert any(b.key == "desk_queue_row_FRESH1" for b in at.button)


def test_old_job_appears_only_under_older_unresolved(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "OLD1", agn_message("OLDTICKET"), minutes_ago=90)
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    assert not any(b.key == "desk_queue_row_OLD1" for b in at.button if _row_visible_by_default(at, b))
    expander_labels = [e.label for e in at.expander]
    assert any(label and label.startswith("Older unresolved jobs (1)") for label in expander_labels)
    older_expander = next(e for e in at.expander if (e.label or "").startswith("Older unresolved jobs"))
    nested_buttons = [b.key for b in older_expander.button]
    assert "desk_queue_row_OLD1" in nested_buttons


def _row_visible_by_default(at, button):
    """A queue row is "visible by default" if it isn't nested inside a
    collapsed expander -- AppTest's flat .button accessor doesn't
    distinguish this on its own, so this checks it isn't present in any
    expander's own scoped .button collection."""
    for e in at.expander:
        if any(b.key == button.key for b in e.button):
            return False
    return True


def test_old_job_never_auto_selected_while_live_job_exists(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "OLD2", agn_message("OLDTICKET2"), minutes_ago=90)
    seed_pending(desk_env, "FRESH2", agn_message("FRESHTICKET2"))
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    # The selected workspace shows the fresh ticket, never the old one.
    rendered = "\n".join(m.value for m in at.markdown)
    assert "FRESHTICKET2" in rendered
    assert "OLDTICKET2" not in rendered


def test_newest_live_job_selected_first(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "OLDER_LIVE", agn_message("OLDERLIVETICKET"), minutes_ago=40)
    seed_pending(desk_env, "NEWER_LIVE", agn_message("NEWERLIVETICKET"), minutes_ago=2)
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "NEWERLIVETICKET" in rendered
    assert "OLDERLIVETICKET" not in rendered  # not the selected workspace -- only in the compact queue row


def test_empty_live_window_shows_exact_message_with_older_backlog_separate(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "ONLYOLD", agn_message("ONLYOLDTICKET"), minutes_ago=120)
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    rendered_info = "\n".join(i.value for i in at.info)
    assert "No new live jobs need attention." in rendered_info
    expander_labels = [e.label for e in at.expander]
    assert any(label and label.startswith("Older unresolved jobs (1)") for label in expander_labels)


def test_selection_survives_aging_past_the_live_window_mid_sequence(desk_env):
    """A job already selected and mid-acknowledgment must not disappear
    from the workspace just because it crosses the 60-minute cutoff --
    selection is resolved against the full filtered set, not just the
    live window. Simulated here by seeding the job already 65 minutes
    old but leaving it as the only job (so it's still reachable as the
    fallback selection) and confirming the workspace -- not the empty
    state -- renders for it."""
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "AGE1", agn_message("AGE1TICKET"), minutes_ago=65)
    at = app_for(PAGE, desk_env)
    at.session_state["desk_dispatch_selected"] = "AGE1"
    at.run()
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "AGE1TICKET" in rendered
    assert any(b.key == "desk_k_AGE1" for b in at.button)


# ----------------------------------------------------------- Job summary -
def test_full_message_hidden_by_default_available_in_job_details(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "MSG1", agn_message("MSG1TICKET"))
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    primary_view = markdown_outside_any_expander(at)
    assert "100 Fake St" not in primary_view  # full address not in the primary view
    assert "1-555-010-3000" not in primary_view  # full phone not in the primary view

    details_expander = next(e for e in at.expander if "Job details" in (e.label or ""))
    nested = "\n".join(m.value for m in details_expander.markdown) + "\n".join(c.value for c in details_expander.code)
    assert "100 Fake St" in nested
    assert "1-555-010-3000" in nested
    assert "MSG1TICKET" in nested  # original message + prepared technician message both present


def test_age_shown_in_whole_minutes_not_seconds(desk_env):
    import re

    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "AGE2", agn_message("AGE2TICKET"))
    at = app_for(PAGE, desk_env)
    rendered = "\n".join(m.value for m in at.markdown)
    # A real "N sec"/"N min N sec" duration string -- not a false positive
    # on the unrelated "section" CSS class name.
    assert re.search(r"\d+\s*sec\b", rendered.lower()) is None
    assert "desk-age\">0m" in rendered or re.search(r'desk-age">\d+m', rendered)


# ------------------------------------------------- Compact queue format --
def test_compact_queue_row_format_excludes_seconds_and_long_sentences(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "ROW1", agn_message("ROWTICKET", area="Charlotte, NC"))
    at = app_for(PAGE, desk_env)
    row = next(b for b in at.button if b.key == "desk_queue_row_ROW1")
    assert "sec" not in row.label.lower()
    assert "Received" not in row.label
    assert "Not dispatched" not in row.label
    assert "•" in row.label  # compact bullet-separated format
    assert "AGN" in row.label and "ROWTICKET" in row.label and "Charlotte" in row.label


# ---------------------------------------------- AGN/Lucky stateful K -----
def test_agn_k_never_shows_copy_and_confirm_simultaneously(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "KSEQ1", agn_message("KSEQ1TICKET"))
    at = app_for(PAGE, desk_env)

    assert any(b.key == "desk_k_KSEQ1" and b.label == "Copy K" for b in at.button)
    assert not any(b.key == "desk_k_confirm_KSEQ1" for b in at.button)

    at.button(key="desk_k_KSEQ1").click().run()
    assert not any(b.key == "desk_k_KSEQ1" for b in at.button)  # Copy K gone
    assert any(b.key == "desk_k_confirm_KSEQ1" and b.label == "Confirm K sent in Quo" for b in at.button)

    at.button(key="desk_k_confirm_KSEQ1").click().run()
    assert not any(b.key == "desk_k_confirm_KSEQ1" for b in at.button)  # Confirm gone
    rendered = "\n".join(m.value for m in at.markdown)
    assert "✓ K sent" in rendered


def test_action_area_2_unavailable_until_k_confirmed(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "GATE1", agn_message("GATE1TICKET"))
    at = app_for(PAGE, desk_env)
    assert not any(b.key == "desk_wa_GATE1" for b in at.button)

    at.button(key="desk_k_GATE1").click().run()
    assert not any(b.key == "desk_wa_GATE1" for b in at.button)  # still gated -- only revealed, not confirmed

    at.button(key="desk_k_confirm_GATE1").click().run()
    assert any(b.key == "desk_wa_GATE1" and b.label == "Copy job for technician" for b in at.button)


def test_agn_full_dispatch_sequence_one_action_at_a_time(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "FULL1", agn_message("FULL1TICKET"))
    at = app_for(PAGE, desk_env)
    at.button(key="desk_k_FULL1").click().run()
    at.button(key="desk_k_confirm_FULL1").click().run()

    at.button(key="desk_wa_FULL1").click().run()
    assert not any(b.key == "desk_wa_FULL1" for b in at.button)
    assert any(b.key == "desk_confirm_dispatch_FULL1" and b.label == "Confirm sent in WhatsApp" for b in at.button)

    at.button(key="desk_confirm_dispatch_FULL1").click().run()
    at.run()  # settle -- see test_mobile_field_workflow.py's same note on rerun-inside-handler
    assert len(at.exception) == 0
    conn = storage.connect(desk_env)
    status = conn.execute("SELECT status FROM jobs WHERE ticket_number = ?", ("FULL1TICKET",)).fetchone()
    conn.close()
    assert status is not None and status[0] == "Approved"


# --------------------------------------------------------- Main Leads ----
def test_main_leads_never_shows_k_wording_and_dispatches_directly(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "ML1", main_leads_message("ML1TICKET"), group_label="Main Leads 2")
    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)
    assert "K acknowledgment" not in rendered
    assert not any(b.key and b.key.startswith("desk_k_") for b in at.button)
    assert any(b.key == "desk_wa_ML1" and b.label == "Copy job for technician" for b in at.button)

    at.button(key="desk_wa_ML1").click().run()
    assert any(b.key == "desk_confirm_dispatch_ML1" and b.label == "Confirm sent in WhatsApp" for b in at.button)


# -------------------------------------------------- Missing destination --
def test_missing_whatsapp_destination_blocks_normal_dispatch_confirmation(desk_env):
    """Regression test for the exact unsafe condition this rebuild fixes:
    a missing WhatsApp destination must never leave a normal dispatch-
    confirmation path available."""
    seed_live_boundary(desk_env)
    # No technician configured at all for this area -- destination is None.
    seed_pending(desk_env, "NODEST2", agn_message("NODEST2TICKET", area="Charlotte, NC"))
    at = app_for(PAGE, desk_env)
    rendered = "\n".join(m.value for m in at.markdown)
    assert "WhatsApp group not configured." in rendered

    at.button(key="desk_k_NODEST2").click().run()
    at.button(key="desk_k_confirm_NODEST2").click().run()
    assert len(at.exception) == 0

    assert not any(b.key == "desk_wa_NODEST2" for b in at.button)
    assert not any(b.key == "desk_confirm_dispatch_NODEST2" for b in at.button)
    assert not any((b.label or "") in ("Confirm sent in WhatsApp", "Confirm dispatched", "I sent it in WhatsApp") for b in at.button)
    assert any(b.key == "desk_wa_copy_only_NODEST2" and b.label == "Copy job" for b in at.button)

    conn = storage.connect(desk_env)
    dispatched_count = conn.execute(
        "SELECT COUNT(*) FROM dispatch_tracking WHERE message_id = ? AND dispatched_manually_at IS NOT NULL",
        ("NODEST2",),
    ).fetchone()[0]
    conn.close()
    assert dispatched_count == 0  # never recorded as dispatched through an undefined destination


# --------------------------------------------------- No page-render writes
def test_rendering_new_jobs_page_never_writes(desk_env):
    seed_live_boundary(desk_env)
    seed_kabaka(desk_env)
    seed_pending(desk_env, "NOWRITE1", agn_message("NOWRITETICKET"))
    conn = storage.connect(desk_env)
    before_tracking = conn.execute("SELECT COUNT(*) FROM dispatch_tracking").fetchone()[0]
    before_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    at = app_for(PAGE, desk_env)
    assert len(at.exception) == 0
    at.run()

    conn = storage.connect(desk_env)
    after_tracking = conn.execute("SELECT COUNT(*) FROM dispatch_tracking").fetchone()[0]
    after_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert before_tracking == after_tracking
    assert before_jobs == after_jobs
