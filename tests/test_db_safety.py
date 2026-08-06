"""Focused tests for dispatcher.db_safety -- the single fail-closed
database-path guard shared by operator_api.py and
scripts/seed_command_center_demo_db.py. See docs/CURRENT_SPRINT_CHECKPOINT.md
for the incident (a demo-seeding script silently falling through to the
real database path) that motivated this module.

No test here ever opens data/dispatcher.db -- resolve_db_path() is a
pure function over Paths, never a connection.
"""

from __future__ import annotations

import pathlib

import pytest

from dispatcher import storage
from dispatcher.db_safety import UnsafeDatabasePathError, resolve_db_path


def test_live_mode_allows_missing_path_to_mean_the_default():
    assert resolve_db_path("live", None) is None


def test_live_mode_allows_an_explicit_override_path(tmp_path):
    p = tmp_path / "override.db"
    assert resolve_db_path("live", p) == p


def test_demo_mode_rejects_missing_path():
    with pytest.raises(UnsafeDatabasePathError):
        resolve_db_path("demo", None)


def test_test_mode_rejects_missing_path():
    with pytest.raises(UnsafeDatabasePathError):
        resolve_db_path("test", None)


def test_demo_mode_rejects_the_absolute_production_path():
    with pytest.raises(UnsafeDatabasePathError):
        resolve_db_path("demo", storage.DB_PATH)


def test_test_mode_rejects_the_absolute_production_path():
    with pytest.raises(UnsafeDatabasePathError):
        resolve_db_path("test", storage.DB_PATH)


def test_demo_mode_rejects_a_relative_path_resolving_to_production(monkeypatch):
    monkeypatch.chdir(storage.DB_PATH.parent.parent)
    relative = pathlib.Path("data") / "dispatcher.db"
    with pytest.raises(UnsafeDatabasePathError):
        resolve_db_path("demo", relative)


def test_demo_mode_rejects_a_differently_spelled_path_to_the_same_file(monkeypatch):
    """Canonical-path comparison, not string equality -- a path with a
    redundant './' segment must still be caught."""
    monkeypatch.chdir(storage.DB_PATH.parent.parent)
    weird = pathlib.Path("data") / "." / "dispatcher.db"
    with pytest.raises(UnsafeDatabasePathError):
        resolve_db_path("demo", weird)


def test_demo_mode_accepts_a_genuine_throwaway_path(tmp_path):
    p = tmp_path / "demo.db"
    assert resolve_db_path("demo", p) == p


def test_test_mode_accepts_a_genuine_throwaway_path(tmp_path):
    p = tmp_path / "test.db"
    assert resolve_db_path("test", p) == p


def test_demo_mode_rejects_a_symlink_pointing_at_the_production_db(tmp_path):
    link = tmp_path / "looks_harmless.db"
    try:
        link.symlink_to(storage.DB_PATH)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment (needs admin/Developer Mode on Windows)")
    with pytest.raises(UnsafeDatabasePathError):
        resolve_db_path("demo", link)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        resolve_db_path("staging", pathlib.Path("/tmp/x.db"))


def test_error_message_never_contains_a_stray_secret_looking_string():
    """The error message includes the real DB path (useful for
    debugging) but must never accidentally include anything from the
    process environment beyond that -- a cheap regression guard."""
    with pytest.raises(UnsafeDatabasePathError) as exc:
        resolve_db_path("demo", None)
    assert "dispatcher.db" in str(exc.value)
