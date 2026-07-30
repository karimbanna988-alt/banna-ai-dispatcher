"""Framework-neutral, read-only operational health snapshot.

get_operational_health() never writes anything and never returns
customer data (no names, phone numbers, addresses, raw messages, API
keys, or passwords) -- only counts, timestamps, and configuration
status. Safe to call from a test, a script, or (if the UI is ever
extended with a compact status strip) a Streamlit page.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from . import outbound, storage
from .review_interpreter import DisabledReviewInterpreter


@dataclass(frozen=True)
class OperationalHealth:
    database_reachable: bool
    current_schema_revision: int
    required_schema_revision: int
    migration_required: bool
    shadow_schema_present: bool
    integrity_status: Optional[str]
    foreign_key_violation_count: Optional[int]
    last_successful_quo_poll: Optional[str]
    last_poll_error: Optional[str]
    live_queue_count: int
    oldest_live_message_age_seconds: Optional[float]
    config_error_count: int
    config_warning_count: int
    technicians_with_dispatch_destination: int
    active_technician_count: int
    outbound_mode: str
    quo_outbound_implemented: bool
    ai_interpreter_enabled: bool
    latest_verified_backup_timestamp: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def _oldest_live_pending_age_seconds(conn: sqlite3.Connection) -> Optional[float]:
    boundary = storage.get_live_queue_started_at(conn)
    if not boundary:
        return None
    row = conn.execute(
        "SELECT MIN(COALESCE(received_at, fetched_at)) FROM quo_pending_messages "
        "WHERE status IN ('pending', 'loaded') AND COALESCE(received_at, fetched_at) >= ?",
        (boundary,),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def get_operational_health(conn: sqlite3.Connection, backup_timestamp: Optional[str] = None) -> OperationalHealth:
    """`backup_timestamp` is caller-supplied (this module has no
    opinion on where backups live -- see scripts/backup_database.py)
    since "latest verified backup" is a filesystem fact, not a database
    fact."""
    notes: List[str] = []

    try:
        schema_status = storage.check_schema_status(conn)
        database_reachable = True
    except sqlite3.Error:
        return OperationalHealth(
            database_reachable=False, current_schema_revision=0, required_schema_revision=storage.SCHEMA_REVISION,
            migration_required=True, shadow_schema_present=False, integrity_status=None, foreign_key_violation_count=None,
            last_successful_quo_poll=None, last_poll_error=None, live_queue_count=0,
            oldest_live_message_age_seconds=None, config_error_count=0, config_warning_count=0,
            technicians_with_dispatch_destination=0, active_technician_count=0,
            outbound_mode=outbound.current_mode().value, quo_outbound_implemented=False,
            ai_interpreter_enabled=DisabledReviewInterpreter().is_enabled(),
            notes=["database unreachable"],
        )

    try:
        shadow_present = storage.shadow_schema_present(conn)
    except sqlite3.Error:
        shadow_present = False
        notes.append("shadow schema check failed to run")

    integrity_status = None
    fk_violation_count = None
    if schema_status.up_to_date:
        try:
            integrity_status = conn.execute("PRAGMA integrity_check").fetchone()[0]
            fk_violation_count = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        except sqlite3.Error:
            notes.append("integrity/foreign_key_check failed to run")
    else:
        notes.append("schema out of date -- integrity/foreign_key_check skipped (see docs/SCHEMA_MIGRATION_RUNBOOK.md)")

    last_poll = None
    last_poll_error = None
    live_queue_count = 0
    oldest_age = None
    technicians_with_dest = 0
    active_technicians = 0
    if schema_status.up_to_date:
        try:
            row = conn.execute("SELECT updated_at FROM quo_poll_state WHERE id = 1").fetchone()
            last_poll = row[0] if row else None
            last_poll_error = storage.get_poll_error(conn)
            boundary = storage.get_live_queue_started_at(conn)
            if boundary:
                live_queue_count = conn.execute(
                    "SELECT COUNT(*) FROM quo_pending_messages WHERE status IN ('pending', 'loaded') "
                    "AND COALESCE(received_at, fetched_at) >= ?",
                    (boundary,),
                ).fetchone()[0]
            oldest_age = _oldest_live_pending_age_seconds(conn)
            technicians = storage.list_technicians(conn, active_only=False)
            active_technicians = sum(1 for t in technicians if t["active"])
            technicians_with_dest = sum(1 for t in technicians if t["active"] and t["dispatch_group"])
        except sqlite3.Error:
            notes.append("operational counts unavailable")

    config_errors = 0
    config_warnings = 0
    if schema_status.up_to_date:
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            import validate_configuration

            findings = validate_configuration.validate(conn)
            config_errors = sum(1 for f in findings if f.level == "ERROR")
            config_warnings = sum(1 for f in findings if f.level == "WARNING")
        except Exception:  # noqa: BLE001 -- health check must never itself crash the caller
            notes.append("configuration validation unavailable")

    return OperationalHealth(
        database_reachable=database_reachable,
        current_schema_revision=schema_status.current_revision,
        required_schema_revision=schema_status.required_revision,
        migration_required=not schema_status.up_to_date,
        shadow_schema_present=shadow_present,
        integrity_status=integrity_status,
        foreign_key_violation_count=fk_violation_count,
        last_successful_quo_poll=last_poll,
        last_poll_error=last_poll_error,
        live_queue_count=live_queue_count,
        oldest_live_message_age_seconds=oldest_age,
        config_error_count=config_errors,
        config_warning_count=config_warnings,
        technicians_with_dispatch_destination=technicians_with_dest,
        active_technician_count=active_technicians,
        outbound_mode=outbound.current_mode().value,
        quo_outbound_implemented=False,  # _send_live() remains NotImplementedError -- see outbound.py
        ai_interpreter_enabled=DisabledReviewInterpreter().is_enabled(),
        latest_verified_backup_timestamp=backup_timestamp,
        notes=notes,
    )
