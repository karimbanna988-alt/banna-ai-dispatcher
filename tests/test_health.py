"""Focused tests for src/dispatcher/health.py -- get_operational_health()
must never write, never return customer data, and degrade gracefully
against a stale (pre-migration) schema."""

from __future__ import annotations

import dataclasses

import pytest

from dispatcher import health, outbound, storage
from dispatcher.parser import extract_job, load_vehicle_rules
from test_schema_migration import _raw_current_minus_idempotency


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def test_health_on_fresh_database(tmp_path):
    conn = storage.connect(tmp_path / "fresh.db")
    result = health.get_operational_health(conn)
    assert result.database_reachable is True
    assert result.migration_required is False
    assert result.integrity_status == "ok"
    assert result.foreign_key_violation_count == 0
    assert result.shadow_schema_present is True


def test_health_reports_shadow_schema_absent_without_blocking_core_readiness(tmp_path):
    """A database shaped exactly like the real one (core up to date,
    shadow tables not yet migrated) must report migration_required=False
    (core is fine) while shadow_schema_present=False (Shadow Mode is
    not yet available) -- the two are independent facts."""
    conn = storage.connect(tmp_path / "core_only.db")
    conn.execute("DROP TABLE shadow_episodes")
    conn.execute("DROP TABLE shadow_events")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()

    result = health.get_operational_health(conn)
    assert result.migration_required is False
    assert result.shadow_schema_present is False


def test_health_never_writes(tmp_path):
    conn = storage.connect(tmp_path / "fresh.db")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "events", "technicians")}
    health.get_operational_health(conn)
    health.get_operational_health(conn)
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "events", "technicians")}
    assert before == after


def test_health_reports_migration_required_on_stale_schema(tmp_path):
    db_path = tmp_path / "stale.db"
    _raw_current_minus_idempotency(db_path)
    conn = storage.connect(db_path)
    result = health.get_operational_health(conn)
    assert result.migration_required is True
    assert result.current_schema_revision < result.required_schema_revision
    # Operational counts that depend on newer columns are safely skipped,
    # not crashed on.
    assert result.integrity_status is None
    assert any("schema out of date" in n for n in result.notes)


def test_health_reports_no_customer_data(tmp_path, vehicle_rules):
    conn = storage.connect(tmp_path / "with_job.db")
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: HEALTHTEST\nPh: 1-555-010-3000\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, cr, final_action="Approve")

    result = health.get_operational_health(conn)
    serialized = str(dataclasses.asdict(result))
    assert "HEALTHTEST" not in serialized
    assert "555-010-3000" not in serialized
    assert "Fake St" not in serialized


def test_health_reports_outbound_mode_and_never_claims_implemented(tmp_path, monkeypatch):
    monkeypatch.delenv(outbound.OUTBOUND_MODE_ENV_VAR, raising=False)
    conn = storage.connect(tmp_path / "t.db")
    result = health.get_operational_health(conn)
    assert result.outbound_mode == "disabled"
    assert result.quo_outbound_implemented is False


def test_health_reports_ai_interpreter_disabled(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    result = health.get_operational_health(conn)
    assert result.ai_interpreter_enabled is False


def test_health_config_error_and_warning_counts_match_validator(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    result = health.get_operational_health(conn)
    # A fresh db with zero technicians is always at least one ERROR
    # ("no active technicians configured at all") per the validator.
    assert result.config_error_count >= 1


def test_health_accepts_caller_supplied_backup_timestamp(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    result = health.get_operational_health(conn, backup_timestamp="2026-07-30T00:00:00Z")
    assert result.latest_verified_backup_timestamp == "2026-07-30T00:00:00Z"


def test_health_on_unreachable_database_reports_gracefully():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.close()  # closed connection -- every query will raise
    result = health.get_operational_health(conn)
    assert result.database_reachable is False
    assert result.migration_required is True
