"""Tests for the whole-suite production-database tripwire in conftest.py.

The tripwire was changed from a file mtime/size check to a per-table
content check so a release run could complete unambiguously while the
live WhatsApp bridge keeps capturing. These tests prove that change did
not weaken it: a test writing to real business data must still fail the
run, and a test editing an existing observation must still fail the run.

None of these touch the real database. They build throwaway databases in
tmp_path and drive the snapshot helper directly.
"""
import sqlite3

import pytest

import conftest as root_conftest


def _make_db(path, observations=2, jobs=1):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, ticket TEXT, status TEXT)")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("CREATE TABLE technicians (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE whatsapp_group_registry (group_id TEXT PRIMARY KEY, role TEXT)")
    conn.execute("CREATE TABLE whatsapp_pending_observations (id INTEGER PRIMARY KEY, text TEXT)")
    for i in range(jobs):
        conn.execute("INSERT INTO jobs VALUES (?,?,?)", (i + 1, f"T{i}", "Approved"))
    for i in range(observations):
        conn.execute("INSERT INTO whatsapp_pending_observations VALUES (?,?)", (i + 1, f"msg{i}"))
    conn.commit()
    conn.close()
    return path


def test_snapshot_is_stable_when_nothing_changes(tmp_path):
    db = _make_db(tmp_path / "d.db")
    assert root_conftest._snapshot_real_db(db) == root_conftest._snapshot_real_db(db)


def test_snapshot_returns_none_for_a_missing_database(tmp_path):
    assert root_conftest._snapshot_real_db(tmp_path / "nope.db") is None


# ------------------------------------------------- must still FAIL ----
def test_a_write_to_business_data_is_still_detected(tmp_path):
    """The property that must never regress: a test that changes a real
    job has to be caught."""
    db = _make_db(tmp_path / "d.db")
    before = root_conftest._snapshot_real_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE jobs SET status='Closed' WHERE id=1")
    conn.commit(); conn.close()
    after = root_conftest._snapshot_real_db(db)
    assert after["jobs"]["digest"] != before["jobs"]["digest"]


def test_inserting_a_job_is_detected(tmp_path):
    db = _make_db(tmp_path / "d.db")
    before = root_conftest._snapshot_real_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES (99,'NEW','New')")
    conn.commit(); conn.close()
    after = root_conftest._snapshot_real_db(db)
    assert after["jobs"]["digest"] != before["jobs"]["digest"]


def test_a_registry_mapping_change_is_detected(tmp_path):
    """Group mappings are business truth -- a test must never alter one."""
    db = _make_db(tmp_path / "d.db")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO whatsapp_group_registry VALUES ('g@g.us','UNMAPPED_WORK')")
    conn.commit(); conn.close()
    before = root_conftest._snapshot_real_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE whatsapp_group_registry SET role='TECH_DISPATCH'")
    conn.commit(); conn.close()
    after = root_conftest._snapshot_real_db(db)
    assert after["whatsapp_group_registry"]["digest"] != before["whatsapp_group_registry"]["digest"]


def test_modifying_an_existing_observation_is_still_detected(tmp_path):
    """Live capture only appends. Editing a row that already existed is a
    test writing to production, and the append-only allowance must not
    hide it."""
    db = _make_db(tmp_path / "d.db")
    before = root_conftest._snapshot_real_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE whatsapp_pending_observations SET text='tampered' WHERE id=1")
    conn.commit(); conn.close()
    bounds = {t: v["max_id"] for t, v in before.items() if v["max_id"] is not None}
    after = root_conftest._snapshot_real_db(db, bounds=bounds)
    assert after["whatsapp_pending_observations"]["digest"] != before["whatsapp_pending_observations"]["digest"]


def test_deleting_an_observation_is_still_detected(tmp_path):
    db = _make_db(tmp_path / "d.db")
    before = root_conftest._snapshot_real_db(db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM whatsapp_pending_observations WHERE id=2")
    conn.commit(); conn.close()
    bounds = {t: v["max_id"] for t, v in before.items() if v["max_id"] is not None}
    after = root_conftest._snapshot_real_db(db, bounds=bounds)
    assert after["whatsapp_pending_observations"]["digest"] != before["whatsapp_pending_observations"]["digest"]


# ----------------------------------------------- must be TOLERATED ----
def test_live_capture_appending_a_new_observation_is_tolerated(tmp_path):
    """The whole point of the change: the bridge capturing a real message
    mid-run is correct behaviour, not a test failure."""
    db = _make_db(tmp_path / "d.db")
    before = root_conftest._snapshot_real_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO whatsapp_pending_observations VALUES (3,'a real new message')")
    conn.commit(); conn.close()
    # Compare the same row range, exactly as the fixture teardown does.
    bounds = {t: v["max_id"] for t, v in before.items() if v["max_id"] is not None}
    after = root_conftest._snapshot_real_db(db, bounds=bounds)
    # Pre-existing rows unchanged, and the table grew.
    assert after["whatsapp_pending_observations"]["digest"] == before["whatsapp_pending_observations"]["digest"]
    assert after["whatsapp_pending_observations"]["max_id"] > before["whatsapp_pending_observations"]["max_id"]
    # Business truth untouched.
    assert after["jobs"]["digest"] == before["jobs"]["digest"]


def test_business_tables_are_not_in_the_append_only_allowance():
    """Guard against someone later adding a business table to the
    tolerated set, which would silently gut the tripwire."""
    for table in ("jobs", "events", "technicians", "whatsapp_group_registry"):
        assert table not in root_conftest._LIVE_APPEND_ONLY_TABLES
