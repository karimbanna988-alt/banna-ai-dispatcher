"""Security/privacy/logging audit -- focused, executable proof rather
than a documentation claim. Every test here operates on fabricated data
in a temporary database; nothing here loads the real .env or makes a
real network call.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from dispatcher import health, storage
from dispatcher.application import compute_idempotency_key
from dispatcher.parser import extract_job, load_vehicle_rules

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


# ------------------------------------------------------------- source scan
def test_quo_api_key_never_hardcoded_in_new_architecture_files():
    files = (
        "src/dispatcher/application.py", "src/dispatcher/adapters.py", "src/dispatcher/health.py",
        "src/dispatcher/source_adapters.py", "src/dispatcher/review_interpreter.py",
        "src/dispatcher/action_names.py", "src/dispatcher/app_models.py", "src/dispatcher/clock.py",
        "scripts/manage_schema.py", "scripts/backup_database.py", "scripts/verify_backup.py",
        "scripts/restore_database.py", "scripts/validate_configuration.py",
    )
    for f in files:
        source = (REPO_ROOT / f).read_text(encoding="utf-8")
        assert "QUO_API_KEY=" not in source  # never a literal assignment
        assert "sk-" not in source


def test_no_env_file_loaded_by_new_test_files():
    """The real .env file must never be loaded by a test -- checked
    precisely (an actual load_dotenv() call, or opening a literal
    ".env" path) rather than a bare substring match, which would also
    false-positive on legitimate docstring prose or
    monkeypatch.setenv()/os.environ.get() calls (setting a fake env var
    for the duration of one test is not loading the real file)."""
    test_files = (
        "tests/test_application_service.py", "tests/test_schema_migration.py",
        "tests/test_backup_restore.py", "tests/test_health.py", "tests/test_e2e_lifecycle.py",
        "tests/test_concurrency.py", "tests/test_transaction_fault_injection.py",
    )
    for f in test_files:
        source = (REPO_ROOT / f).read_text(encoding="utf-8")
        assert "load_dotenv(" not in source
        assert 'Path(".env")' not in source
        assert 'open(".env")' not in source


def test_no_real_network_libraries_in_new_files():
    files = (
        "src/dispatcher/application.py", "src/dispatcher/adapters.py", "src/dispatcher/health.py",
        "src/dispatcher/source_adapters.py", "src/dispatcher/review_interpreter.py",
        "scripts/manage_schema.py", "scripts/backup_database.py",
    )
    for f in files:
        source = (REPO_ROOT / f).read_text(encoding="utf-8")
        assert "import requests" not in source
        assert "import urllib" not in source
        assert "httpx" not in source


# -------------------------------------------------------- idempotency keys
def test_idempotency_keys_are_one_way_hashes():
    key = compute_idempotency_key(42, "record_technician_update", "Going|None|the customer's real name and phone")
    assert len(key) == 32
    assert all(c in "0123456789abcdef" for c in key)
    # A one-way hash never contains the plaintext it was derived from.
    assert "customer" not in key
    assert "phone" not in key


def test_idempotency_key_does_not_leak_content_length_trivially():
    short_key = compute_idempotency_key(1, "action", "x")
    long_key = compute_idempotency_key(1, "action", "x" * 5000)
    assert len(short_key) == len(long_key) == 32  # fixed-length output regardless of input size


# ----------------------------------------------------- health snapshot PII
def test_health_snapshot_never_contains_pii(tmp_path, vehicle_rules):
    conn = storage.connect(tmp_path / "t.db")
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: SECTEST1\nPh: 1-555-010-9999\n"
        "Addr: 999 Secret St, Charlotte, NC\nDesc: Car Lockout\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, cr, final_action="Approve")

    result = health.get_operational_health(conn)
    serialized = str(dataclasses.asdict(result))
    for forbidden in ("SECTEST1", "555-010-9999", "Secret St", "QUO_API_KEY"):
        assert forbidden not in serialized


# ------------------------------------------------ configuration validator
def test_validator_output_contains_no_raw_message_content(tmp_path, vehicle_rules):
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import validate_configuration

    conn = storage.connect(tmp_path / "t.db")
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: SECTEST2\nPh: 1-555-010-8888\n"
        "Addr: 888 Hidden Ave, Charlotte, NC\nDesc: Car Lockout\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(conn, job)
    storage.save_audit_entry(conn, job, cr, final_action="Approve")

    findings = validate_configuration.validate(conn)
    conn.close()
    serialized = str(findings)
    assert "SECTEST2" not in serialized
    assert "555-010-8888" not in serialized
    assert "Hidden Ave" not in serialized


# ------------------------------------------------------- prepared messages
def test_prepared_company_messages_stored_once_not_duplicated_per_event(tmp_path, vehicle_rules):
    """Preparing a company update/closing records the prepared text
    exactly once per action (in luxor_message), not copied into
    multiple unrelated columns or re-logged elsewhere."""
    from dispatcher.application import DispatcherService
    from dispatcher.app_models import TechnicianUpdateCommand

    conn = storage.connect(tmp_path / "t.db")
    job = extract_job(
        "Ref: AGN Locksmith Services\nPDL: SECTEST3\nPh: 1-555-010-7777\n"
        "Addr: 100 Fake St, Charlotte, NC\nDesc: Car Lockout\n",
        vehicle_rules,
    )
    cr = storage.classify_incoming(conn, job)
    job_id = storage.save_audit_entry(conn, job, cr, final_action="Approve")

    service = DispatcherService(conn)
    service.record_technician_update(TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="omw now"))
    service.prepare_company_update(job_id, "Going")

    rows = conn.execute(
        "SELECT luxor_message FROM events WHERE job_id = ? AND final_action = 'Company Update Prepared'", (job_id,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "Going"


# --------------------------------------------------------- fixtures scan
def test_all_new_test_fixtures_never_open_the_real_database_path():
    """Spot-check: none of this round's new test files ever construct
    the real database's literal path, or call storage.connect() with no
    path argument (which would default to the real DB_PATH) -- checked
    via AST (an actual zero-argument Call node), not a substring match,
    which would also match legitimate docstring prose mentioning the
    function name (e.g. test_schema_migration.py's own module
    docstring, which explains this exact contract in prose)."""
    import ast

    new_test_files = (
        "tests/test_schema_migration.py", "tests/test_backup_restore.py", "tests/test_health.py",
        "tests/test_e2e_lifecycle.py", "tests/test_concurrency.py", "tests/test_transaction_fault_injection.py",
        "tests/test_application_service.py", "tests/test_dependency_boundaries.py", "tests/test_schema_guard_ui.py",
    )
    for f in new_test_files:
        path = REPO_ROOT / f
        source = path.read_text(encoding="utf-8")
        assert "data/dispatcher.db" not in source

        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "storage"
            ):
                assert node.args or node.keywords, f"{f} calls storage.connect() with no path argument -- would open the real database"
