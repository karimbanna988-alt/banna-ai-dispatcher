"""Unit tests for Quo polling/dedup logic. Uses a hand-built stub in place
of QuoClient -- poll_for_new_messages takes the client as a parameter, so
no real network access or real client construction is needed.

Confirmed 2026-07-27: polling targets a small, known, configured list of
dispatch-group contacts directly (rules/quo_dispatch_groups.yaml) rather
than discovering conversations generically -- generic discovery was found
to lag real message activity by over an hour in production."""

import sqlite3

import pytest

from dispatcher import storage
from dispatcher.quo_client import Contact, QuoMessage
from dispatcher.quo_ingest import (
    DispatchGroupConfig,
    QuoPollConfig,
    load_dispatch_groups,
    poll_for_new_messages,
)


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture()
def config():
    return QuoPollConfig(
        our_phone_number_id="PNtest",
        dispatch_groups=[
            DispatchGroupConfig(label="Lucky", quo_contact_id="C_LUCKY"),
            DispatchGroupConfig(label="AGN", quo_contact_id="C_AGN"),
        ],
    )


class StubClient:
    def __init__(self, contacts_by_id, messages_by_phone):
        self._contacts = contacts_by_id
        self._messages = messages_by_phone
        self.get_contact_calls = []
        self.iter_messages_calls = []

    def get_contact(self, contact_id):
        self.get_contact_calls.append(contact_id)
        return self._contacts[contact_id]

    def iter_messages(self, *, phone_number_id, participants, **filters):
        self.iter_messages_calls.append({"phone_number_id": phone_number_id, "participants": participants, **filters})
        key = participants[0] if participants else None
        return iter(self._messages.get(key, []))


def make_contact(contact_id, label, phone="+15551234567"):
    return Contact(id=contact_id, first_name=label, last_name=None, company=None, phone_numbers=[phone])


def make_message(msg_id, direction="incoming", text="Co: LUCKY LOCKSMITH LLC\nPDL: X", from_number="+15551234567"):
    return QuoMessage(
        id=msg_id, to="PNus", from_number=from_number, text=text,
        phone_number_id="PNtest", conversation_id="CONV1", direction=direction, status="delivered",
        created_at="2026-07-27T20:00:00Z", updated_at="2026-07-27T20:00:00Z",
    )


def test_real_config_file_loads_the_four_confirmed_groups():
    loaded = load_dispatch_groups()
    assert loaded.our_phone_number_id
    labels = {g.label for g in loaded.dispatch_groups}
    assert labels == {"Lucky", "CLOSINGS", "AGN", "Main Leads 2"}


def test_inbound_message_is_queued_with_its_group_label(conn, config):
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})

    outcome = poll_for_new_messages(conn, client, config=config)

    lucky_messages = [q for q in outcome.queued if q.group_label == "Lucky"]
    assert len(lucky_messages) == 1
    assert lucky_messages[0].message_id == "MSG1"
    assert lucky_messages[0].raw_text == msg.text


def test_outbound_message_is_not_queued(conn, config):
    outbound = make_message("MSG1", direction="outgoing", text="Closed\n$150 CC")
    client = StubClient(
        {"C_LUCKY": make_contact("C_LUCKY", "Lucky"), "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")},
        {"+15551234567": [outbound]},
    )

    outcome = poll_for_new_messages(conn, client, config=config)

    assert outcome.queued == []


def test_message_already_processed_is_not_requeued(conn, config):
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})

    first = poll_for_new_messages(conn, client, config=config)
    second = poll_for_new_messages(conn, client, config=config)

    assert len(first.queued) == 1
    assert second.queued == []


def test_every_configured_group_is_polled(conn, config):
    lucky_contact = make_contact("C_LUCKY", "Lucky", "+15551111111")
    agn_contact = make_contact("C_AGN", "AGN", "+15552222222")
    lucky_msg = make_message("MSG1", from_number="+15551111111")
    agn_msg = make_message("MSG2", text="Ref: AGN Locksmith Services\nPDL: Y", from_number="+15552222222")
    client = StubClient(
        {"C_LUCKY": lucky_contact, "C_AGN": agn_contact},
        {"+15551111111": [lucky_msg], "+15552222222": [agn_msg]},
    )

    outcome = poll_for_new_messages(conn, client, config=config)

    assert {q.group_label for q in outcome.queued} == {"Lucky", "AGN"}
    assert client.get_contact_calls == ["C_LUCKY", "C_AGN"]


def test_contact_with_no_phone_number_is_skipped(conn, config):
    empty_contact = Contact(id="C_LUCKY", first_name="Lucky", last_name=None, company=None, phone_numbers=[])
    client = StubClient({"C_LUCKY": empty_contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {})

    outcome = poll_for_new_messages(conn, client, config=config)

    assert outcome.queued == []
    assert client.iter_messages_calls == [] or all(c["participants"] != [] for c in client.iter_messages_calls)


def test_first_poll_uses_lookback_not_full_history(conn, config, monkeypatch):
    monkeypatch.setenv("QUO_POLL_INITIAL_LOOKBACK_MINUTES", "5")
    client = StubClient(
        {"C_LUCKY": make_contact("C_LUCKY", "Lucky"), "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {},
    )

    assert storage.get_quo_checkpoint(conn) is None
    poll_for_new_messages(conn, client, config=config)

    filters_used = client.iter_messages_calls[0]
    assert filters_used["created_after"] is not None


def test_checkpoint_persists_across_a_simulated_restart(tmp_path, config):
    db_path = tmp_path / "restart_test.db"
    conn1 = storage.connect(db_path)
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client1 = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})
    poll_for_new_messages(conn1, client1, config=config)

    # Simulate an app restart: fresh connection to the same db file.
    conn2 = storage.connect(db_path)
    client2 = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})
    second = poll_for_new_messages(conn2, client2, config=config)

    assert second.queued == []  # still deduped, checkpoint/processed-ids survived


def test_message_with_no_text_is_marked_processed_but_not_queued(conn, config):
    """Documented, deliberate decision: a message with no usable text has
    nothing worth persisting durably, so it keeps the pre-existing
    metadata-only "processed" marker instead of a quo_pending_messages row."""
    empty_msg = make_message("MSG1", text=None)
    client = StubClient(
        {"C_LUCKY": make_contact("C_LUCKY", "Lucky"), "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")},
        {"+15551234567": [empty_msg]},
    )

    outcome = poll_for_new_messages(conn, client, config=config)
    assert outcome.queued == []
    assert storage.is_quo_message_processed(conn, "MSG1") is True
    assert storage.get_pending_quo_messages(conn) == []


# --------------------------------------------- Durable queue (Phase 1 CRM) --
def test_successful_poll_writes_a_durable_pending_row(conn, config):
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})

    poll_for_new_messages(conn, client, config=config)

    pending = storage.get_pending_quo_messages(conn)
    assert len(pending) == 1
    assert pending[0]["message_id"] == "MSG1"
    assert pending[0]["raw_text"] == msg.text
    assert pending[0]["status"] == "pending"


def test_pending_queue_survives_a_simulated_restart(tmp_path, config):
    db_path = tmp_path / "restart_queue_test.db"
    conn1 = storage.connect(db_path)
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client1 = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})
    poll_for_new_messages(conn1, client1, config=config)

    # Fresh connection to the same file -- nothing here relies on any
    # in-memory/session state.
    conn2 = storage.connect(db_path)
    pending = storage.get_pending_quo_messages(conn2)
    assert len(pending) == 1
    assert pending[0]["message_id"] == "MSG1"
    assert pending[0]["raw_text"] == msg.text


def test_failed_persistence_is_not_marked_processed_and_holds_checkpoint(conn, config, monkeypatch):
    """Failure injection: simulate a database write failure for one message
    mid-poll. It must not be marked processed (so it isn't silently lost),
    and the poll checkpoint must not advance past it (so the next poll's
    time window still covers it)."""
    contact = make_contact("C_LUCKY", "Lucky")
    ok_msg = make_message("MSG-OK")
    bad_msg = make_message("MSG-BAD")
    client = StubClient(
        {"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")},
        {"+15551234567": [ok_msg, bad_msg]},
    )

    real_save = storage.save_pending_and_mark_processed

    def flaky_save(conn_, *, message_id, **kwargs):
        if message_id == "MSG-BAD":
            raise sqlite3.OperationalError("simulated database write failure")
        return real_save(conn_, message_id=message_id, **kwargs)

    monkeypatch.setattr(storage, "save_pending_and_mark_processed", flaky_save)

    outcome = poll_for_new_messages(conn, client, config=config)

    assert outcome.failed_count == 1
    assert {q.message_id for q in outcome.queued} == {"MSG-OK"}
    assert storage.is_quo_message_processed(conn, "MSG-OK") is True
    assert storage.is_quo_message_processed(conn, "MSG-BAD") is False
    assert [p["message_id"] for p in storage.get_pending_quo_messages(conn)] == ["MSG-OK"]
    # Checkpoint must not have advanced this poll -- still None (first poll).
    assert storage.get_quo_checkpoint(conn) is None


def test_failed_message_is_retried_on_next_poll_without_duplicating_the_succeeded_one(conn, config, monkeypatch):
    contact = make_contact("C_LUCKY", "Lucky")
    ok_msg = make_message("MSG-OK")
    bad_msg = make_message("MSG-BAD")
    client = StubClient(
        {"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")},
        {"+15551234567": [ok_msg, bad_msg]},
    )

    real_save = storage.save_pending_and_mark_processed

    def flaky_save(conn_, *, message_id, **kwargs):
        if message_id == "MSG-BAD":
            raise sqlite3.OperationalError("simulated database write failure")
        return real_save(conn_, message_id=message_id, **kwargs)

    monkeypatch.setattr(storage, "save_pending_and_mark_processed", flaky_save)
    poll_for_new_messages(conn, client, config=config)  # first poll: MSG-BAD fails

    monkeypatch.setattr(storage, "save_pending_and_mark_processed", real_save)  # "fixed" now
    second = poll_for_new_messages(conn, client, config=config)  # retry, same stub data

    assert {q.message_id for q in second.queued} == {"MSG-BAD"}  # only the retried one
    ids = sorted(p["message_id"] for p in storage.get_pending_quo_messages(conn))
    assert ids == ["MSG-BAD", "MSG-OK"]  # MSG-OK not duplicated


def test_load_next_marks_pending_row_loaded(conn, config):
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})
    poll_for_new_messages(conn, client, config=config)

    next_item = storage.get_next_pending_quo_message(conn)
    assert next_item["message_id"] == "MSG1"
    storage.mark_quo_pending_message_loaded(conn, "MSG1")

    reloaded = storage.get_pending_quo_messages(conn)
    assert reloaded[0]["status"] == "loaded"
    assert reloaded[0]["loaded_at"] is not None


def test_loaded_row_interrupted_by_restart_is_recovered_by_load_next(conn, config):
    """A row left 'loaded' (app/laptop restart mid-review, never actioned)
    must still be returned by get_next_pending_quo_message -- not stranded
    outside the pending/loaded queue forever."""
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})
    poll_for_new_messages(conn, client, config=config)
    storage.mark_quo_pending_message_loaded(conn, "MSG1")  # simulate: loaded, then restart happened

    recovered = storage.get_next_pending_quo_message(conn)
    assert recovered["message_id"] == "MSG1"
    assert recovered["status"] == "loaded"


def test_actioned_row_is_no_longer_in_the_pending_queue(conn, config):
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})
    poll_for_new_messages(conn, client, config=config)
    storage.mark_quo_pending_message_loaded(conn, "MSG1")

    storage.mark_quo_pending_message_actioned(conn, "MSG1")

    assert storage.get_pending_quo_messages(conn) == []
    assert storage.count_pending_quo_messages(conn) == 0
    assert storage.get_next_pending_quo_message(conn) is None
