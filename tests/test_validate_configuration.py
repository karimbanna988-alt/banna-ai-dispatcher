"""Focused tests for scripts/validate_configuration.py -- deterministic,
read-only, never touches the real database. Every test builds its own
temporary SQLite database via storage.connect(tmp_path) and opens it
strictly read-only for validation, exactly like the script does.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dispatcher import storage  # noqa: E402

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_configuration.py"
spec = importlib.util.spec_from_file_location("validate_configuration", SCRIPT_PATH)
validate_configuration = importlib.util.module_from_spec(spec)
sys.modules["validate_configuration"] = validate_configuration  # dataclass() needs this registered first
spec.loader.exec_module(validate_configuration)


@pytest.fixture()
def readonly_conn(tmp_path):
    db_path = tmp_path / "test.db"
    setup_conn = storage.connect(db_path)
    yield db_path, setup_conn
    setup_conn.close()


def _readonly(db_path: Path) -> sqlite3.Connection:
    return validate_configuration._connect_readonly(db_path)


def test_no_active_technicians_is_an_error(readonly_conn):
    db_path, _setup = readonly_conn
    conn = _readonly(db_path)
    findings = validate_configuration.validate(conn)
    conn.close()
    assert any(f.level == "ERROR" and "No active technicians" in f.message for f in findings)


def test_active_technician_with_no_areas_is_an_error(readonly_conn):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="No Area Tech", areas=[])
    conn = _readonly(db_path)
    findings = validate_configuration.validate(conn)
    conn.close()
    assert any(f.level == "ERROR" and "no configured service areas" in f.message for f in findings)


def test_technician_with_unknown_area_is_an_error(readonly_conn):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="Bad Area Tech", areas=["Nonexistent City"])
    conn = _readonly(db_path)
    findings = validate_configuration.validate(conn)
    conn.close()
    assert any(f.level == "ERROR" and "unknown service area" in f.message for f in findings)


def test_active_technician_without_dispatch_group_is_a_warning(readonly_conn):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="No Dispatch Group", areas=["Charlotte"])
    conn = _readonly(db_path)
    findings = validate_configuration.validate(conn)
    conn.close()
    assert any(f.level == "WARNING" and "dispatch_group" in f.message for f in findings)


def test_fully_configured_technician_produces_no_warning_for_that_technician(readonly_conn):
    db_path, setup_conn = readonly_conn
    storage.add_technician(
        setup_conn, name="Fully Configured", areas=["Charlotte"],
        dispatch_group="Test Group", closing_group="Test Closing Group",
    )
    conn = _readonly(db_path)
    findings = validate_configuration.validate(conn)
    conn.close()
    tech_id_row = setup_conn.execute("SELECT id FROM technicians WHERE name = 'Fully Configured'").fetchone()
    label = f"technician id={tech_id_row[0]}"
    assert not any(label in f.message and "dispatch_group" in f.message for f in findings)


def test_duplicate_technician_name_case_insensitive_is_a_warning(readonly_conn):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="Same Name", areas=["Charlotte"], dispatch_group="G1")
    storage.add_technician(setup_conn, name="same name", areas=["Charlotte"], dispatch_group="G2")
    conn = _readonly(db_path)
    findings = validate_configuration.validate(conn)
    conn.close()
    assert any(f.level == "WARNING" and "duplicates" in f.message for f in findings)


def test_atlantic_city_resolves_to_nj():
    from dispatcher.rules.market_aliases import resolve_market_and_area
    resolution = resolve_market_and_area(None, "Atlantic City Locksmith Pro")
    assert resolution.canonical_area == "NJ"


def test_source_policy_coverage_has_no_gaps_for_known_sources():
    findings = validate_configuration._validate_source_policies()
    assert findings == []


def test_readonly_connection_cannot_write(readonly_conn):
    db_path, _setup = readonly_conn
    conn = _readonly(db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO technicians (name, active, areas_json, skills_json, vehicle_restrictions_json, created_at, updated_at) VALUES ('x', 1, '[]', '[]', '[]', 'now', 'now')")
    conn.close()


def test_main_returns_nonzero_exit_code_on_error(readonly_conn, capsys):
    db_path, _setup = readonly_conn
    exit_code = validate_configuration.main(["--db", str(db_path)])
    assert exit_code == 1  # no active technicians -- always an error on a bare fresh db


def test_main_never_writes_to_the_database(readonly_conn):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="Stable Tech", areas=["Charlotte"], dispatch_group="G1")
    before = setup_conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    validate_configuration.main(["--db", str(db_path)])
    after = setup_conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    assert before == after


def test_export_blank_template_has_empty_destinations(readonly_conn):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="Template Tech", areas=["Charlotte"], dispatch_group="Real Group")
    conn = _readonly(db_path)
    csv_text = validate_configuration.export_blank_configuration_template(conn)
    conn.close()
    assert "Template Tech" in csv_text
    assert "Real Group" not in csv_text  # never fills a real destination in
    lines = csv_text.strip().splitlines()
    assert lines[0] == "technician_id,name,areas,dispatch_group,closing_group"
    assert lines[1].endswith(",,")  # dispatch_group and closing_group both blank


def test_export_template_never_writes_to_the_database(readonly_conn, tmp_path):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="Stable Tech 2", areas=["Charlotte"])
    before = setup_conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    out_path = tmp_path / "template.csv"
    validate_configuration.main(["--db", str(db_path), "--export-template", str(out_path)])
    after = setup_conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    assert before == after
    assert out_path.exists()


def test_no_customer_data_printed(readonly_conn, capsys):
    db_path, setup_conn = readonly_conn
    storage.add_technician(setup_conn, name="Real Tech Name", areas=["Charlotte"])
    validate_configuration.main(["--db", str(db_path)])
    captured = capsys.readouterr()
    assert "Real Tech Name" not in captured.out
