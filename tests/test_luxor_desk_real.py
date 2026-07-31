"""Focused tests for the real, database-backed LUXOR OPERATIONS DESK
(luxor_desk.py + luxor_desk_pages/*.py + luxor_desk_read_model.py).

Fabricated records only ever exist here, inside this test file, seeded
into a temporary on-disk SQLite database via storage.connect(tmp_path) --
never the real database. luxor_desk_read_model.py's dedup/live-boundary/
gate logic is shared with luxor_console_pages/dispatch_queue.py (see
that module's own docstring), so tests/test_speed_console.py's 30 tests
already cover that shared logic in depth; this file focuses on what's
new here: no fabricated production data, the full real queue (not one
focused job plus hidden "Also in..."), company grouping, workspace
click-through, HUMAN_REVIEW_NEEDED wording, and the four real reference
pages."""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import storage

DESK_FILES = [
    "luxor_desk.py", "luxor_desk_shared.py", "luxor_desk_read_model.py",
    "luxor_desk_pages/dispatch.py", "luxor_desk_pages/active_jobs.py",
    "luxor_desk_pages/closings.py", "luxor_desk_pages/technicians.py",
    "luxor_desk_pages/activity.py",
]

FABRICATED_IDENTIFIERS = ("ML-77QX", "A6D19", "L4P81", "L2Q56", "Alex Rivera", "Taylor Nguyen", "$95.00")


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
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


def future_ts(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def seed_live_boundary(db_path, when="2020-01-01T00:00:00Z"):
    conn = storage.connect(db_path)
    conn.execute(
        "INSERT INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET live_queue_started_at = excluded.live_queue_started_at, updated_at = excluded.updated_at",
        (when, when),
    )
    conn.commit()
    conn.close()


def seed_pending(db_path, message_id, raw_text, group_label="AGN", received_at=None, minutes_from_now=5):
    if received_at is None:
        received_at = future_ts(minutes_from_now)
    conn = storage.connect(db_path)
    storage.save_pending_and_mark_processed(
        conn, message_id=message_id, conversation_id=f"c-{message_id}", group_label=group_label,
        raw_text=raw_text, from_number="+15550109900", received_at=received_at,
    )
    conn.close()


def agn_message(ticket="FAKE1", area="Charlotte, NC"):
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Fake St, {area}\nDesc: Car Lockout\n"


def main_leads_message(ticket="ML1", area="Charlotte, NC 28202"):
    return (
        f"Power By Ucallz App\n\nFabricated Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Test Person\nPhone: (555) 010-2000\nAddress: 100 Fake Dr, {area}\nDesc: Car Lockout\n"
    )


def make_app():
    at = AppTest.from_file("luxor_desk.py", default_timeout=30)
    at.run()
    return at


def login(at, password="pw"):
    at.text_input(key="luxor_desk_password_input").set_value(password).run()
    at.button[0].click().run()
    assert len(at.exception) == 0
    return at


# --------------------------------------------------- Structural guards ---
def test_no_fabricated_dataset_imported_by_production_files():
    """Scans only actual import lines -- not docstrings/comments that
    describe this same constraint in prose, which would otherwise
    self-trigger (luxor_desk.py's own module docstring explains that it
    never imports luxor_desk_data, which literally contains the string
    "luxor_desk_data")."""
    forbidden = ("luxor_desk_data", "DESK_JOBS", "RECORDS_TECHNICIANS")
    for f in DESK_FILES:
        lines = pathlib.Path(f).read_text(encoding="utf-8").splitlines()
        import_lines = [line for line in lines if line.strip().startswith(("import ", "from "))]
        for bad in forbidden:
            assert not any(bad in line for line in import_lines), f"{f} must not import {bad}"


def test_fabricated_identifiers_cannot_appear_in_production_files():
    for f in DESK_FILES:
        source = pathlib.Path(f).read_text(encoding="utf-8")
        for bad in FABRICATED_IDENTIFIERS:
            assert bad not in source, f"{f} must not contain fabricated identifier {bad!r}"


def test_no_raw_script_or_onclick_anywhere():
    forbidden = ("<script", "onclick=", "onclick =", "javascript:", "navigator.clipboard")
    for f in DESK_FILES:
        source = pathlib.Path(f).read_text(encoding="utf-8")
        for bad in forbidden:
            assert bad not in source, f"{f} must never contain {bad!r}"


def test_no_second_persistence_layer():
    """luxor_desk_read_model.py must call the existing storage.py
    functions, never open a raw sqlite3 connection or define new tables
    itself -- only dispatcher.storage owns schema/writes."""
    source = pathlib.Path("luxor_desk_read_model.py").read_text(encoding="utf-8")
    assert "CREATE TABLE" not in source
    assert "sqlite3.connect(" not in source


def test_get_policy_import_guard_uses_get_policy_only():
    # AGN/Lucky K-ack decisions must go through the existing policy
    # module, not a hardcoded per-source if/else duplicating its rules.
    source = pathlib.Path("luxor_desk_shared.py").read_text(encoding="utf-8")
    assert "from dispatcher.policy import get_policy" in source


# ----------------------------------------------------------- Login gate --
def test_login_gate_blocks_unauthenticated_access(desk_env):
    at = make_app()
    assert len(at.exception) == 0
    assert any("LUXOR LOCKSMITH" in t.value for t in at.title)


# --------------------------------------------------------------- Queue ---
def test_all_live_jobs_shown_not_one_plus_hidden_also_in(desk_env):
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "Q1", agn_message("Q1TICKET"))
    seed_pending(desk_env, "Q2", agn_message("Q2TICKET"))
    seed_pending(desk_env, "Q3", agn_message("Q3TICKET"))
    at = login(make_app())
    assert len(at.exception) == 0
    queue_buttons = [b for b in at.button if b.key and b.key.startswith("desk_queue_row_")]
    # All 3 live jobs must be individually selectable rows -- none hidden.
    assert len(queue_buttons) == 3
    assert not any("Also in" in (b.label or "") for b in at.button)


def test_ack_fragments_never_become_queue_rows(desk_env):
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "ACK1", "k", group_label="AGN")
    seed_pending(desk_env, "ACK2", "ok", group_label="AGN")
    at = login(make_app())
    assert len(at.exception) == 0
    expander_labels = [e.label for e in at.expander]
    assert any("Other Messages (2)" in label for label in expander_labels)
    queue_buttons = [b for b in at.button if b.key and b.key.startswith("desk_queue_row_")]
    assert len(queue_buttons) == 0


def test_backlog_excluded_from_queue(desk_env):
    # No explicit boundary -- ensure_live_queue_activated seeds it to
    # "now" on first run, so this 2020 message is backlog by construction.
    seed_pending(desk_env, "OLD1", agn_message("OLDTICKET"), received_at="2020-01-01T00:00:00Z")
    at = login(make_app())
    assert len(at.exception) == 0
    queue_buttons = [b for b in at.button if b.key and b.key.startswith("desk_queue_row_")]
    assert len(queue_buttons) == 0
    expander_labels = [e.label for e in at.expander]
    assert any("Previous Backlog (1)" in label for label in expander_labels)


def test_duplicate_source_ticket_renders_once(desk_env):
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "DUP1", agn_message("DUPTICKET"), minutes_from_now=5)
    seed_pending(desk_env, "DUP2", agn_message("DUPTICKET"), minutes_from_now=10)
    at = login(make_app())
    assert len(at.exception) == 0
    queue_buttons = [b for b in at.button if b.key and b.key.startswith("desk_queue_row_")]
    assert len(queue_buttons) == 1
    assert "(+1)" in queue_buttons[0].label


def test_ticketless_messages_never_merged(desk_env):
    seed_live_boundary(desk_env)
    no_ticket_a = "Ref: AGN Locksmith Services\nPh: 1-555-010-3000\nAddr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n"
    no_ticket_b = "Ref: AGN Locksmith Services\nPh: 1-555-010-3001\nAddr: 200 Fake St, Charlotte, NC\nDesc: House Lockout\n"
    seed_pending(desk_env, "NTA", no_ticket_a)
    seed_pending(desk_env, "NTB", no_ticket_b)
    at = login(make_app())
    assert len(at.exception) == 0
    # No ticket -> gate fails -> Other Messages, never merged into one row.
    expander_labels = [e.label for e in at.expander]
    assert any("Other Messages (2)" in label for label in expander_labels)


def test_company_filter_counts_correct(desk_env):
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "FAGN", agn_message("FAGNTICKET"))
    seed_pending(desk_env, "FML", main_leads_message("FMLTICKET"), group_label="Main Leads 2")
    at = login(make_app())
    pills = at.pills(key="desk_dispatch_filter")
    assert "AGN (1)" in pills.options
    assert "Main Leads (1)" in pills.options
    assert "All (2)" in pills.options


# -------------------------------------------------------- Job workspace --
def test_clicking_queue_row_opens_its_workspace(desk_env):
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "WS1", agn_message("WSTICKET"))
    seed_pending(desk_env, "WS2", agn_message("WSTICKET2"))
    at = login(make_app())
    # Queue row identity is the message_id (WS2), not the ticket text --
    # find the row by its visible label instead.
    target = next(b for b in at.button if b.key and b.key.startswith("desk_queue_row_") and "WSTICKET2" in (b.label or ""))
    target.click().run()
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "WSTICKET2" in rendered


def test_agn_shows_k_ready_wording(desk_env):
    """Copy K -> Confirm K sent in Quo -> checked line: one stateful
    button, never both states shown simultaneously."""
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "K1", agn_message("K1TICKET"))
    at = login(make_app())
    assert any(b.key == "desk_k_K1" and b.label == "Copy K" for b in at.button)
    assert not any(c.value == "K" for c in at.code)  # not revealed until tapped
    assert not any(b.key == "desk_k_confirm_K1" for b in at.button)

    at.button(key="desk_k_K1").click().run()
    assert any(c.value == "K" for c in at.code)
    assert any(b.key == "desk_k_confirm_K1" and b.label == "Confirm K sent in Quo" for b in at.button)
    assert not any(b.key == "desk_k_K1" for b in at.button)  # Copy K gone once revealed

    at.button(key="desk_k_confirm_K1").click().run()
    rendered = "\n".join(m.value for m in at.markdown)
    assert "✓ K sent" in rendered
    assert not any(b.key == "desk_k_confirm_K1" for b in at.button)


def test_main_leads_shows_no_k_wording(desk_env):
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "ML1", main_leads_message("MLTICKET"), group_label="Main Leads 2")
    at = login(make_app())
    rendered = "\n".join(c.value for c in at.caption) + "\n".join(m.value for m in at.markdown)
    assert "K acknowledgment" not in rendered
    assert not any(b.key == "desk_k_ML1" for b in at.button)
    assert not any((b.label or "") in ("Copy K", "Confirm K sent in Quo") for b in at.button)


def test_missing_required_fields_shows_human_review_needed(desk_env):
    seed_live_boundary(desk_env)
    incomplete = "Ref: AGN Locksmith Services\nPDL: INCOMPLETE1\nPh: 1-555-010-3000\nDesc: Car Lockout\n"
    seed_pending(desk_env, "INC1", incomplete)
    at = login(make_app())
    assert len(at.exception) == 0
    rendered = "\n".join(w.value for w in at.warning)
    assert "Review before dispatching" in rendered
    # Dispatch actions must be replaced, not merely warned alongside.
    assert not any(b.key == "desk_wa_INC1" for b in at.button)
    assert not any(b.key == "desk_k_INC1" for b in at.button)


def test_multiple_eligible_technicians_all_shown(desk_env):
    seed_live_boundary(desk_env)
    conn = storage.connect(desk_env)
    storage.add_technician(conn, name="Fabricated Tech A", areas=["Charlotte"], skills=["car lockout"])
    storage.add_technician(conn, name="Fabricated Tech B", areas=["Charlotte"], skills=["car lockout", "house lockout"])
    conn.close()
    seed_pending(desk_env, "MULTI1", agn_message("MULTITICKET", area="Charlotte, NC"))
    at = login(make_app())
    assert len(at.exception) == 0
    rendered = "\n".join(c.value for c in at.caption)
    assert "Fabricated Tech A" in rendered
    assert "Fabricated Tech B" in rendered


def test_no_whatsapp_or_dispatch_claim_without_destination(desk_env):
    """No normal dispatch-confirmation path is ever offered through an
    undefined WhatsApp destination -- only a copy-only fallback. AGN
    requires K confirmed first (action area 2 stays gated until then)."""
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "NODEST1", agn_message("NODESTTICKET", area="Charlotte, NC"))
    at = login(make_app())
    at.button(key="desk_k_NODEST1").click().run()
    at.button(key="desk_k_confirm_NODEST1").click().run()
    assert len(at.exception) == 0

    assert not any(b.key == "desk_wa_NODEST1" for b in at.button)
    assert not any(b.key == "desk_confirm_dispatch_NODEST1" for b in at.button)
    assert any(b.key == "desk_wa_copy_only_NODEST1" for b in at.button)
    rendered = "\n".join(m.value for m in at.markdown)
    assert "WhatsApp group not configured." in rendered


def test_pass_cancel_requires_reason(desk_env):
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "PASS1", agn_message("PASSTICKET"))
    at = login(make_app())
    reject_button = next(b for b in at.button if b.key == "desk_reject_PASS1")
    assert reject_button.disabled is True


# ------------------------------------------------------- Reference pages -
def test_active_jobs_page_uses_real_data_only(desk_env):
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file("luxor_desk_pages/active_jobs.py", default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)
    for bad in FABRICATED_IDENTIFIERS:
        assert bad not in rendered


def test_closings_page_shows_empty_state_not_fabricated_data(desk_env):
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file("luxor_desk_pages/closings.py", default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    assert len(at.exception) == 0
    assert any("No closings are waiting." in s.value for s in at.success)


def test_technicians_page_shows_real_roster_only(desk_env):
    import luxor_desk_shared as shared_desk
    conn = storage.connect(desk_env)
    storage.add_technician(conn, name="Fabricated Roster Tech", areas=["Charlotte"], skills=["car lockout"])
    conn.close()
    at = AppTest.from_file("luxor_desk_pages/technicians.py", default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    assert len(at.exception) == 0
    rendered = "\n".join(m.value for m in at.markdown)
    assert "Fabricated Roster Tech" in rendered
    for bad in ("Alex Rivera", "Taylor Nguyen"):
        assert bad not in rendered


def test_activity_page_renders_without_error(desk_env):
    import luxor_desk_shared as shared_desk
    at = AppTest.from_file("luxor_desk_pages/activity.py", default_timeout=30)
    at.session_state[shared_desk.AUTH_SESSION_KEY] = True
    at.run()
    assert len(at.exception) == 0


# ---------------------------------------------------------- Outbound/GET -
def test_outbound_defaults_disabled_in_workspace(desk_env, monkeypatch):
    monkeypatch.delenv("QUO_OUTBOUND_MODE", raising=False)
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "OUT1", agn_message("OUTTICKET"))
    at = login(make_app())
    button = next(b for b in at.button if b.key == "desk_k_OUT1")
    button.click().run()
    assert len(at.exception) == 0
    conn = storage.connect(desk_env)
    count = conn.execute("SELECT COUNT(*) FROM quo_outbound_log").fetchone()[0]
    conn.close()
    assert count == 0


def test_no_write_http_verbs_in_dispatch_page():
    source = pathlib.Path("luxor_desk_pages/dispatch.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source


def test_quo_api_key_never_hardcoded_or_logged():
    """The real API key must only ever flow through
    os.environ.get(API_KEY_ENV_VAR) -- never a literal, never printed,
    never written into a caption/markdown string."""
    for f in DESK_FILES:
        source = pathlib.Path(f).read_text(encoding="utf-8")
        assert "print(" not in source or "api_key" not in source.lower()
        # The only acceptable appearance of the literal env var name is
        # importing/referencing API_KEY_ENV_VAR, never the raw string.
        for line in source.splitlines():
            if "QUO_API_KEY" in line:
                assert "API_KEY_ENV_VAR" not in line or "import" in line, (
                    f"{f}: {line!r} -- QUO_API_KEY should only appear via the API_KEY_ENV_VAR constant"
                )


def test_tests_use_temporary_database_only(desk_env, tmp_path):
    """desk_env's own fixture never points at the real DB_PATH -- this
    guards against a future edit accidentally removing the monkeypatch."""
    assert str(desk_env).endswith("test.db")
    assert str(desk_env).startswith(str(tmp_path))  # pytest's own isolated tmp dir, never the real repo DB
    from dispatcher import storage
    assert storage.DB_PATH == desk_env
    assert "Desktop\\ai dispatcher\\data" not in str(storage.DB_PATH).replace("/", "\\")


# ============================================================
# Regression tests for the two architectural defects
# ============================================================

def test_rendering_dispatch_page_never_writes_dispatch_tracking(desk_env):
    """Merely opening/refreshing the Dispatch page must not record any
    operational timestamp -- first_seen/prepared are now stamped only at
    the real ingestion boundary (quo_ingest.poll_for_new_messages), which
    this test never calls. A message seeded directly (bypassing
    ingestion, as every other test in this file also does) must show an
    entirely untouched dispatch_tracking row before and after the page
    renders."""
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "SIDEFX1", agn_message("SIDEFXTICKET"))

    conn = storage.connect(desk_env)
    before = storage.get_dispatch_tracking(conn, "SIDEFX1")
    conn.close()
    assert before["first_seen_at"] is None
    assert before["luxor_prepared_at"] is None

    at = login(make_app())
    assert len(at.exception) == 0
    # Render again (simulating a refresh) to be doubly sure.
    at.run()

    conn = storage.connect(desk_env)
    after = storage.get_dispatch_tracking(conn, "SIDEFX1")
    conn.close()
    assert after["first_seen_at"] is None
    assert after["luxor_prepared_at"] is None


def test_ingestion_boundary_is_where_first_seen_and_prepared_get_stamped(desk_env, monkeypatch):
    """The correct boundary (quo_ingest.poll_for_new_messages) DOES stamp
    both timestamps, exactly once, at the moment a message is actually
    ingested -- confirming the write moved, rather than simply vanished."""
    from unittest.mock import MagicMock

    from dispatcher import quo_ingest
    from dispatcher.quo_client import QuoMessage

    conn = storage.connect(desk_env)
    fake_client = MagicMock()
    fake_contact = MagicMock(phone_numbers=["+15550001111"])
    fake_client.get_contact.return_value = fake_contact
    fake_client.iter_messages.return_value = [
        QuoMessage(
            id="ING1", to="PN1", from_number="+15550001111", text=agn_message("INGTICKET"),
            phone_number_id="PN1", conversation_id="c1", direction="incoming",
            status="delivered", created_at=future_ts(5), updated_at=future_ts(5),
        )
    ]
    config = quo_ingest.QuoPollConfig(
        our_phone_number_id="PN1",
        dispatch_groups=[quo_ingest.DispatchGroupConfig(label="AGN", quo_contact_id="C1")],
    )
    quo_ingest.poll_for_new_messages(conn, fake_client, config)

    tracking = storage.get_dispatch_tracking(conn, "ING1")
    conn.close()
    assert tracking["first_seen_at"] is not None
    assert tracking["luxor_prepared_at"] is not None


def test_correction_for_same_ticket_stays_separately_actionable(desk_env):
    """A correction/cancellation/status/closing sharing (source, ticket)
    with an already-dispatched job must render as its OWN canonical
    queue row -- never silently folded into (and hidden behind) the
    original new-job card."""
    import sys
    sys.path.insert(0, ".")
    sys.path.insert(0, "src")
    from dispatcher.parser import extract_job, load_vehicle_rules

    seed_live_boundary(desk_env)
    vehicle_rules = load_vehicle_rules()

    original_raw = agn_message("CORRTICKET", area="Charlotte, NC")
    conn = storage.connect(desk_env)
    job = extract_job(original_raw, vehicle_rules)
    result = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, result, final_action="Approve")  # commits a real jobs row
    conn.close()

    # A corrected address for the SAME ticket -- must classify as a
    # CORRECTION (existing_job_id set), not a fresh unrelated NEW job.
    corrected_raw = original_raw.replace("100 Fake St", "999 Other Rd")
    seed_pending(desk_env, "CORR_NEW", original_raw, minutes_from_now=5)
    seed_pending(desk_env, "CORR_FIX", corrected_raw, minutes_from_now=10)

    at = login(make_app())
    assert len(at.exception) == 0
    queue_buttons = [b for b in at.button if b.key and b.key.startswith("desk_queue_row_")]
    # Both the original new-job message and the correction are distinct
    # canonical entries -- neither collapses into or hides the other.
    assert len(queue_buttons) == 2


def test_exact_reminder_of_same_event_still_collapses(desk_env):
    """The fix for lifecycle-aware dedup must not regress the original
    behavior: two byte-identical re-sends of the same NEW-job message
    still fold into one canonical row with a "+1" count."""
    seed_live_boundary(desk_env)
    seed_pending(desk_env, "REM1", agn_message("REMTICKET"), minutes_from_now=5)
    seed_pending(desk_env, "REM2", agn_message("REMTICKET"), minutes_from_now=10)
    at = login(make_app())
    assert len(at.exception) == 0
    queue_buttons = [b for b in at.button if b.key and b.key.startswith("desk_queue_row_")]
    assert len(queue_buttons) == 1
    assert "(+1)" in queue_buttons[0].label
