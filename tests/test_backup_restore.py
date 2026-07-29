"""Focused tests for scripts/backup_database.py, verify_backup.py,
restore_database.py, and manage_schema.py. Every test operates on a
temporary SQLite database; the real database is never touched, and
restore is only ever exercised against a temporary target path.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dispatcher import storage  # noqa: E402
from dispatcher.parser import extract_job, load_vehicle_rules  # noqa: E402


def _load_module(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backup_database = _load_module("backup_database")
verify_backup = _load_module("verify_backup")
restore_database = _load_module("restore_database")
manage_schema = _load_module("manage_schema")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def _seeded_db(tmp_path, vehicle_rules) -> Path:
    db_path = tmp_path / "source.db"
    conn = storage.connect(db_path)
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: BKPTEST\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, cr, final_action="Approve")
    conn.close()
    return db_path


# ------------------------------------------------------------- backup ---
def test_backup_creates_a_distinct_file(tmp_path, vehicle_rules):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    assert backup_path.exists()
    assert backup_path != source


def test_backup_preserves_row_content(tmp_path, vehicle_rules):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    conn = sqlite3.connect(backup_path)
    row = conn.execute("SELECT ticket_number FROM jobs WHERE ticket_number = 'BKPTEST'").fetchone()
    conn.close()
    assert row is not None


def test_backup_never_resolves_to_the_source_path(tmp_path, vehicle_rules):
    """backup_database()'s timestamped naming scheme structurally cannot
    collide with the source filename -- the explicit same-file guard
    inside it (mirroring restore_database.py's equivalent, directly
    tested there) is defensive, not normally reachable through this
    function's own naming convention."""
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    assert backup_path.resolve() != source.resolve()
    assert backup_path.name != source.name


def test_describe_backup_reports_no_row_content_keys(tmp_path, vehicle_rules):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    info = backup_database.describe_backup(backup_path)
    serialized = str(info)
    assert "BKPTEST" not in serialized
    assert "sha256" in info
    assert "integrity_check" in info
    assert info["integrity_check"] == "ok"


# --------------------------------------------------------- verify_backup
def test_verify_backup_passes_for_a_healthy_backup(tmp_path, vehicle_rules, capsys):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    exit_code = verify_backup.main([str(backup_path)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "BKPTEST" not in captured.out
    assert "PASS" in captured.out


def test_verify_backup_missing_file_reports_error(tmp_path, capsys):
    exit_code = verify_backup.main([str(tmp_path / "nonexistent.bak")])
    assert exit_code == 2


# -------------------------------------------------------------- restore -
def test_restore_to_temporary_target_succeeds(tmp_path, vehicle_rules):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    target = tmp_path / "restored" / "copy.db"
    restored = restore_database.restore_database(backup_path, target)
    assert restored.exists()
    info = restore_database.verify_restored_copy(restored)
    assert info["integrity_check"] == "ok"
    assert info["foreign_key_violations"] == 0
    assert info["row_counts"]["jobs"] == 1


def test_restore_refuses_to_target_the_live_database_path(tmp_path, vehicle_rules, monkeypatch):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    fake_live = tmp_path / "fake_live.db"
    monkeypatch.setattr(storage, "DB_PATH", fake_live)
    with pytest.raises(ValueError, match="live database"):
        restore_database.restore_database(backup_path, fake_live)


def test_restore_refuses_existing_target(tmp_path, vehicle_rules):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    target = tmp_path / "already_here.db"
    target.write_bytes(b"not empty")
    with pytest.raises(FileExistsError):
        restore_database.restore_database(backup_path, target)


def test_restore_refuses_same_source_and_target(tmp_path, vehicle_rules):
    source = _seeded_db(tmp_path, vehicle_rules)
    with pytest.raises(ValueError):
        restore_database.restore_database(source, source)


def test_restored_copy_opens_read_only_without_error(tmp_path, vehicle_rules):
    source = _seeded_db(tmp_path, vehicle_rules)
    backup_path = backup_database.backup_database(source, dest_dir=tmp_path / "backups")
    target = tmp_path / "restored2" / "copy.db"
    restored = restore_database.restore_database(backup_path, target)
    conn = sqlite3.connect(f"file:{restored.as_posix()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO technicians (name, active, areas_json, skills_json, vehicle_restrictions_json, created_at, updated_at) VALUES ('x', 1, '[]', '[]', '[]', 'now', 'now')")
    conn.close()


# --------------------------------------------------------- manage_schema
def test_manage_schema_check_reports_up_to_date(tmp_path, vehicle_rules, capsys):
    source = _seeded_db(tmp_path, vehicle_rules)
    exit_code = manage_schema.cmd_check(source)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "up to date" in captured.out.lower()


def test_manage_schema_check_reports_pending_migration(tmp_path, capsys):
    from test_schema_migration import _raw_current_minus_idempotency
    db_path = tmp_path / "pending.db"
    _raw_current_minus_idempotency(db_path)
    exit_code = manage_schema.cmd_check(db_path)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "idempotency_key" in captured.out


def test_manage_schema_migrate_backs_up_first_and_applies(tmp_path, monkeypatch):
    from test_schema_migration import _raw_current_minus_idempotency
    db_path = tmp_path / "pending.db"
    _raw_current_minus_idempotency(db_path)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup_database, "DEFAULT_BACKUP_DIR", backup_dir)

    exit_code = manage_schema.cmd_migrate(db_path, assume_yes=True)
    assert exit_code == 0
    assert backup_dir.exists()
    assert any(backup_dir.iterdir())

    conn = storage.connect(db_path)
    status = storage.check_schema_status(conn)
    assert status.up_to_date


def test_manage_schema_migrate_missing_db_reports_error(tmp_path):
    exit_code = manage_schema.cmd_migrate(tmp_path / "nope.db", assume_yes=True)
    assert exit_code == 2


def test_manage_schema_verify_passes_for_current_db(tmp_path, vehicle_rules, capsys):
    source = _seeded_db(tmp_path, vehicle_rules)
    exit_code = manage_schema.cmd_verify(source)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PASS" in captured.out
    assert "BKPTEST" not in captured.out
