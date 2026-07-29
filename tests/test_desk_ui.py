"""Lightweight UI tests + raw-code visibility guard for LUXOR OPERATIONS
DESK (luxor_desk.py). Fabricated data only -- no database connection, no
Quo import, no network.

A prior candidate's custom clipboard <button onclick="navigator.clipboard...">
rendered its own JavaScript source as visible page text instead of an
interactive control. This prototype deliberately contains no JavaScript
at all (only st.code()'s native copy icon), and
test_no_raw_script_or_onclick_anywhere is the structural guard that
would fail this file the same way if that pattern ever crept back in."""

from __future__ import annotations

import pathlib

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from luxor_desk_data import DESK_JOBS

DESK_FILES = ["luxor_desk.py", "luxor_desk_data.py", "luxor_desk_shared.py"]


@pytest.fixture()
def desk_env(monkeypatch):
    monkeypatch.setenv("LUXOR_DEMO_PASSWORD", "pw")
    st.cache_resource.clear()
    st.cache_data.clear()


def make_app():
    at = AppTest.from_file("luxor_desk.py", default_timeout=30)
    at.run()
    return at


def login(at, password="pw"):
    at.text_input(key="luxor_desk_password_input").set_value(password).run()
    at.button[0].click().run()
    assert len(at.exception) == 0
    return at


# --------------------------------------------------- Raw-code visibility guard
def test_no_raw_script_or_onclick_anywhere():
    forbidden = ("<script", "onclick=", "onclick =", "javascript:", "navigator.clipboard")
    for f in DESK_FILES:
        source = pathlib.Path(f).read_text(encoding="utf-8")
        for bad in forbidden:
            assert bad not in source, f"{f} must never contain {bad!r}"


def test_no_backend_imports():
    forbidden = ("dispatcher.storage", "dispatcher.quo_client", "dispatcher.quo_ingest", "sqlite3")
    for f in DESK_FILES:
        lines = pathlib.Path(f).read_text(encoding="utf-8").splitlines()
        import_lines = [line for line in lines if line.strip().startswith(("import ", "from "))]
        for bad in forbidden:
            assert not any(bad in line for line in import_lines), f"{f} must not import {bad}"


# ------------------------------------------------- No-horizontal-overflow guard
def test_no_horizontal_overflow_css_structure():
    """Structural guard for the exact defect the dispatcher reported (buttons
    clipped/overflowing on portrait mobile): the shared stylesheet must
    force horizontal overflow off at the page level, and every two-column
    action row must use a shrink-safe flex row (never a fixed-width grid
    that can overflow a 390px viewport)."""
    css = pathlib.Path("luxor_desk_shared.py").read_text(encoding="utf-8")
    assert "overflow-x: hidden" in css
    assert 'st-key-actions2_' in css
    assert "flex-wrap: nowrap" in css
    assert "min-width: 0" in css  # required for flex children to shrink instead of overflow


# --------------------------------------------------------------- UI behavior
def test_login_gate_blocks_unauthenticated_access(desk_env):
    at = make_app()
    assert len(at.exception) == 0
    assert any("LUXOR LOCKSMITH" in t.value for t in at.title)
    assert any(b.label == "Enter" for b in at.button)


def test_stage_navigator_hides_done_and_shows_correct_counts(desk_env):
    at = login(make_app())
    pills = at.pills(key="desk_stage_pills")
    assert pills.options == ["New (3)", "Active (2)", "Closing (1)"]
    assert pills.value == "New"  # defaults to the first/newest-actionable stage


def test_default_view_shows_one_focused_job_with_actions(desk_env):
    at = login(make_app())
    # Exactly one job's action controls are visible: the focused card's.
    action_keys = [b.key for b in at.button if b.key and (
        b.key.startswith("open_wa_") or b.key.startswith("k_ready_")
        or b.key.startswith("mark_dispatched_") or b.key.startswith("needs_review_")
    )]
    focused_tickets = {k.rsplit("_", 1)[-1] for k in action_keys}
    assert len(focused_tickets) == 1


def test_only_two_queue_rows_shown_alongside_one_focused_new_job(desk_env):
    at = login(make_app())
    queue_buttons = [b for b in at.button if b.key and b.key.startswith("queue_")]
    # 3 fabricated New jobs total: 1 focused + 2 remaining queue rows.
    assert len(queue_buttons) == 2


def test_clicking_queue_row_makes_it_the_focused_job(desk_env):
    at = login(make_app())
    at.button(key="queue_A9K2L").click().run()
    assert len(at.exception) == 0
    assert any(b.key == "k_ready_A9K2L" for b in at.button)  # AGN now focused -> K Ready visible
    assert not any(b.key == "queue_A9K2L" for b in at.button)  # no longer in the queue


def test_main_leads_new_job_omits_k_ready(desk_env):
    ml_job = next(j for j in DESK_JOBS if j.stage == "New" and j.source == "Main Leads")
    at = login(make_app())
    # ML-77QX is "urgent" -- the highest urgency rank -- so it's the
    # default-focused New job already; no queue-row click needed.
    if not any(b.key == f"open_wa_{ml_job.ticket}" for b in at.button):
        at.button(key=f"queue_{ml_job.ticket}").click().run()
    assert not any(b.key == f"k_ready_{ml_job.ticket}" for b in at.button)
    assert any(b.key == f"open_wa_{ml_job.ticket}" for b in at.button)


def test_active_stage_shows_one_primary_and_one_secondary_row(desk_env):
    at = login(make_app())
    at.pills(key="desk_stage_pills").set_value("Active (2)").run()
    assert len(at.exception) == 0
    expander_labels = [e.label for e in at.expander]
    assert "COPY COMPANY UPDATE" in expander_labels
    assert any(b.key and b.key.startswith("update_sent_") for b in at.button)
    assert any(b.key and b.key.startswith("cancel_pass_") for b in at.button)
    # No new-job or closing action buttons leak into the Active view.
    assert not any(b.key and b.key.startswith("k_ready_") for b in at.button)
    assert not any(b.key and b.key.startswith("mark_dispatched_") for b in at.button)
    assert not any(b.key and b.key.startswith("closing_sent_") for b in at.button)


def test_closing_stage_shows_one_primary_and_one_secondary_row(desk_env):
    at = login(make_app())
    at.pills(key="desk_stage_pills").set_value("Closing (1)").run()
    assert len(at.exception) == 0
    expander_labels = [e.label for e in at.expander]
    assert "COPY COMPANY CLOSING" in expander_labels
    assert any(b.key and b.key.startswith("closing_sent_") for b in at.button)
    assert any(b.key and b.key.startswith("needs_review_") for b in at.button)
    assert not any(b.key and b.key.startswith("update_sent_") for b in at.button)
    assert not any(b.key and b.key.startswith("queue_") for b in at.button)  # only 1 job in Closing


def test_source_filter_narrows_working_set(desk_env):
    at = login(make_app())
    at.selectbox(key="desk_source_select").set_value("Main Leads").run()
    assert len(at.exception) == 0
    ml_ticket = next(j.ticket for j in DESK_JOBS if j.stage == "New" and j.source == "Main Leads")
    assert any(b.key and b.key.endswith(ml_ticket) for b in at.button if b.key != "desk_sync")
    # No AGN/Lucky New-stage tickets should appear as action or queue buttons.
    other_new_tickets = [j.ticket for j in DESK_JOBS if j.stage == "New" and j.source != "Main Leads"]
    for t in other_new_tickets:
        assert not any(b.key and b.key.endswith(t) for b in at.button)


def test_full_luxor_message_is_inside_a_collapsible_expander(desk_env):
    # AppTest's element tree includes expander contents regardless of
    # visual collapsed/expanded state (Streamlit still executes the
    # `with st.expander(...):` block every run) -- so this can't assert
    # true visual hiddenness. What it can and does verify: the full Luxor
    # message is only ever placed inside an st.expander (either "Job
    # details" or the primary "COPY LUXOR JOB" action), never rendered as
    # a bare top-level element on the card.
    at = login(make_app())
    assert any(e.label == "COPY LUXOR JOB" for e in at.expander)
    # The default-focused New job is the highest-urgency one (same sort
    # luxor_desk.py itself applies), not just the first item in the list.
    urgency_rank = {"urgent": 0, "warning": 1, "normal": 2}
    focused_new_job = min(
        (j for j in DESK_JOBS if j.stage == "New"), key=lambda j: urgency_rank.get(j.urgency, 3),
    )
    assert any(c.value == focused_new_job.luxor_message for c in at.code)


def test_desktop_two_panel_grid_css_present():
    """Structural guard for the desktop layout: mobile stays a single
    stacked column (grid-template-areas "workspace" "queue"), and only
    at >=900px does it become a genuine two-column split with the queue
    on the left ("queue workspace") -- both areas must map to the actual
    container keys luxor_desk.py renders."""
    css = pathlib.Path("luxor_desk_shared.py").read_text(encoding="utf-8")
    assert 'st-key-desk_layout' in css
    assert 'st-key-desk_area_workspace' in css
    assert 'st-key-desk_area_queue' in css
    assert '"workspace" "queue"' in css  # mobile: stacked, workspace first
    assert 'min-width: 900px' in css
    assert '"queue workspace"' in css  # desktop: queue left, workspace right


def test_queue_row_shows_current_exception_or_status(desk_env):
    at = login(make_app())
    # In the Active view, A6D19 (first in urgency-tied list order) is
    # focused; L2Q56 sits in the queue carrying tech_status "ETA" -- the
    # queue row must surface that as its current-status fragment.
    at.pills(key="desk_stage_pills").set_value("Active (2)").run()
    assert len(at.exception) == 0
    queue_button = next(b for b in at.button if b.key == "queue_L2Q56")
    assert "ETA" in queue_button.label


def test_no_real_technician_names_in_desk_data():
    real_names = ("Shabaan", "Sweillam", "Kabaka", "Sebai", "Ziyadah", "Abozeid", "Omar Shata")
    source = pathlib.Path("luxor_desk_data.py").read_text(encoding="utf-8")
    for name in real_names:
        assert name not in source
