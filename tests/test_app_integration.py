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

from dispatcher import storage

SIM_TAB_INDEX = 5
PROCESS_TAB_INDEX = 0
STATUS_TAB_INDEX = 2
CLOSING_TAB_INDEX = 3


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
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
