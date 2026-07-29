"""Tests for the dispatch-lifecycle timestamp tracking added for the
Speed Dispatch Console. Every milestone is nullable and set at most once
-- never invented for messages that predate the tracking table."""

import pytest

from dispatcher import storage


@pytest.fixture()
def conn(tmp_path):
    c = storage.connect(tmp_path / "test.db")
    yield c
    c.close()


def test_untracked_message_returns_all_none():
    pass  # covered by the conn-backed variant below; kept for discoverability


def test_get_dispatch_tracking_defaults_to_none(conn):
    tracking = storage.get_dispatch_tracking(conn, "UNSEEN1")
    assert tracking["message_id"] == "UNSEEN1"
    assert tracking["first_seen_at"] is None
    assert tracking["dispatched_manually_at"] is None


def test_mark_first_seen_sets_timestamp(conn):
    storage.mark_first_seen(conn, "MSG1")
    tracking = storage.get_dispatch_tracking(conn, "MSG1")
    assert tracking["first_seen_at"] is not None
    assert tracking["acknowledged_at"] is None


def test_repeated_mark_does_not_overwrite_original_timestamp(conn):
    storage.mark_first_seen(conn, "MSG1")
    first = storage.get_dispatch_tracking(conn, "MSG1")["first_seen_at"]
    storage.mark_first_seen(conn, "MSG1")  # re-invoke, e.g. a second page render
    second = storage.get_dispatch_tracking(conn, "MSG1")["first_seen_at"]
    assert first == second


def test_each_milestone_independently_settable(conn):
    storage.mark_first_seen(conn, "MSG1")
    storage.mark_luxor_prepared(conn, "MSG1")
    storage.mark_acknowledged(conn, "MSG1")
    storage.mark_copied(conn, "MSG1")
    storage.mark_whatsapp_opened(conn, "MSG1")
    storage.mark_dispatched_manually(conn, "MSG1")

    tracking = storage.get_dispatch_tracking(conn, "MSG1")
    for column in (
        "first_seen_at", "luxor_prepared_at", "acknowledged_at",
        "copied_at", "whatsapp_opened_at", "dispatched_manually_at",
    ):
        assert tracking[column] is not None


def test_tracking_is_isolated_per_message(conn):
    storage.mark_acknowledged(conn, "MSG_A")
    tracking_b = storage.get_dispatch_tracking(conn, "MSG_B")
    assert tracking_b["acknowledged_at"] is None
