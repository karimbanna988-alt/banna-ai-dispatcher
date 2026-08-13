"""Safety tests for scripts/report_whatsapp_shadow_traces.py.

The report reads live customer-bearing messages, so the properties that
matter are: it cannot write, and it never prints message content.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "report_whatsapp_shadow_traces.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("report_shadow_traces", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_report_opens_the_database_read_only(mod):
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "?mode=ro" in source
    assert "uri=True" in source


def test_report_contains_no_write_capability(mod):
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "commit(", "save_audit_entry"):
        assert forbidden not in source, f"report must never contain {forbidden!r}"


def test_report_never_prints_message_text(mod):
    """Only ticket/group/role/direction/timestamp may reach stdout -- a
    print of the raw body would leak customer detail to a terminal."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    printing = [ln for ln in source.splitlines() if ln.strip().startswith("print(")]
    for line in printing:
        assert "text" not in line, f"report must not print message text: {line.strip()!r}"
        assert "body" not in line


def test_collect_hops_skips_messages_with_no_parseable_ticket(mod):
    """A message with no ticket contributes no hop -- it must never be
    attached to a trace by timing proximity."""
    from dispatcher.parser import load_vehicle_rules
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE whatsapp_pending_observations
                    (group_id TEXT, group_name TEXT, text TEXT, whatsapp_timestamp TEXT)""")
    conn.execute("""CREATE TABLE whatsapp_outgoing_audit
                    (group_id TEXT, group_name TEXT, text TEXT, whatsapp_timestamp TEXT)""")
    conn.execute("""CREATE TABLE whatsapp_group_registry
                    (group_id TEXT, role TEXT, confirmed_by_owner INT)""")
    conn.execute("INSERT INTO whatsapp_pending_observations VALUES ('g1','G','k','100')")
    conn.execute("INSERT INTO whatsapp_pending_observations VALUES ('g1','G','on my way','101')")
    conn.commit()
    hops = mod.collect_hops(conn, load_vehicle_rules())
    assert hops == []


def test_collect_hops_extracts_a_hop_per_ticketed_message(mod):
    from dispatcher.parser import load_vehicle_rules
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE whatsapp_pending_observations
                    (group_id TEXT, group_name TEXT, text TEXT, whatsapp_timestamp TEXT)""")
    conn.execute("""CREATE TABLE whatsapp_outgoing_audit
                    (group_id TEXT, group_name TEXT, text TEXT, whatsapp_timestamp TEXT)""")
    conn.execute("""CREATE TABLE whatsapp_group_registry
                    (group_id TEXT, role TEXT, confirmed_by_owner INT)""")
    conn.execute("INSERT INTO whatsapp_group_registry VALUES ('g1','COMPANY_JOB_INTAKE',1)")
    # Real newlines matter: the parser's ticket/phone/address patterns are
    # line-anchored (re.MULTILINE), so a literal "\n" would not parse.
    message = "Ref: AGN Locksmith Services\nPDL: ABC12\nPh: 1-555-010-3000\nAddr: 1 Fake St, Springfield\n"
    conn.execute("INSERT INTO whatsapp_pending_observations VALUES (?,?,?,?)",
                 ("g1", "Intake", message, "100"))
    conn.commit()
    hops = mod.collect_hops(conn, load_vehicle_rules())
    assert len(hops) == 1
    assert hops[0].ticket == "ABC12"
    assert hops[0].direction == "incoming"
    assert hops[0].confirmed_group is True
