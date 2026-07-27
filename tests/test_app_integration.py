"""End-to-end integration tests driving app.py itself (not just the
src/dispatcher modules in isolation) via streamlit.testing.v1.AppTest, using
the fake-inbox simulator tab to exercise every conversation scenario.

Each test isolates its own SQLite database (monkeypatched storage.DB_PATH +
st.cache_resource.clear()) so runs never touch the real data/dispatcher.db
and never leak cached connections between tests.
"""

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


def test_cancellation_worded_differently_is_a_documented_gap(app):
    """Known, accepted gap: CANCELLATION detection is a substring check for
    the literal word "cancel". Several real messages hit during manual
    testing 2026-07-27 ("no longer needs service", "found her keys...")
    never say "cancel" and fall through to UNRECOGNIZED today."""
    select_scenario(app, "Customer cancellation -- worded differently (known gap)")
    deliver_next(app)
    click_process_action(app, "Approve")
    deliver_next(app)
    click_process_action(app, "Approve")

    timeline = timeline_for("SIM8CW")
    assert [e["message_type"] for e in timeline] == ["NEW", "UNRECOGNIZED"]


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

    # Polling again with the same stub (same message) must not re-queue it.
    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    quo_tab.button[0].click().run()
    quo_tab = app.tabs[QUO_LIVE_TAB_INDEX]
    assert "Found 0 new message" in quo_tab.success[0].value
