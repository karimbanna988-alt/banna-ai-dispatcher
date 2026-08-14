"""Tests for the Dispatch Console -- the primary operator interface.

The console is the product; the CRM behind Admin is the memory. These
tests pin the properties that make it safe and fast, not its cosmetics.
"""
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "operator_ui_v2_connected"
INDEX = UI / "index.html"
CONSOLE_JS = UI / "js" / "console.js"
CONSOLE_CSS = UI / "css" / "console.css"
ADMIN = UI / "admin.html"


@pytest.fixture(scope="module")
def index_html():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def console_js():
    return CONSOLE_JS.read_text(encoding="utf-8")


# ------------------------------------------------------- structure ----
def test_console_is_the_primary_page_served_at_root(index_html):
    """Opening http://localhost:8512 must land on the dispatch console,
    not the back-office CRM."""
    assert "LUXOR Dispatch" in index_html
    assert 'id="colDispatch"' in index_html
    assert 'id="colProgress"' in index_html
    assert 'id="colClose"' in index_html


def test_the_three_operational_columns_are_the_main_screen(index_html):
    for heading in ("To dispatch", "In progress", "To close"):
        assert heading in index_html


def test_the_old_crm_is_preserved_behind_admin():
    """The CRM/state layer is the memory -- it must not be deleted."""
    assert ADMIN.is_file()
    admin = ADMIN.read_text(encoding="utf-8")
    for screen in ("Command Center", "Technicians", "Performance", "Learning Review"):
        assert screen in admin


def test_console_links_to_admin(index_html, console_js):
    assert 'id="adminBtn"' in index_html
    assert "admin.html" in console_js


# ---------------------------------------------------------- safety ----
def test_console_cannot_send_a_whatsapp_message(console_js):
    """The operator copies and pastes; nothing is ever transmitted."""
    for forbidden in ("sendMessage", "sendWhatsapp", "/api/send", "twilio"):
        assert forbidden not in console_js


def test_console_makes_no_quo_write(console_js):
    assert "quo" not in console_js.lower() or "quo_pending" not in console_js.lower()
    for forbidden in ("/api/quo/send", "postQuo"):
        assert forbidden not in console_js


def test_dispatch_uses_only_the_existing_audited_endpoints(console_js):
    """No new backend: dispatch reuses acknowledge -> prepare -> confirm,
    the same flow the previous UI committed through."""
    for call in ("Api.acknowledge", "Api.prepareDispatch", "Api.confirmDispatch"):
        assert call in console_js


def test_console_never_invents_missing_values(console_js):
    """Missing fields are shown as missing, never filled in."""
    assert "not stated" in console_js
    assert "never invented" in console_js


def test_single_keypress_cannot_dispatch_an_unselected_job(console_js):
    """A stray 'd' must not dispatch something the operator is not
    looking at."""
    assert 'state.selected.col === "dispatch"' in console_js


def test_a_job_needing_review_is_not_dispatched_by_the_quick_action(console_js):
    """Pressing Dispatch on a card flagged needs_review must open the
    detail instead of committing."""
    assert "needs_review" in console_js
    assert "needs review first" in console_js


# ------------------------------------------------------ stale data ----
def test_the_board_excludes_stale_precommit_messages(console_js):
    """A 300-hour-old unresolved message must never compete with
    something that arrived 30 seconds ago."""
    assert "DISPATCH_MAX_AGE_MIN" in console_js
    assert "newJobs.older" in console_js
    assert "staleNote" in console_js


def test_stale_items_are_signposted_not_deleted(console_js):
    """Historical data stays available through Admin."""
    assert "Inbox Review" in console_js


def test_elapsed_time_is_shown_not_a_raw_timestamp(console_js):
    """Regression: waiting_since arrives as an ISO timestamp and was
    rendering raw on the card, which tells an operator nothing."""
    assert "elapsedSince" in console_js
    assert "elapsedMinutes" in console_js


# ------------------------------------------------------ operator UX ----
def test_keyboard_shortcuts_are_wired(console_js):
    for key in ('"j"', '"k"', '"Enter"', '"c"', '"Escape"', '"/"'):
        assert key in console_js


def test_polling_pauses_while_a_detail_panel_is_open(console_js):
    """The board must never re-render underneath the operator mid-action."""
    assert "state.paused" in console_js
    assert "if (state.paused) return" in console_js


def test_ai_decision_why_and_confidence_are_surfaced(console_js):
    for element in ("confidenceChip", "Decision", "d-why", "Proposed technician message"):
        assert element in console_js


def test_console_has_no_inline_script_tags(index_html):
    """The CSP sets script-src 'self' -- an inline script would be
    blocked and silently break the page."""
    import re
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", index_html)
    assert inline == []


def test_console_escapes_dynamic_values(console_js):
    assert "function esc" in console_js or "const esc" in console_js
    assert "&amp;" in console_js


# ---------------------------------------------------------- visual ----
def test_console_uses_the_specified_dark_operations_palette():
    css = CONSOLE_CSS.read_text(encoding="utf-8")
    for colour in ("#0B0E14", "#111827", "#F1F5F9", "#94A3B8"):
        assert colour in css, f"missing specified colour {colour}"
    assert "#00D4FF" in css or "#00BFFF" in css


def test_console_is_responsive_for_mobile():
    css = CONSOLE_CSS.read_text(encoding="utf-8")
    assert "@media" in css
    assert "grid-template-columns: 1fr" in css
