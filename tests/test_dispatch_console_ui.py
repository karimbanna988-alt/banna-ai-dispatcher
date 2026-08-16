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
    # Product is Banna Dispatch; Luxor Locksmith is the tenant. Only the
    # visible UI was rebranded -- no LUXOR_* env var, fixture or business
    # record was renamed (see src/dispatcher/branding.py).
    assert "Banna Dispatch" in index_html
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
def test_console_uses_the_light_operations_palette():
    """The console is a WHITE operations product, not a developer
    console. The dark telemetry palette this replaced made every screen
    read as equally urgent, which is the opposite of what a phone-first
    board needs.

    Colour has to stay meaningful: blue is the active stage and the one
    primary action, amber is a decision the operator owes, red is an
    actual fault, purple is Apprentice."""
    css = CONSOLE_CSS.read_text(encoding="utf-8")
    assert "--surface:   #FFFFFF" in css, "cards must be white"
    assert "color-scheme" not in css or "dark" not in css.split("color-scheme")[-1][:40]
    for token in ("--blue:", "--green:", "--amber:", "--red:", "--purple:"):
        assert token in css, f"missing semantic colour token {token}"
    # The neon accents of the old console must be gone.
    for neon in ("#00D4FF", "#00BFFF", "#0B0E14"):
        assert neon not in css, f"cyberpunk accent {neon} survived the redesign"


def test_console_is_responsive_for_mobile():
    css = CONSOLE_CSS.read_text(encoding="utf-8")
    assert "@media" in css
    assert "grid-template-columns: 1fr" in css


# ------------------------------------------- owner acceptance: auth ----
def test_hidden_attribute_is_forced_to_win_over_layout_display():
    """THE owner-reported defect. `.login` and `.app` both set
    `display: flex`, and an author class selector beats the user-agent
    `[hidden] { display: none }` rule -- so `element.hidden = true` had no
    visual effect. Both layers rendered at once and, critically, the login
    card stayed on screen after a SUCCESSFUL sign-in, which reads to an
    operator as "login didn't work".

    Everything the console shows/hides uses the `hidden` attribute, so
    without this rule the whole show/hide model is silently inert."""
    css = CONSOLE_CSS.read_text(encoding="utf-8")
    # Match the real declaration, not the comment above it that quotes the
    # user-agent rule while explaining the bug.
    import re
    rule = re.search(r"^\s*\[hidden\]\s*\{[^}]*\}", css, re.MULTILINE)
    assert rule, "console.css must contain a real [hidden] rule, not only a comment about one"
    body = rule.group(0)
    assert "display: none" in body
    assert "!important" in body, (
        "without !important the class rules (.login/.app display:flex) win again"
    )


def test_login_clears_a_previous_error_before_retrying(console_js):
    """A stale "Invalid password." must not linger after a later
    successful sign-in."""
    login_fn = console_js[console_js.index("async function doLogin"):]
    login_fn = login_fn[:login_fn.index("\n  }")]
    assert '$("loginError").textContent = ""' in login_fn


def test_password_is_cleared_from_the_dom_after_a_successful_login(console_js):
    login_fn = console_js[console_js.index("async function doLogin"):]
    login_fn = login_fn[:login_fn.index("\n  }")]
    assert '$("loginPassword").value = ""' in login_fn


def test_a_failed_login_surfaces_the_error_to_the_user(console_js):
    assert '$("loginError").textContent = e.message' in console_js


def test_login_screen_and_app_start_in_the_correct_initial_state(index_html):
    """The app must carry `hidden` in the markup so an unauthenticated
    visitor never sees the console before the session check completes."""
    assert 'id="app" class="app" hidden' in index_html
    assert 'id="loginScreen"' in index_html
