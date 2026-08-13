"""Safety tests for scripts/monitor_whatsapp_shadow_decisions_live.py.

This monitor prints live WhatsApp interpretation decisions to a terminal
that stays open on the operator's desktop, so the two properties that
actually matter are: it can never write to the database, and it can
never print full customer detail (phone/address/complete message text).
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "monitor_whatsapp_shadow_decisions_live.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("monitor_shadow_decisions", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_preview_truncates_long_text_so_a_full_message_never_reaches_the_terminal(mod):
    long_text = "Ticket: ABC-123 Name: A Real Person Phone: (555) 010-9999 Address: 100 Somewhere St, Springfield"
    out = mod._preview(long_text)
    assert len(out) <= mod.PREVIEW_CHARS + 3  # + the "..." marker
    assert out.endswith("...")
    assert "Springfield" not in out
    assert "010-9999" not in out


def test_preview_flattens_newlines_so_a_multiline_block_cannot_dump_across_the_screen(mod):
    out = mod._preview("line one\nline two\nline three")
    assert "\n" not in out


def test_preview_handles_empty_and_none_without_crashing(mod):
    assert mod._preview("") == ""
    assert mod._preview(None) == ""


def test_short_text_is_shown_in_full_without_a_truncation_marker(mod):
    assert mod._preview("K") == "K"


def test_monitor_opens_the_database_read_only(mod):
    """The mode=ro URI flag is the structural guarantee -- SQLite itself
    refuses a write on this connection, so this can never mutate real
    business data no matter what the rest of the file does."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "?mode=ro" in source
    assert "uri=True" in source


def test_monitor_contains_no_write_or_send_capability(mod):
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "sendMessage", "save_audit_entry", "conn.commit"):
        assert forbidden not in source, f"monitor must never contain {forbidden!r}"


def test_monitor_reuses_the_real_interpretation_function_rather_than_reimplementing_it(mod):
    """If this ever drifts into its own copy of the interpretation logic,
    the terminal view could disagree with what the CRM actually shows."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "_interpret_whatsapp_observation" in source
