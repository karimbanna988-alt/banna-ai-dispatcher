"""Focused tests for the "light premium CRM UX" pass over
operator_ui_v2_connected/. These are structural/text checks against the
static CSS/HTML/JS source (this frontend has no JS test runner) --
runtime behavior (caps actually apply, no horizontal scroll at 5
viewports, DEMO MODE banner renders) was verified separately in a
browser against the isolated demo server, not the real database, and
is not repeated here. No test in this file touches any database, sends
any network request, or requires authentication.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
UI_DIR = REPO_ROOT / "operator_ui_v2_connected"


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (UI_DIR / "css" / "styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def connected_css() -> str:
    return (UI_DIR / "css" / "connected.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (UI_DIR / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (UI_DIR / "admin.html").read_text(encoding="utf-8")


# --------------------------------------------------------- design tokens --
REQUIRED_TOKENS = [
    "--bg", "--surface", "--panel-bg", "--border",
    "--text", "--text-muted",
    "--header-navy", "--primary", "--accent-teal",
    "--success", "--warning", "--danger",
]


def test_all_required_design_tokens_are_declared(styles_css):
    root_block = re.search(r":root\s*\{(.*?)\}", styles_css, re.DOTALL).group(1)
    for token in REQUIRED_TOKENS:
        assert re.search(rf"{re.escape(token)}\s*:", root_block), f"missing token {token}"


def test_no_dark_page_background():
    """The page/login background must be a light token, never a dark
    navy/black literal -- regression guard for the old dark login
    gradient this round replaced."""
    connected_css_text = (UI_DIR / "css" / "connected.css").read_text(encoding="utf-8")
    login_block = re.search(r"\.login-screen\s*\{(.*?)\}", connected_css_text, re.DOTALL).group(1)
    assert "gradient" not in login_block
    assert "var(--bg)" in login_block


def test_no_hardcoded_status_color_literals_remain(styles_css, connected_css):
    """Status colors (success/warning) should be tokens, not scattered
    hex literals -- regression guard against the ad hoc #1e6b3a /
    #7a6006 / #d9a91a literals this round consolidated. The one
    legitimate occurrence of each is its own :root token definition."""
    styles_body = re.sub(r":root\s*\{.*?\}", "", styles_css, count=1, flags=re.DOTALL)
    for literal in ("#1e6b3a", "#7a6006", "#d9a91a"):
        assert literal not in styles_body
        assert literal not in connected_css


# --------------------------------------------------- Command Center order --
def test_command_center_section_order_in_html(index_html):
    """Summary first: KPI grid, then closed-amount banner, then
    Attention Now, then Active jobs, then Charts -- in that order."""
    cc_start = index_html.index('id="screen-commandcenter"')
    cc_end = index_html.index("</section>", cc_start)
    section = index_html[cc_start:cc_end]
    order = ["ccKpiGrid", "ccClosedAmount", "ccAttentionSection", "ccActiveTable", "cc-charts-grid"]
    positions = [section.index(marker) for marker in order]
    assert positions == sorted(positions), f"Command Center sections out of order: {list(zip(order, positions))}"


def test_command_center_has_exactly_six_kpi_cards_wired(app_js):
    grid_fn = re.search(r'grid\.innerHTML = \[(.*?)\]\.join', app_js, re.DOTALL).group(1)
    card_calls = re.findall(r"ccKpiCardHtml\(", grid_fn)
    assert len(card_calls) == 6


def test_command_center_has_exactly_three_charts(index_html):
    cc_start = index_html.index('id="screen-commandcenter"')
    cc_end = index_html.index("</section>", cc_start)
    section = index_html[cc_start:cc_end]
    assert section.count('class="perf-card cc-chart-card"') == 3
    assert "Verified closings" not in section, "the 4th chart (redundant with the closed-amount banner) should be removed"


# --------------------------------------------------------------- caps -----
def test_attention_now_capped_at_five(app_js):
    assert "const CC_ATTENTION_MAX = 5;" in app_js
    assert "dashboard.attention_now.slice(0, CC_ATTENTION_MAX)" in app_js


def test_active_board_capped_at_six(app_js):
    assert "const CC_ACTIVE_BOARD_MAX = 6;" in app_js
    assert ".slice(0, CC_ACTIVE_BOARD_MAX)" in app_js


def test_attention_view_all_link_exists_for_overflow(index_html, app_js):
    assert 'id="ccAttentionViewAllWrap"' in index_html
    assert "attentionViewAllWrap.style.display" in app_js


def test_donut_chart_caps_categories_at_six(app_js):
    fn = re.search(r"function ccDonutChartSvg\(counts\) \{(.*?)\n\}", app_js, re.DOTALL).group(1)
    assert "entries.length > 6" in fn
    assert '"Other"' in fn


# -------------------------------------------------------------- no PII ----
PII_FIELDS = ["j.phone", "j.address", "c.phone", "c.address", "customer_phone", "customer_address"]


def test_command_center_render_functions_never_reference_pii_fields(app_js):
    cc_start = app_js.index("/* ===================================================== COMMAND CENTER ===")
    cc_end = app_js.index("/* ------------------------------------------------------------- Charts")
    section = app_js[cc_start:cc_end]
    for field in PII_FIELDS:
        assert field not in section, f"{field} referenced in Command Center render code"


def test_active_screen_table_columns_never_include_phone_or_address(index_html):
    active_start = index_html.index('id="screen-active"')
    active_end = index_html.index("</section>", active_start)
    section = index_html[active_start:active_end]
    thead = re.search(r"<thead>(.*?)</thead>", section, re.DOTALL).group(1)
    assert "Phone" not in thead
    assert "Address" not in thead


def test_active_table_matches_spec_column_order(index_html):
    active_start = index_html.index('id="screen-active"')
    active_end = index_html.index("</section>", active_start)
    section = index_html[active_start:active_end]
    thead = re.search(r"<thead>(.*?)</thead>", section, re.DOTALL).group(1)
    headers = re.findall(r"<th>(.*?)</th>", thead)
    assert headers == ["Job", "Source", "City", "Service", "Technician", "Stage", "Last update", "Next action"]


# ------------------------------------------------ progressive disclosure --
def test_job_detail_wraps_phone_address_and_raw_message_in_collapsed_details(app_js):
    fn = re.search(r"function jobDetailHtml\(j\) \{(.*?)\n\}", app_js, re.DOTALL).group(1)
    details_block = fn[fn.index('<details class="disclosure"><summary>Job details</summary>'):]
    assert "j.phone" in details_block
    assert "j.address" in details_block
    assert "original_message_masked" in details_block
    # Confirm these fields are NOT referenced anywhere in jobDetailHtml
    # outside that collapsed block (i.e. not shown up front).
    before_details = fn[:fn.index('<details class="disclosure"><summary>Job details</summary>')]
    assert "j.phone" not in before_details
    assert "j.address" not in before_details


def test_technician_card_hides_config_behind_view_details(app_js):
    """Superseded by the redesign's unified responsive card grid (see
    tests/test_ui_acceptance_redesign.py) -- techMobileList/techTableBody
    no longer exist, replaced by a single #techCardGrid used at every
    viewport."""
    fn_start = app_js.index('function renderTechniciansScreen(')
    fn_end = app_js.index("function techEditFormHtml")
    section = app_js[fn_start:fn_end]
    assert 'data-toggledetail' in section
    assert 'style="display:none' in section


# ---------------------------------------------- historical message scope --
def test_jobs_screen_only_shows_last_60_minutes_by_default(index_html):
    jobs_start = index_html.index('id="screen-jobs"')
    jobs_end = index_html.index("</section>", jobs_start)
    section = index_html[jobs_start:jobs_end]
    assert "last 60 minutes" in section
    assert "Archive / Inbox Review" in section  # historical messages live on a separate screen


def test_archive_screen_is_a_distinct_section_from_jobs(index_html):
    assert index_html.index('id="screen-archive"') != index_html.index('id="screen-jobs"')


# ------------------------------------------------------- mobile clearance -
def test_main_content_reserves_space_for_fixed_bottom_nav(styles_css):
    main_content_rule = re.search(r"\.main-content\s*\{(.*?)\}", styles_css, re.DOTALL).group(1)
    assert "70px" in main_content_rule  # matches .mobile-bottom-nav's own height + safe area


def test_bottom_nav_is_fixed_and_hidden_on_desktop(styles_css):
    base_rule = re.search(r"^\.mobile-bottom-nav \{\n(.*?)\n\}", styles_css, re.DOTALL | re.MULTILINE).group(1)
    assert "position: fixed" in base_rule
    assert "bottom: 0" in base_rule
    assert ".mobile-bottom-nav { display: none !important; }" in styles_css  # desktop breakpoint hides it


def test_touch_targets_at_least_44px_on_mobile(styles_css):
    mobile_block = re.search(
        r"@media \(max-width: 899px\), \(max-height: 499px\) \{(.*?)\n\}", styles_css, re.DOTALL
    ).group(1)
    assert "min-height: 44px" in mobile_block


# ------------------------------------------------------ honest data states
def test_kpi_card_shows_na_for_undefined_business_metrics(app_js):
    fn = re.search(r"function fmtKpiValue\(kpi\) \{(.*?)\n\}", app_js, re.DOTALL).group(1)
    assert '"N/A"' in fn
    assert "needs_business_definition" in fn


def test_closed_amount_never_invents_a_revenue_number(app_js):
    fn = re.search(r"function ccClosedAmountHtml\(kpi\) \{(.*?)\n\}", app_js, re.DOTALL).group(1)
    assert "Closing data incomplete" in fn
    assert "Number(kpi.value)" not in fn.split("data_quality === \"ok\"")[0]  # only formats a number in the "ok" branch


def test_charts_receive_data_only_from_dashboard_argument_never_a_new_fetch(app_js):
    """Chart render functions must be pure formatters of what
    loadCommandCenter() already fetched -- no chart function should
    itself call the API."""
    chart_start = app_js.index("/* ------------------------------------------------------------- Charts")
    chart_end = app_js.index("function renderCommandCenterScreen")
    section = app_js[chart_start:chart_end]
    assert "Api." not in section
    assert "fetch(" not in section


# ------------------------------------------ login-screen demo mode banner -
def test_login_screen_has_a_demo_mode_banner_with_required_copy(index_html):
    login_start = index_html.index('id="loginScreen"')
    login_end = index_html.index('id="appShell"')
    section = index_html[login_start:login_end]
    assert 'id="loginDemoModeBanner"' in section
    assert "DEMO MODE" in section
    assert "Synthetic data only" in section
    assert "No live Quo or WhatsApp connection" in section


def test_login_screen_banner_hidden_by_default(index_html):
    banner_tag = re.search(r'<div id="loginDemoModeBanner"[^>]*>', index_html).group(0)
    assert 'display:none' in banner_tag


def test_login_demo_banner_never_appears_inside_appshell(index_html):
    """The login-screen banner must be a sibling of appShell, not nested
    inside it -- otherwise it would be gated by the same auth-only
    display toggle as the post-login banner instead of showing before
    any login attempt."""
    appshell_start = index_html.index('id="appShell"')
    appshell_end = index_html.index("</html>")
    assert 'id="loginDemoModeBanner"' not in index_html[appshell_start:appshell_end]


def test_boot_fetches_demo_status_unconditionally_before_login(app_js):
    boot_index = app_js.index("(async function boot()")
    pre_boot = app_js[:boot_index]
    assert "Api.getDemoStatus()" in pre_boot


def test_demo_status_fetch_never_blocks_or_gates_login(app_js):
    fetch_block = app_js[app_js.index("Api.getDemoStatus()"):app_js.index("(async function boot()")]
    assert ".catch(() => {})" in fetch_block or ".catch(" in fetch_block
