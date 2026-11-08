"""The phone-first white redesign.

The owner works from an iPhone between jobs. The screen has to answer
one question -- WHAT DO I DO NEXT? -- so Jobs is the home screen and
everything else (Live Operations, Apprentice, technicians, health) moved
behind the bottom navigation.

Two properties get behavioural tests rather than string matching,
because string matching would prove the code contains a formatter, not
that the formatter is correct:

  * 12-hour rendering, exercised through the real function in Node
  * safety holds carrying no dispatch/copy action
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "operator_ui_v2_connected"
INDEX = UI / "index.html"
CSS = UI / "css" / "console.css"
JS = UI / "js" / "console.js"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def index_html():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css():
    return CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js():
    return JS.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """Source text of one top-level function in console.js."""
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(f"unterminated function {name}")


# =================================================== 12-hour clock =====
def test_one_shared_time_formatter(js):
    """One helper, not formatting logic scattered through the file."""
    assert "function formatDisplayTime" in js
    # Exactly one call to the platform formatter, inside that helper.
    assert js.count("toLocaleTimeString") == 1
    assert "toLocaleTimeString" in _function_body(js, "formatDisplayTime")
    # The old per-site formatter is gone.
    assert "function clockOf" not in js


def test_no_24_hour_formatting_survives(js):
    """`hour: "2-digit"` with no hour12 is how 21:50 reached the screen."""
    body = _function_body(js, "formatDisplayTime")
    assert "hour12: true" in body
    assert 'hour: "numeric"' in body
    assert '"2-digit"' not in body.split("hour:")[1].split(",")[0]


@needs_node
@pytest.mark.parametrize("iso,expected", [
    ("2026-08-16T21:50:20", "9:50 PM"),     # evening -- the reported case
    ("2026-08-16T00:15:00", "12:15 AM"),    # midnight is 12 AM, never 00
    ("2026-08-16T12:15:00", "12:15 PM"),    # noon is 12 PM, never 00
    ("2026-08-16T09:05:00", "9:05 AM"),
    ("2026-08-16T23:59:00", "11:59 PM"),
    ("2026-08-16T12:00:00", "12:00 PM"),
    ("2026-08-16T00:00:00", "12:00 AM"),
])
def test_twelve_hour_rendering_is_correct(iso, expected):
    """Runs the REAL helper. A local (not UTC) literal is used so the
    test asserts on the same instant the browser would render, without
    depending on the machine's zone."""
    assert _run_formatter(iso) == expected


@needs_node
def test_seconds_are_hidden_by_default_and_available_on_request():
    assert _run_formatter("2026-08-16T21:50:20") == "9:50 PM"
    assert _run_formatter("2026-08-16T21:50:20", seconds=True) == "9:50:20 PM"


@needs_node
@pytest.mark.parametrize("bad", ["", "not a date"])
def test_unusable_timestamps_render_as_nothing(bad):
    """Never "Invalid Date" on an operator's screen."""
    assert _run_formatter(bad) == ""


def _run_formatter(value, seconds=False):
    src = _function_body(JS.read_text(encoding="utf-8"), "formatDisplayTime")
    opts = '{ seconds: true }' if seconds else "undefined"
    script = f"{src}\nprocess.stdout.write(formatDisplayTime({json.dumps(value)}, {opts}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_display_only_no_backend_time_semantics_changed(js):
    """The helper must not be reachable from anything that orders or
    correlates. It formats and returns a string; that is all."""
    body = _function_body(js, "formatDisplayTime")
    for forbidden in ("fetch(", "Api.", "sort", "localeCompare", "setHours", "setUTC"):
        assert forbidden not in body, f"formatter must not {forbidden}"


def test_every_visible_clock_goes_through_the_helper(js):
    """Live Operations and the job timeline are the two places a wall
    clock is shown."""
    assert "formatDisplayTime(ev.time)" in js
    assert "formatDisplayTime(t)" in js          # timeline rows
    assert "formatDisplayTime(state.lastGood)" in js


# ================================================ information layers ===
def test_bottom_navigation_has_four_primary_screens(index_html):
    """Four destinations, not ten. Technicians, Apprentice, Shadow and
    System are tools reached from More -- they are not the daily job."""
    nav = index_html[index_html.index('id="tabbar"'):index_html.index("</nav>", index_html.index('id="tabbar"'))]
    for screen in ("today", "jobs", "reports", "more"):
        assert f'data-screen="{screen}"' in nav
    assert nav.count("data-screen=") == 4, "the primary nav must stay at four"
    for secondary in ("live", "apprentice", "techs", "shadow"):
        assert f'data-screen="{secondary}"' not in nav, f"{secondary} is not primary navigation"
        assert f'data-goto="{secondary}"' in index_html, f"{secondary} must be reachable from More"


def test_primary_navigation_precedes_every_screen_inside_sticky_chrome(index_html, css):
    """Desktop must not place the primary navigation after the job list.

    The same source-ordered nav remains fixed at the bottom on phones, but
    its desktop in-flow position is immediately below the header.
    """
    chrome_start = index_html.index('class="app-chrome"')
    chrome_end = index_html.index("</div>", index_html.index('id="offlineBar"'))
    chrome = index_html[chrome_start:chrome_end]
    assert chrome.index('class="topbar"') < chrome.index('id="tabbar"')
    assert chrome.index('id="tabbar"') < chrome.index('id="offlineBar"')
    assert index_html.index('id="tabbar"') < index_html.index('id="screenToday"')

    chrome_css = css[css.index(".app-chrome {"):css.index("}", css.index(".app-chrome {") + 1)]
    assert "position: sticky" in chrome_css and "top: 0" in chrome_css

    base_nav = css[css.index(".tabbar {"):css.index("}", css.index(".tabbar {") + 1)]
    assert "position: fixed" in base_nav and "bottom: 0" in base_nav

    desktop = css[css.index("@media (min-width: 769px)"):css.index("@media (max-width: 768px)")]
    desktop_nav = desktop[desktop.index(".tabbar {"):desktop.index("}", desktop.index(".tabbar {") + 1)]
    assert "position: static" in desktop_nav


def test_today_is_the_default_screen(index_html, js):
    """The owner opens Banna to see the business, not the dispatch board."""
    assert 'class="screen on" id="screenToday"' in index_html
    assert 'screen: "today"' in js
    assert "setScreen(state.screen)" in js


def test_live_and_apprentice_are_not_on_the_jobs_screen(index_html):
    """The defect this sprint fixes: telemetry and suggestions sharing
    the board with the two or three jobs that need a decision."""
    jobs = index_html[index_html.index('id="screenJobs"'):index_html.index('id="screenLive"')]
    for stray in ("loBody", "apBody", "healthbar", "hbQueue"):
        assert stray not in jobs, f"{stray} still renders on the Jobs screen"


def test_health_is_a_header_indicator_not_a_section(index_html):
    header = index_html[index_html.index("<header"):index_html.index("</header>")]
    for dot in ("dotCrm", "dotAdapter", "dotWa"):
        assert dot in header
    # The detailed health block lives on the More screen only.
    more = index_html[index_html.index('id="screenMore"'):]
    assert 'id="healthbar"' in more


def test_switching_screens_loads_that_screen_only(js):
    body = _function_body(js, "setScreen")
    for screen, loader in (("live", "loadLive()"), ("apprentice", "loadApprentice()"),
                           ("techs", "loadTechs()"), ("more", "renderMore()")):
        assert f'name === "{screen}"' in body and loader in body


# ======================================================== safety =======
def test_a_review_card_offers_no_send_or_copy(js):
    """A safety hold is shown so the operator knows a decision was made
    -- never so they can act on it blind."""
    body = _function_body(js, "reviewCard")
    for forbidden in ("copy-candidate", "copy-closing", 'data-act="dispatch"', "proposed_message"):
        assert forbidden not in body, f"review card exposes {forbidden}"
    # It may open its own detail; that is reading, not acting.
    assert 'data-act="review"' in body


def test_the_review_detail_bar_stays_empty(js):
    body = _function_body(js, "detailBar")
    assert 'if (col === "candidate-review") return "";' in body


def test_review_action_only_opens_the_detail(js):
    assert 'if (act === "review") {' in js
    handler = js[js.index('if (act === "review") {'):]
    assert 'openDetail("candidate-review"' in handler[:200]


def _strip_comments(source: str) -> str:
    """Comments describing a rule must not be mistaken for breaking it --
    a docstring saying "no approve control exists here" is evidence FOR
    the property, not against it."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def test_apprentice_stays_read_only(js):
    body = _strip_comments(_function_body(js, "loadApprentice"))
    for forbidden in ("approve", "activate", "Api.post", "data-act=\"ap-approve\""):
        assert forbidden.lower() not in body.lower(), f"apprentice exposes {forbidden}"
    assert "View evidence" in body


def test_actionable_candidates_still_work(js):
    """The positive control: if this fails, the safety assertions above
    are passing because nothing is actionable at all."""
    body = _function_body(js, "candidateCard")
    assert 'data-act="copy-candidate"' in body
    assert "Copy message" in body
    bar = _function_body(js, "detailBar")
    assert 'if (col === "dispatch")' in bar and 'data-act="dispatch"' in bar


def test_console_still_cannot_transmit(js):
    for forbidden in ("sendMessage", "whatsapp.send", "client.send", "quo_write"):
        assert forbidden not in js


# ======================================================== mobile =======
def _phone_block(source: str) -> str:
    start = source.index("@media (max-width: 768px)")
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError("unterminated phone block")


def test_bottom_nav_respects_the_home_indicator(css):
    tabbar = css[css.index(".tabbar {"):css.index("}", css.index(".tabbar {"))]
    assert "position: fixed" in tabbar
    assert "env(safe-area-inset-bottom)" in tabbar


def test_content_is_not_hidden_behind_the_bottom_nav(css):
    """Sticky navigation that covers the last card is the classic mobile
    defect -- the app reserves its height."""
    app = css[css.index(".app {"):css.index("}", css.index(".app {"))]
    assert "padding-bottom" in app
    assert "--tabbar-h" in app and "env(safe-area-inset-bottom)" in app


def test_touch_targets_stay_thumb_sized(css):
    phone = _phone_block(css)
    sizes = [int(m) for m in re.findall(r"min-height:\s*(\d+)px", phone)]
    assert sizes and min(sizes) >= 44, f"touch target below 44px: {sorted(sizes)}"
    tab = css[css.index(".tab {"):css.index("}", css.index(".tab {"))]
    assert int(re.search(r"min-height:\s*(\d+)px", tab).group(1)) >= 44


def test_nothing_scrolls_sideways(css):
    phone = _phone_block(css)
    assert re.search(r"html,\s*body\s*\{[^}]*overflow-x:\s*hidden", phone)
    assert re.search(r"\.board\s*\{\s*grid-template-columns:\s*1fr", phone)


def test_long_text_wraps_instead_of_overflowing(css):
    """Tickets, company names and addresses are arbitrary length."""
    for selector in (".card-title", ".card-line", ".rec-v"):
        # Anchor to the BASE rule: ".card.next .card-title" contains the
        # same substring and would satisfy this check without the base
        # rule ever setting a wrap.
        start = css.index("\n" + selector + " {") + 1
        block = css[start:css.index("}", start)]
        assert "overflow-wrap: anywhere" in block, f"{selector} can overflow"
    ticket = css[css.index(".ticket {"):css.index("}", css.index(".ticket {"))]
    assert "text-overflow: ellipsis" in ticket


def test_no_card_is_wider_than_the_viewport(css):
    """No fixed pixel widths inside the phone block."""
    phone = _phone_block(css)
    widths = re.findall(r"[^-]width:\s*(\d+)px", phone)
    assert not widths, f"fixed widths on a phone: {widths}"


def test_the_detail_is_full_screen_on_a_phone(css):
    phone = _phone_block(css)
    detail = phone[phone.index(".detail {"):phone.index("}", phone.index(".detail {"))]
    assert "width: 100%" in detail and "top: 0" in detail


def test_small_phones_are_handled(css):
    """390px (iPhone 14) and 430px (Pro Max) both fall inside the phone
    block; the narrowest layout gets an extra tightening pass."""
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 380px)" in css


# ================================= OPERATING LINE / NEXT ACTION =======
def test_the_first_actionable_job_is_promoted_to_next_action(js):
    body = _function_body(js, "render")
    assert "Next action" in body
    assert "Up next" in body
    assert 'class="flow-h next"' in body


def test_next_action_does_not_reorder_the_backend_queue(js):
    """The UI may EMPHASISE the first item; it may not decide which item
    that is. A sort here would silently invent a dispatch priority the
    business never agreed to."""
    body = _strip_comments(_function_body(js, "render"))
    flow = body[body.index("const actionable"):]
    for forbidden in (".sort(", "localeCompare", "priority", "score", "urgency"):
        assert forbidden not in flow, f"the operating line invents order via {forbidden}"
    # It concatenates the two existing lists in the order they arrived.
    assert "[].concat(" in flow
    assert "actionable[0]" in flow and "actionable.slice(1)" in flow


def test_review_holds_are_never_the_next_action(js):
    """A safety hold is not work the operator can do -- promoting one to
    NEXT ACTION would present a blocked job as the next move."""
    body = _function_body(js, "render")
    flow = body[body.index("const actionable"):body.index("if (reviews.length)")]
    assert "candidateReviews" not in flow
    assert "reviews" not in flow.replace("reviews.length", "")


def test_an_empty_queue_says_so_without_inventing_work(js):
    assert "No jobs need action right now" in js


def test_next_action_has_the_stronger_hierarchy(css):
    assert ".card.next" in css
    block = css[css.index(".card.next {"):css.index("}", css.index(".card.next {"))]
    assert "box-shadow" in block or "border-color" in block


# ==================================== DISPLAY LABELS (presentation) ===
def test_machine_enums_are_translated_for_display_only(js):
    assert "function displayLabel" in js
    body = _function_body(js, "displayLabel")
    # It reads a lookup and returns a string. It must never write, send,
    # or compare against the backend using the translated value.
    for forbidden in ("Api.", "fetch(", "POST", "localStorage"):
        assert forbidden not in body, f"display mapping must not {forbidden}"


@pytest.mark.parametrize("machine,human", [
    ("HUMAN_REVIEW_NEEDED", "Needs human review"),
    ("COMPANY UNRESOLVED", "Company needs confirmation"),
    ("ORDER UNDETERMINED", "Message order unclear"),
])
def test_the_required_label_translations_exist(js, machine, human):
    table = js[js.index("const DISPLAY_LABELS"):js.index("function displayLabel")]
    assert machine in table and human in table


def test_unknown_backend_values_still_render(js):
    """A new backend state must degrade to something readable, never
    disappear from the operator's screen."""
    body = _function_body(js, "displayLabel")
    assert "return raw" in body or "raw.charAt(0)" in body


def test_backend_values_are_not_rewritten_anywhere(js):
    """Translation happens at the point of display. The underlying
    status/review_reason fields are still the machine values, so
    filtering and safety checks keep comparing against the real enum."""
    assert 'c.status === "WAITING FOR HUMAN DISPATCH"' in js
    assert "(c) => c.review_reason" in js


# ============================================= BOTTOM NAV ICONS =======
def test_bottom_nav_uses_real_svg_icons(index_html):
    nav = index_html[index_html.index('class="tabbar"'):index_html.index("</nav>", index_html.index('class="tabbar"'))]
    assert nav.count("<svg") == 4, "every tab needs a real icon"
    for placeholder in ("&#9632;", "&#9679;", "&#9670;", "&#9650;", "&#8943;"):
        assert placeholder not in nav, "geometric placeholder survived"
    # No emoji, no external icon font.
    assert "font-awesome" not in index_html.lower() and "material-icons" not in index_html.lower()


def test_every_tab_is_labelled_for_assistive_tech(index_html):
    nav = index_html[index_html.index('class="tabbar"'):index_html.index("</nav>", index_html.index('class="tabbar"'))]
    assert nav.count("aria-label=") == 4
    assert nav.count('aria-hidden="true"') == 4


def test_the_active_tab_uses_a_soft_pill(css):
    block = css[css.index(".tab.on {"):css.index("}", css.index(".tab.on {"))]
    assert "var(--blue)" in block
    assert "var(--blue-soft)" in block, "active tab must be a soft pill, not a solid block"


# ================================================= COLOUR SYSTEM ======
def test_the_day_theme_is_a_light_neutral_with_white_cards(css):
    root = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    assert "--bg:        #F2F4F7" in root, "light neutral page"
    assert "--surface:   #FFFFFF" in root, "white cards"


def test_the_specified_accents_are_used(css):
    root = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    assert "#E2663B" in root, "day accent"
    assert "#15803D" in root, "success green"
    assert "#C2410C" in root, "warning amber"
    assert "#B91C1C" in root, "danger red"


# ============================================ day / night themes ======
def test_the_theme_follows_the_operational_clock_not_the_device(js):
    """The laptop runs UTC+3 while the business runs on America/New_York.
    A device-local switch would turn the screen dark in the middle of
    the shift, so the theme reads the same clock the CRM reports on."""
    body = _function_body(js, "shiftHour")
    assert '"America/New_York"' in body
    assert "Intl.DateTimeFormat" in body
    assert "getTimezoneOffset" not in body


def test_day_starts_before_eight_so_the_shift_opens_bright(js):
    assert "DAY_STARTS_HOUR = 6" in js
    assert "NIGHT_STARTS_HOUR = 18" in js
    body = _function_body(js, "themeForHour")
    assert "DAY_STARTS_HOUR" in body and "NIGHT_STARTS_HOUR" in body


@needs_node
@pytest.mark.parametrize("iso,expected", [
    ("2026-11-08T10:30:00Z", "night"),   # 05:30 ET, still dark
    ("2026-11-08T11:30:00Z", "day"),     # 06:30 ET, day has begun
    ("2026-11-08T12:45:00Z", "day"),     # 07:45 ET, shift start -> bright
    ("2026-11-08T22:30:00Z", "day"),     # 17:30 ET
    ("2026-11-08T23:30:00Z", "night"),   # 18:30 ET
    ("2026-11-09T04:00:00Z", "night"),   # 23:00 ET
])
def test_theme_boundaries_run_correctly(iso, expected):
    """Executes the real functions, so a timezone mistake fails here."""
    src = _function_body(JS.read_text(encoding="utf-8"), "shiftHour")
    src += "\n" + _function_body(JS.read_text(encoding="utf-8"), "themeForHour")
    src += ("\nconst DAY_STARTS_HOUR = 6, NIGHT_STARTS_HOUR = 18;"
            "\nprocess.stdout.write(themeForHour(shiftHour(new Date(%r))));" % iso)
    out = subprocess.run([NODE, "-e", src.replace("'", '"', 0)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


def test_the_theme_is_re_evaluated_during_a_long_shift(js):
    """A dispatcher who opens the app at 5pm must not still be on the
    day theme at midnight."""
    assert "setInterval(applyTheme" in js


def test_the_browser_chrome_follows_the_theme(js):
    body = _function_body(js, "applyTheme")
    assert 'meta[name="theme-color"]' in body
    assert "#11151B" in body


def test_cards_separate_from_the_page(css):
    block = css[css.index(".card {"):css.index("}", css.index(".card {"))]
    assert "box-shadow" in block and "border" in block


# ================================================= SHELL VERSION ======
def test_the_shell_cache_matches_the_asset_version():
    index = INDEX.read_text(encoding="utf-8")
    sw = (UI / "sw.js").read_text(encoding="utf-8")
    asset = re.search(r"console\.js\?v=(\d+)", index).group(1)
    cache = re.search(r'SHELL_VERSION = "[a-z]+-shell-v(\d+)"', sw).group(1)
    assert asset == cache, "a stale worker would keep serving the old build"
