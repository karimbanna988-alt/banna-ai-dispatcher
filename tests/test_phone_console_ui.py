"""Tests for the phone-first Dispatch Console and its PWA shell.

The owner does not work from a laptop. These pin the properties that
make the console usable and TRUTHFUL on an iPhone: one stage at a time,
thumb-sized controls, no sideways scrolling, a visible offline state,
and a service worker that can never serve yesterday's job as today's.
"""
import ast
import json
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "operator_ui_v2_connected"
INDEX = UI / "index.html"
CSS = UI / "css" / "console.css"
JS = UI / "js" / "console.js"
SW = UI / "sw.js"
MANIFEST = UI / "manifest.webmanifest"


@pytest.fixture(scope="module")
def index_html():
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css():
    return CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js():
    return JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sw():
    return SW.read_text(encoding="utf-8")


# ------------------------------------------------- mobile workflow ----
def test_a_phone_shows_one_stage_at_a_time(css):
    """The requirement that distinguishes a real mobile workflow from
    three desktop columns stacked vertically."""
    phone = _phone_block(css)
    assert ".col { display: none; }" in phone
    for stage in ("dispatch", "progress", "close"):
        assert f'.board[data-stage="{stage}"]' in phone


def test_the_stage_switcher_is_one_tap_and_carries_counts(index_html):
    for stage in ("dispatch", "progress", "close"):
        assert f'data-stage="{stage}"' in index_html
    for count_id in ("stDispatch", "stProgress", "stClose"):
        assert f'id="{count_id}"' in index_html


def test_the_stage_switcher_is_hidden_on_desktop_where_all_columns_show(css):
    """On a desktop every column is already visible, so a stage filter
    there would hide work the operator can see."""
    assert re.search(r"\.stages\s*\{\s*display:\s*none", css)


def test_switching_stage_needs_no_page_load(js):
    assert "function setStage" in js
    assert 'setAttribute("data-stage"' in js


def test_touch_targets_are_thumb_sized(css):
    """Nothing an operator must hit one-handed may be a desktop-sized
    control. 44px is the platform floor."""
    phone = _phone_block(css)
    sizes = [int(m) for m in re.findall(r"min-height:\s*(\d+)px", phone)]
    assert sizes, "phone block declares no explicit touch-target sizes"
    assert min(sizes) >= 44, f"touch target below 44px: {sorted(sizes)}"


def test_no_hover_only_interaction_on_a_phone(css):
    """A finger has no hover state. The phone block must neutralise the
    desktop card hover rather than depend on it."""
    phone = _phone_block(css)
    assert ".card:hover" in phone


def test_the_page_cannot_scroll_sideways(css):
    assert re.search(r"html,\s*body\s*\{[^}]*overflow-x:\s*hidden", _phone_block(css))


def test_iphone_safe_areas_are_respected(css):
    """Notch and home indicator. Without these the top bar sits under the
    status bar and the action bar under the home indicator."""
    assert "env(safe-area-inset-top)" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "viewport-fit=cover" in INDEX.read_text(encoding="utf-8")


def test_the_primary_action_sits_in_the_lower_reachable_area(index_html, js):
    assert 'id="detailBar"' in index_html
    assert "function detailBar" in js


def test_the_detail_is_a_full_screen_sheet_on_a_phone(css):
    phone = _phone_block(css)
    assert re.search(r"\.detail\s*\{[^}]*top:\s*0", phone)
    assert re.search(r"\.detail\s*\{[^}]*width:\s*100%", phone)


# ------------------------------------------------------ truthfulness --
def test_losing_the_backend_is_visible_to_the_operator(index_html, js, css):
    """Cached pixels must never read as live work."""
    assert 'id="offlineBar"' in index_html
    assert "function setOffline" in js
    assert "OFFLINE" in index_html
    assert ".board.stale" in css


def test_offline_is_not_declared_on_a_single_blip(js):
    assert "OFFLINE_AFTER_FAILURES" in js
    assert "state.failures" in js


def test_a_recovered_connection_clears_the_offline_state(js):
    assert "if (state.offline) setOffline(false)" in js


# ----------------------------------------------------------- closing --
def _console_close_reads(js_source: str) -> set:
    """Every `e.<field>` the close-stage code reads."""
    fields = set()
    for fn in ("function closeCard", "function closingState", "function renderCloseDetail"):
        start = js_source.index(fn)
        body = js_source[start:js_source.index("\n  }", start)]
        fields |= set(re.findall(r"\be\.([a-z_]+)", body))
    return fields


def _closing_dto_keys() -> set:
    """The literal keys _closing_dto actually returns, read from the
    source with ast -- not a hand-copied list that can drift."""
    tree = ast.parse((REPO_ROOT / "operator_api.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_closing_dto":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    return {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("_closing_dto not found")


def test_the_close_stage_reads_fields_the_api_actually_returns(js):
    """REGRESSION. The console read `closing_amount`, `closing_payment_method`
    and `missing_fields`; /api/closings returns `amount`, `payment_method`
    and `missing`. Every field came back undefined, so `missing.length`
    was 0 and EVERY closing -- including one with no amount at all --
    rendered as "Ready to close". That is the exact failure the product
    contract forbids."""
    returned = _closing_dto_keys()
    read = _console_close_reads(js)
    unknown = read - returned
    assert not unknown, f"console reads fields /api/closings does not return: {sorted(unknown)}"


def test_a_closing_is_ready_only_on_explicit_recorded_facts(js):
    start = js.index("function closingState")
    body = js[start:js.index("\n  }", start)]
    assert "e.closing_recorded" in body
    assert "e.missing" in body
    assert "e.prepared_text" in body
    # The single ready:true branch must be the last one, after every guard.
    assert body.count("ready: true") == 1


def test_a_closing_that_is_not_ready_offers_no_one_tap_close(js):
    start = js.index("function closeCard")
    body = js[start:js.index("\n  }", start)]
    assert "st.ready ?" in body, "the copy-closing button must be gated on readiness"


def test_the_last_technician_update_renders_as_text_not_an_object(js):
    """REGRESSION. latest_technician_update is an object
    ({update_type, raw_message, ...}). Interpolating it directly printed
    a literal "[object Object]" on the card, exactly where the
    technician's own words are supposed to be."""
    assert "function lastUpdateText" in js
    assert "esc(e.latest_technician_update)" not in js, (
        "the update object must be unwrapped before rendering")


# ---------------------------------------------------------- the PWA ---
def test_the_manifest_is_a_standalone_phone_app():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["name"] == "Banna Dispatch"
    assert m["short_name"]
    assert m["display"] == "standalone"
    assert m["start_url"]
    assert m["theme_color"] and m["background_color"]


def test_every_declared_icon_exists_on_disk():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for icon in m["icons"]:
        path = UI / icon["src"]
        assert path.is_file(), f"manifest declares a missing icon: {icon['src']}"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_page_declares_the_phone_metadata(index_html):
    for needed in ('rel="manifest"', 'name="theme-color"',
                   'rel="apple-touch-icon"', 'name="apple-mobile-web-app-capable"'):
        assert needed in index_html


def test_the_server_serves_a_manifest_browsers_will_accept():
    """Chrome silently ignores a manifest sent as octet-stream, which
    makes the app non-installable with no console error."""
    source = (REPO_ROOT / "operator_api.py").read_text(encoding="utf-8")
    assert '".webmanifest": "application/manifest+json"' in source


def test_static_assets_are_versioned(index_html):
    assert "console.css?v=" in index_html
    assert "console.js?v=" in index_html


# --------------------------------------------- service worker safety --
def test_the_service_worker_never_touches_job_truth(sw):
    """THE service-worker rule: a cached API response would show an
    operator old work as if it were live."""
    assert 'url.pathname.startsWith("/api/")' in sw
    bail = sw.index('url.pathname.startsWith("/api/")')
    assert "return;" in sw[bail:bail + 120], "the /api/ branch must return before any cache use"


def test_no_api_path_is_in_the_precached_shell(sw):
    start = sw.index("SHELL_ASSETS")
    block = sw[start:sw.index("]", start)]
    assert "/api/" not in block


def test_the_service_worker_has_no_offline_write_queue(sw):
    """Offline is read-only. A queued dispatch or closing could fire
    later against a job whose state has since moved on.

    Checked against CODE, not comments -- sw.js documents the ban in
    prose ("NO background sync"), and a naive substring scan would match
    the prohibition itself and pass or fail for the wrong reason."""
    code = _strip_comments(sw)
    for forbidden in ("sync", "periodicsync", "postMessage", "indexeddb", "localstorage"):
        assert forbidden not in code.lower(), f"service worker must not use {forbidden}"


def test_the_service_worker_only_handles_get_on_its_own_origin(sw):
    assert 'req.method !== "GET"' in sw
    assert "url.origin !== self.location.origin" in sw


def test_registration_requires_a_secure_context(js):
    """Over plain LAN http registration always fails; guarding it keeps
    the failure honest instead of logging an error nobody can act on."""
    assert "window.isSecureContext" in js
    assert 'register("/sw.js")' in js


def test_the_shell_cache_version_matches_the_asset_version(sw, index_html):
    """Bumping ?v= without bumping the cache leaves the worker serving
    the previous build forever."""
    asset_v = re.search(r"console\.js\?v=(\d+)", index_html).group(1)
    cache_v = re.search(r'SHELL_VERSION = "[a-z]+-shell-v(\d+)"', sw).group(1)
    assert asset_v == cache_v
    assert f"console.js?v={asset_v}" in sw


# ------------------------------------------------------------ safety --
def test_the_phone_console_still_cannot_transmit_anything(js, sw):
    for forbidden in ("sendMessage", "sendWhatsapp", "/api/send", "twilio"):
        assert forbidden not in _strip_comments(js)
        assert forbidden not in _strip_comments(sw)


def _strip_comments(source: str) -> str:
    """Remove JS comments so a guard checks code, not the prose that
    documents the guard. Line comments are only stripped when `//` is not
    preceded by `:`, so a URL inside a string survives intact."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", " ", source)


def test_no_business_logic_was_reimplemented_in_javascript(js):
    """Waiting-state wording is computed server-side from the shared
    message_patterns. A regex here would fork the dispatcher brain."""
    assert "operator_state" in js
    for forbidden in ("on my way", "no answer", "omw", "quoted"):
        assert forbidden not in js.lower(), (
            f"{forbidden!r} looks like message classification moved into the client")


def _phone_block(css_source: str) -> str:
    """The @media (max-width: 768px) block -- the phone workflow."""
    start = css_source.index("@media (max-width: 768px)")
    depth, i = 0, css_source.index("{", start)
    for j in range(i, len(css_source)):
        if css_source[j] == "{":
            depth += 1
        elif css_source[j] == "}":
            depth -= 1
            if depth == 0:
                return css_source[start:j + 1]
    raise AssertionError("unterminated phone media block")
