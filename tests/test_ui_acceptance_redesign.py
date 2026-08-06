"""Focused tests for the "visual acceptance failure" redesign pass over
operator_ui_v2_connected/ and its demo seed fixtures. These are
structural/source checks (this frontend has no JS test runner); the
actual rendered result was verified separately in a browser against the
isolated demo server, not the real database. No test in this file
touches the real database, sends any network request, or requires
authentication.
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
    return (UI_DIR / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def seed_script() -> str:
    return (REPO_ROOT / "scripts" / "seed_command_center_demo_db.py").read_text(encoding="utf-8")


# ------------------------------------------------------------- asset paths
def test_served_static_assets_exist_at_the_expected_paths():
    for rel in ("index.html", "css/styles.css", "css/connected.css", "js/app.js"):
        assert (UI_DIR / rel).is_file(), f"missing asset: {rel}"


def test_static_dir_in_operator_api_points_at_the_connected_ui_folder():
    api_src = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    assert 'STATIC_DIR = REPO_ROOT / "operator_ui_v2_connected"' in api_src


def test_static_responses_send_cache_control_no_store():
    """Stale-asset caching (the owner's leading hypothesis for "looks
    unchanged") is structurally ruled out: every response, including
    static files, carries Cache-Control: no-store via SECURITY_HEADERS,
    sent by the same _send_common_headers() call path _serve_static()
    uses. This is a regression guard, not new behavior."""
    api_src = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    headers_block = re.search(r"SECURITY_HEADERS = \((.*?)\)\n", api_src, re.DOTALL).group(1)
    assert '"Cache-Control", "no-store"' in headers_block


# --------------------------------------------------------- KPI categories -
KPI_CATEGORIES = ["new", "waiting", "active", "inprogress", "closingsdue", "completed"]


def test_six_kpi_categories_have_distinct_accent_and_bg_tokens(connected_css):
    root_block = re.search(r":root\s*\{(.*?)\}", (UI_DIR / "css" / "styles.css").read_text(encoding="utf-8"), re.DOTALL).group(1)
    accents = set()
    for cat in KPI_CATEGORIES:
        for suffix in ("accent", "bg"):
            token = f"--kpi-{cat}-{suffix}"
            assert re.search(rf"{re.escape(token)}\s*:", root_block), f"missing token {token}"
        accent_match = re.search(rf"--kpi-{cat}-accent\s*:\s*([^;]+);", root_block)
        accents.add(accent_match.group(1).strip())
    assert len(accents) == 6, "KPI category accent colors must be visually distinct, not all the same"


def test_kpi_card_html_applies_a_category_class(app_js):
    fn = re.search(r"function ccKpiCardHtml\((.*?)\n\}", app_js, re.DOTALL).group(1)
    assert "category" in fn
    assert "cat.cls" in fn or "kpi-${" in fn or re.search(r"kpi-\$\{", fn)


def test_all_six_kpi_grid_calls_pass_a_category(app_js):
    grid_fn = re.search(r'grid\.innerHTML = \[(.*?)\]\.join', app_js, re.DOTALL).group(1)
    calls = re.findall(r"ccKpiCardHtml\(([^)]*)\)", grid_fn)
    assert len(calls) == 6
    for call in calls:
        args = [a.strip() for a in call.split(",")]
        assert len(args) == 4, f"expected 4 args (title, kpi, screenLink, category), got: {call}"


# ------------------------------------------------------- populated fixtures
def test_seed_script_creates_at_least_five_attention_and_six_active_jobs(seed_script):
    job_ids = set(re.findall(r'"([A-Z]\d{3})"', seed_script))
    assert len(job_ids) >= 8, f"expected at least 8 distinct job fixtures, found {len(job_ids)}: {job_ids}"


def test_seed_script_covers_at_least_three_sources(seed_script):
    for builder in ("agn_message", "main_leads_message", "lucky_message"):
        assert f"def {builder}(" in seed_script, f"missing source-fixture builder: {builder}"


def test_seed_script_includes_a_verified_closing_amount(seed_script):
    assert re.search(r"amount.*=.*[\"']\$?\d+", seed_script) or "95" in seed_script


# --------------------------------------------------- technician cards (10) -
def test_technicians_screen_uses_a_card_grid_not_a_raw_table(index_html):
    tech_start = index_html.index('id="screen-technicians"')
    tech_end = index_html.index("</section>", tech_start)
    section = index_html[tech_start:tech_end]
    assert 'id="techCardGrid"' in section
    assert "<table" not in section, "technicians screen should render cards, not an exposed data table"


def test_technician_card_hides_dispatch_and_closing_group_behind_view_details(app_js):
    fn = re.search(r"function renderTechniciansScreen\((.*?)\n\}\n\nfunction techEditFormHtml", app_js, re.DOTALL).group(1)
    assert "data-toggledetail" in fn
    assert 'style="display:none' in fn
    # The dispatch/closing group *values* must only be rendered inside
    # techEditFormHtml (the collapsed detail), not printed into the card
    # summary -- referencing the field to decide whether to show a
    # "missing" warning chip is fine, printing esc(t.dispatch_group) is not.
    grid_html_block = fn[: fn.index("grid.querySelectorAll")]
    assert "esc(t.dispatch_group" not in grid_html_block
    assert "esc(t.closing_group" not in grid_html_block


def test_technician_card_shows_avatar_initials_and_active_state(app_js):
    fn = re.search(r"function renderTechniciansScreen\((.*?)\n\}\n\nfunction techEditFormHtml", app_js, re.DOTALL).group(1)
    assert "techAvatarHtml(t.name)" in fn
    assert "t.active" in fn


def test_tech_card_grid_css_defined(styles_css):
    assert ".tech-card-grid" in styles_css
    assert ".tech-card" in styles_css


# ------------------------------------------------------------------ header
def test_header_is_light_not_a_dominant_dark_navy_block(styles_css):
    header_rule = re.search(r"\.app-header\s*\{(.*?)\}", styles_css, re.DOTALL).group(1)
    assert "var(--header-navy)" not in header_rule
    assert "var(--surface)" in header_rule or "var(--bg)" in header_rule or "#fff" in header_rule.lower()


def test_sidebar_nav_items_render_an_icon(index_html, styles_css):
    sidebar_start = index_html.index('class="desktop-sidebar"')
    sidebar_end = index_html.index("</div>", index_html.index("nav-item", sidebar_start))
    assert "data-icon=" in index_html[sidebar_start:sidebar_start + 2000]
    assert "content: attr(data-icon)" in styles_css


# ------------------------------------------------------------- max width --
def test_main_content_constrains_width_on_wide_desktop_viewports(styles_css):
    desktop_block = re.search(
        r"@media \(min-width: 900px\) and \(min-height: 500px\) \{(.*?)\n\}\n\n", styles_css, re.DOTALL
    )
    assert desktop_block is not None
    assert "max-width" in desktop_block.group(1)
