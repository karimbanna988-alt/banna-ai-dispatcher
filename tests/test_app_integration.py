"""End-to-end integration tests driving app.py itself (not just the
src/dispatcher modules in isolation) via streamlit.testing.v1.AppTest, using
the fake-inbox simulator tab to exercise every conversation scenario.

Each test isolates its own SQLite database (monkeypatched storage.DB_PATH +
st.cache_resource.clear()) so runs never touch the real data/dispatcher.db
and never leak cached connections between tests.
"""

import json

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dispatcher import quo_ingest, storage
from dispatcher.quo_client import Contact, QuoMessage
from dispatcher.quo_ingest import load_dispatch_groups

SIM_TAB_INDEX = 5
PROCESS_TAB_INDEX = 0
STATUS_TAB_INDEX = 2
CLOSING_TAB_INDEX = 3
QUO_LIVE_TAB_INDEX = 6
JOBS_TAB_INDEX = 7
TECHNICIANS_TAB_INDEX = 8


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    # app.py calls load_dotenv() on every run, which would otherwise load a
    # real QUO_API_KEY from a developer's local .env into this test process.
    # python-dotenv's load_dotenv() never overrides a key that's already set,
    # so pre-setting it to "" here keeps the real key out of test runs
    # entirely -- no real credential is ever loaded, let alone used, by the
    # automated suite. Individual tests override this with their own fake
    # key via monkeypatch.setenv when they need the Quo tab "configured".
    monkeypatch.setenv("QUO_API_KEY", "")
    st.cache_resource.clear()
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()
    assert len(at.exception) == 0
    return at


def select_scenario(at, title):
    at.tabs[SIM_TAB_INDEX].selectbox[0].select(title).run()
    assert len(at.exception) == 0


def deliver_next(at):
    at.tabs[SIM_TAB_INDEX].button[0].click().run()
    assert len(at.exception) == 0


def click_process_action(at, label):
    btn = [b for b in at.tabs[PROCESS_TAB_INDEX].button if b.label == label][0]
    btn.click().run()
    assert len(at.exception) == 0


def timeline_for(ticket):
    conn = storage.connect(storage.DB_PATH)
    return storage.get_audit_timeline(conn, ticket)


# ---------------------------------------------------------- Channel tags ---
MANUAL_PASTE_TEXT = (
    "Power By Ucallz App\n\nFabricated Locksmith Pro\nTicket: CHANMANUAL1\n"
    "Name: Test Person\nPhone: (555) 010-1000\n2nd Phone #:\n"
    "Address: 100 Fake St, Faketown, GA 30301\nDesc: Car lockout\n2015 Toyota Corolla\n"
)


def test_manually_pasted_message_is_tagged_manual_channel(app):
    """A message typed/pasted directly into the Process tab (not delivered
    via the simulator or the live Quo tab) must be tagged "manual" -- the
    default, ordinary path, distinguishable from shadow mode."""
    process_tab = app.tabs[PROCESS_TAB_INDEX]
    process_tab.text_area[0].set_value(MANUAL_PASTE_TEXT).run()
    process_tab = app.tabs[PROCESS_TAB_INDEX]
    [b for b in process_tab.button if b.label == "Process"][0].click().run()
    click_process_action(app, "Approve")

    timeline = timeline_for("CHANMANUAL1")
    assert len(timeline) == 1
    assert timeline[0]["channel"] == "manual"


def test_simulator_delivered_message_is_tagged_simulator_channel(app):
    select_scenario(app, "New job -- AGN")
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for("SIM2AG")
    assert timeline[0]["channel"] == "simulator"


# ------------------------------------------------------------- New job -----
@pytest.mark.parametrize(
    "title, ticket",
    [
        ("New job -- Main Leads", "SIM1-MLNJ"),
        ("New job -- AGN", "SIM2AG"),
        ("New job -- Lucky", "SIM3LK"),
    ],
)
def test_new_job_is_classified_new_and_recorded(app, title, ticket):
    select_scenario(app, title)
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for(ticket)
    assert len(timeline) == 1
    assert timeline[0]["message_type"] == "NEW"
    assert timeline[0]["final_action"] == "Approve"


# ------------------------------------------------------- Reminder/duplicate -
def test_identical_reminder_is_classified_duplicate(app):
    select_scenario(app, "Reminder / duplicate -- AGN (byte-identical)")
    deliver_next(app)
    click_process_action(app, "Approve")
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for("SIM4RD")
    assert [e["message_type"] for e in timeline] == ["NEW", "DUPLICATE"]


def test_reworded_reminder_is_a_documented_gap_not_duplicate(app):
    """Known, accepted gap (see docs/architecture.md Known Limitations):
    classify_incoming only detects DUPLICATE for a byte-identical repeat.
    A reworded reminder for the same ticket currently falls through to
    UNRECOGNIZED. This test documents that behavior so it's a visible,
    tracked fact -- not a silent surprise -- and will need updating the day
    dedicated reminder detection is added."""
    select_scenario(app, "Reminder -- worded differently (known gap)")
    deliver_next(app)
    click_process_action(app, "Approve")
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for("SIM5RW")
    assert [e["message_type"] for e in timeline] == ["NEW", "UNRECOGNIZED"]


# ------------------------------------------------------- Corrected info ----
def test_corrected_address_is_classified_correction(app):
    select_scenario(app, "Corrected job information -- Lucky")
    deliver_next(app)
    click_process_action(app, "Approve")
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for("SIM6CA")
    assert [e["message_type"] for e in timeline] == ["NEW", "CORRECTION"]


# ------------------------------------------------------- Cancellation ------
def test_explicit_cancellation_is_classified(app):
    select_scenario(app, 'Customer cancellation -- says "cancel"')
    deliver_next(app)
    click_process_action(app, "Approve")
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for("SIM7CE")
    assert [e["message_type"] for e in timeline] == ["NEW", "CANCELLATION"]


def test_cancellation_worded_differently_is_now_correctly_classified(app):
    """Previously a documented, accepted gap: CANCELLATION detection was a
    substring check for the literal word "cancel" only. Fixed 2026-07-29
    with conservative, deterministic reworded-phrase patterns (see
    storage._is_confident_cancellation) -- the real message that
    originally exposed this gap ("customer found her keys, no longer
    needs service, hung up the call") now correctly classifies as
    CANCELLATION instead of falling through to UNRECOGNIZED."""
    select_scenario(app, "Customer cancellation -- worded differently")
    deliver_next(app)
    click_process_action(app, "Approve")
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for("SIM8CW")
    assert [e["message_type"] for e in timeline] == ["NEW", "CANCELLATION"]


# --------------------------------------------------- Technician status -----
def test_technician_status_updates_are_persisted(app):
    select_scenario(app, "Technician status update -- AGN")
    deliver_next(app)  # dispatch the job
    click_process_action(app, "Approve")

    expected_replies = ["Going", "Tech ETA 15 min", "Tech called"]
    for _ in expected_replies:
        deliver_next(app)
        status_tab = app.tabs[STATUS_TAB_INDEX]
        approve_btn = [b for b in status_tab.button if b.label == "Approve status reply"][0]
        approve_btn.click().run()
        assert len(app.exception) == 0

    timeline = timeline_for("SIM9ST")
    assert [e["message_type"] for e in timeline] == ["NEW", "STATUS", "STATUS", "STATUS"]
    replies = [e["luxor_message"] for e in timeline[1:]]
    assert replies == expected_replies


# -------------------------------------------------------------- Closing ----
def test_closing_is_rendered_and_recorded_for_a_supported_company(app):
    select_scenario(app, "Closing information -- AGN")
    deliver_next(app)  # dispatch the job
    click_process_action(app, "Approve")

    deliver_next(app)  # deliver the closing input
    closing_tab = app.tabs[CLOSING_TAB_INDEX]
    record_btn = [b for b in closing_tab.button if "Record" in b.label][0]
    record_btn.click().run()
    assert len(app.exception) == 0

    timeline = timeline_for("SIM10CL")
    assert [e["message_type"] for e in timeline] == ["NEW", "CLOSING"]
    assert timeline[1]["luxor_message"].startswith("Ref: AGN Locksmith Services")


def test_closing_is_rejected_for_a_company_with_no_template(app):
    select_scenario(app, "Closing information -- Main Leads (expect unsupported)")
    deliver_next(app)  # dispatch the job
    click_process_action(app, "Approve")

    deliver_next(app)  # attempt the closing input
    closing_tab = app.tabs[CLOSING_TAB_INDEX]
    assert not [b for b in closing_tab.button if "Record" in b.label]
    assert closing_tab.error[0].value.startswith("Main Leads jobs are our own advertising leads")

    timeline = timeline_for("SIM11UN")
    assert [e["message_type"] for e in timeline] == ["NEW"]


# ---------------------------------------------------------- Quo live tab ---
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


QUO_AGN_TEXT = (
    "Ref: AGN Locksmith Services\nPDL: QLIVE1\nN: Pat\n"
    "Ph: 1-555-010-9900 (Job Ext: 700)\nAddr: 400 Fake Way , Faketown, NC, 27500\n"
    "Desc: Car Lockout [19+]\nOccu: Locksmith\n"
)


def test_quo_live_tab_shows_error_when_api_key_missing(app, monkeypatch):
    monkeypatch.delenv("QUO_API_KEY", raising=False)
    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    assert quo_tab.error[0].value.startswith("QUO_API_KEY is not set")
    assert quo_tab.button == []


def test_quo_live_message_flows_through_same_pipeline_and_audit_log(app, monkeypatch):
    monkeypatch.setenv("QUO_API_KEY", "test-key-not-real")

    # Uses the real rules/quo_dispatch_groups.yaml -- poll_for_new_messages
    # will call get_contact() for every configured group, so the stub needs
    # an entry for each of the four real ones, not just the one we care
    # about for this test.
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
        id="MSGLIVE1", to="PNus", from_number=agn_phone, text=QUO_AGN_TEXT,
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONVLIVE1", direction="incoming",
        status="delivered", created_at="2026-07-27T10:00:00Z", updated_at="2026-07-27T10:00:00Z",
    )
    monkeypatch.setattr(
        quo_ingest, "QuoClient", _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound]}),
    )
    app.run()  # re-run so the newly-set QUO_API_KEY env var takes effect

    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    quo_tab.button[0].click().run()  # "Poll Quo now"
    assert len(app.exception) == 0
    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    assert "Found 1 new message" in quo_tab.success[0].value

    quo_tab.button[1].click().run()  # "Load next Quo message into review"
    assert len(app.exception) == 0

    # Same review screen, same pipeline, as every other message source.
    process_tab = app.tabs[PROCESS_TAB_INDEX]
    assert any("AGN" in md.value for md in process_tab.markdown)
    click_process_action(app, "Approve")

    timeline = timeline_for("QLIVE1")
    assert len(timeline) == 1
    assert timeline[0]["message_type"] == "NEW"
    assert timeline[0]["final_action"] == "Approve"
    assert timeline[0]["raw_message"] == QUO_AGN_TEXT
    # Shadow mode: the audit record must be clearly distinguishable from a
    # manually pasted or simulator-delivered message.
    assert timeline[0]["channel"] == quo_ingest.SHADOW_MODE_CHANNEL

    # Polling again with the same stub (same message) must not re-queue it.
    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    quo_tab.button[0].click().run()
    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    assert "Found 0 new message" in quo_tab.success[0].value


# -------------------------------------------- Durable Quo queue (Phase 1) --
def _setup_quo_stub(app, monkeypatch, message_id, ticket_text=None):
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
    text = ticket_text or QUO_AGN_TEXT
    inbound = QuoMessage(
        id=message_id, to="PNus", from_number=agn_phone, text=text,
        phone_number_id=real_config.our_phone_number_id, conversation_id="CONVDUR1", direction="incoming",
        status="delivered", created_at="2026-07-27T10:00:00Z", updated_at="2026-07-27T10:00:00Z",
    )
    monkeypatch.setattr(quo_ingest, "QuoClient", _fake_quo_client_factory(contacts_by_id, {agn_phone: [inbound]}))
    app.run()


def test_approving_a_quo_message_marks_its_durable_queue_row_actioned(app, monkeypatch):
    _setup_quo_stub(app, monkeypatch, "MSGACT1")

    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    quo_tab.button[0].click().run()  # "Poll Quo now"

    conn = storage.connect(storage.DB_PATH)
    assert storage.count_pending_quo_messages(conn) == 1

    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    quo_tab.button[1].click().run()  # "Load next Quo message into review"
    assert storage.count_pending_quo_messages(conn) == 1  # still counted -- now 'loaded'
    assert storage.get_pending_quo_messages(conn)[0]["status"] == "loaded"

    click_process_action(app, "Approve")

    assert storage.count_pending_quo_messages(conn) == 0  # now 'actioned'


def test_quo_pending_queue_survives_a_simulated_app_restart(app, monkeypatch):
    """The queue must be recoverable from a fresh app instance pointed at
    the same database file, without ever re-polling Quo -- proves the
    durable table, not st.session_state, is the source of truth."""
    _setup_quo_stub(app, monkeypatch, "MSGRESTART1")

    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    quo_tab.button[0].click().run()  # "Poll Quo now" -- content durably written

    # Simulate an app/laptop restart: a brand-new AppTest instance against
    # the same (still-monkeypatched) database path.
    st.cache_resource.clear()
    restarted = AppTest.from_file("app.py", default_timeout=30)
    restarted.run()
    assert len(restarted.exception) == 0

    quo_tab2 = restarted.tabs[QUO_LIVE_TAB_INDEX]
    assert any("1 message(s) queued for review" in md.value for md in quo_tab2.markdown)

    # Load it without re-polling -- proves the content itself (not just the
    # count) survived the restart.
    quo_tab2.button[1].click().run()
    process_tab = restarted.tabs[PROCESS_TAB_INDEX]
    assert any("AGN" in md.value for md in process_tab.markdown)
    click_process_action(restarted, "Approve")

    timeline = timeline_for("QLIVE1")
    assert len(timeline) == 1
    assert timeline[0]["raw_message"] == QUO_AGN_TEXT
    assert timeline[0]["channel"] == quo_ingest.SHADOW_MODE_CHANNEL


# ------------------------------------------------------------- Jobs tab ----
def test_jobs_tab_lists_one_row_per_job_and_masks_pii(app):
    select_scenario(app, "New job -- AGN")
    deliver_next(app)
    click_process_action(app, "Approve")  # SIM2AG, phone "555-010-5500", addr "600 Fake Blvd"

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    table = jobs_tab.table[0].value
    assert set(table.columns) == {
        "Source", "Ticket", "Status", "Created", "Last activity", "Latest action", "Assigned",
    }
    assert "SIM2AG" in table["Ticket"].values
    row = table[table["Ticket"] == "SIM2AG"].iloc[0]
    assert row["Source"] == "AGN"
    assert row["Status"] == "Approved"
    assert row["Latest action"] == "Approve"

    # Masking: no full phone number or street address anywhere in the list.
    table_text = table.to_string()
    assert "555-010-5500" not in table_text
    assert "600 Fake Blvd" not in table_text


def test_jobs_tab_status_reflects_handle_manually(app):
    select_scenario(app, "New job -- Lucky")
    deliver_next(app)
    click_process_action(app, "Handle Manually")

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    table = jobs_tab.table[0].value
    row = table[table["Ticket"] == "SIM3LK"].iloc[0]
    assert row["Status"] == "Handle Manually"


def test_jobs_tab_status_filter(app):
    select_scenario(app, "New job -- AGN")
    deliver_next(app)
    click_process_action(app, "Approve")

    select_scenario(app, "New job -- Lucky")
    deliver_next(app)
    click_process_action(app, "Handle Manually")

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    jobs_tab.selectbox(key="jobs_status_filter").select("Handle Manually").run()
    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    table = jobs_tab.table[0].value
    assert set(table["Ticket"].values) == {"SIM3LK"}


def test_jobs_tab_ticket_search_filter(app):
    select_scenario(app, "New job -- AGN")
    deliver_next(app)
    click_process_action(app, "Approve")

    select_scenario(app, "New job -- Lucky")
    deliver_next(app)
    click_process_action(app, "Handle Manually")

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    jobs_tab.text_input(key="jobs_ticket_search").set_value("SIM2AG").run()
    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    table = jobs_tab.table[0].value
    assert set(table["Ticket"].values) == {"SIM2AG"}


def test_jobs_tab_selecting_a_job_shows_its_full_unmasked_timeline(app):
    select_scenario(app, "New job -- AGN")
    deliver_next(app)
    click_process_action(app, "Approve")

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    select_box = jobs_tab.selectbox(key="jobs_selected_job")
    matching = [opt for opt in select_box.options if "SIM2AG" in opt]
    assert len(matching) == 1
    select_box.select(matching[0]).run()

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    jobs_tab.button(key="jobs_show_timeline").click().run()
    jobs_tab = app.tabs[JOBS_TAB_INDEX]

    # Full, unmasked detail in this authorized view -- same raw content the
    # Audit Timeline tab already shows for the same event.
    assert len(jobs_tab.json) == 1
    event = json.loads(jobs_tab.json[0].value)
    assert "600 Fake Blvd" in event["raw_message"]
    assert event["message_type"] == "NEW"
    assert event["final_action"] == "Approve"


# ------------------------------------------------------- Technicians tab ---
def add_technician_via_ui(at, name, areas="", skills="", vehicle_restrictions=""):
    tech_tab = at.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.text_input(key="tech_new_name").set_value(name).run()
    tech_tab = at.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.text_input(key="tech_new_areas").set_value(areas).run()
    tech_tab = at.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.text_input(key="tech_new_skills").set_value(skills).run()
    tech_tab = at.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.text_input(key="tech_new_vehicle_restrictions").set_value(vehicle_restrictions).run()
    tech_tab = at.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.button(key="tech_add_button").click().run()


def test_technicians_tab_add_and_list(app):
    add_technician_via_ui(app, "Alex", areas="Charlotte, Raleigh", skills="lockout")

    tech_tab = app.tabs[TECHNICIANS_TAB_INDEX]
    assert any("Added Alex" in s.value for s in tech_tab.success)
    table = tech_tab.table[0].value
    assert "Alex" in table["Name"].values
    row = table[table["Name"] == "Alex"].iloc[0]
    assert row["Active"] == "Yes"
    assert "Charlotte" in row["Service areas"] and "Raleigh" in row["Service areas"]
    assert row["Skills"] == "lockout"


def test_technicians_tab_rejects_duplicate_name(app):
    add_technician_via_ui(app, "Alex", areas="Charlotte")
    add_technician_via_ui(app, "Alex", areas="Raleigh")

    tech_tab = app.tabs[TECHNICIANS_TAB_INDEX]
    assert any("already exist" in e.value for e in tech_tab.error)
    table = tech_tab.table[0].value
    assert (table["Name"] == "Alex").sum() == 1  # not duplicated


def test_technicians_tab_requires_a_name(app):
    tech_tab = app.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.button(key="tech_add_button").click().run()
    tech_tab = app.tabs[TECHNICIANS_TAB_INDEX]
    assert any("Name is required" in e.value for e in tech_tab.error)


def test_technicians_tab_toggle_active(app):
    add_technician_via_ui(app, "Alex", areas="Charlotte")

    tech_tab = app.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.selectbox(key="tech_toggle_select").select("Alex").run()
    tech_tab = app.tabs[TECHNICIANS_TAB_INDEX]
    tech_tab.button(key="tech_toggle_button").click().run()

    tech_tab = app.tabs[TECHNICIANS_TAB_INDEX]
    assert any("now inactive" in s.value for s in tech_tab.success)
    table = tech_tab.table[0].value
    assert table[table["Name"] == "Alex"].iloc[0]["Active"] == "No"


# ------------------------------------------ Jobs tab: assign technician ----
def test_jobs_tab_assignment_is_blocked_when_job_area_is_not_in_the_real_mapping(app):
    """The simulator's AGN fixture uses city "Faketown", which isn't in
    the real rules/service_areas.yaml at all -- so even a technician whose
    configured area matches that city exactly must still come back Handle
    Manually (area lookup: not_found), never a silent success. This is
    correct, inherited behavior, not a gap in this feature."""
    select_scenario(app, "New job -- AGN")
    deliver_next(app)
    click_process_action(app, "Approve")

    add_technician_via_ui(app, "Alex", areas="Faketown")

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    select_box = jobs_tab.selectbox(key="jobs_selected_job")
    matching = [opt for opt in select_box.options if "SIM2AG" in opt]
    select_box.select(matching[0]).run()

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    jobs_tab.selectbox(key="jobs_assign_technician_select").select("Alex").run()
    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    jobs_tab.button(key="jobs_apply_assignment").click().run()

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    assert any("Handle Manually" in e.value for e in jobs_tab.error)
    timeline = timeline_for("SIM2AG")
    assignment_events = [e for e in timeline if e["message_type"] == "ASSIGNMENT"]
    assert len(assignment_events) == 1
    assert assignment_events[0]["final_action"] == "Handle Manually"


def test_jobs_tab_assignment_removal_always_allowed(app):
    select_scenario(app, "New job -- AGN")
    deliver_next(app)
    click_process_action(app, "Approve")

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    select_box = jobs_tab.selectbox(key="jobs_selected_job")
    matching = [opt for opt in select_box.options if "SIM2AG" in opt]
    select_box.select(matching[0]).run()

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    jobs_tab.selectbox(key="jobs_assign_technician_select").select("(remove assignment)").run()
    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    jobs_tab.button(key="jobs_apply_assignment").click().run()

    jobs_tab = app.tabs[JOBS_TAB_INDEX]
    assert any("Removed" in s.value for s in jobs_tab.success)
