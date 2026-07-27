"""Unit tests for Quo polling/dedup logic. Uses a hand-built stub in place
of QuoClient -- poll_for_new_messages takes the client as a parameter, so
no real network access or real client construction is needed.

Confirmed 2026-07-27: polling targets a small, known, configured list of
dispatch-group contacts directly (rules/quo_dispatch_groups.yaml) rather
than discovering conversations generically -- generic discovery was found
to lag real message activity by over an hour in production."""

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

    queued = poll_for_new_messages(conn, client, config=config)

    lucky_messages = [q for q in queued if q.group_label == "Lucky"]
    assert len(lucky_messages) == 1
    assert lucky_messages[0].message_id == "MSG1"
    assert lucky_messages[0].raw_text == msg.text


def test_outbound_message_is_not_queued(conn, config):
    outbound = make_message("MSG1", direction="outgoing", text="Closed\n$150 CC")
    client = StubClient(
        {"C_LUCKY": make_contact("C_LUCKY", "Lucky"), "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")},
        {"+15551234567": [outbound]},
    )

    queued = poll_for_new_messages(conn, client, config=config)

    assert queued == []


def test_message_already_processed_is_not_requeued(conn, config):
    contact = make_contact("C_LUCKY", "Lucky")
    msg = make_message("MSG1")
    client = StubClient({"C_LUCKY": contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {"+15551234567": [msg]})

    first = poll_for_new_messages(conn, client, config=config)
    second = poll_for_new_messages(conn, client, config=config)

    assert len(first) == 1
    assert second == []


def test_every_configured_group_is_polled(conn, config):
    lucky_contact = make_contact("C_LUCKY", "Lucky", "+15551111111")
    agn_contact = make_contact("C_AGN", "AGN", "+15552222222")
    lucky_msg = make_message("MSG1", from_number="+15551111111")
    agn_msg = make_message("MSG2", text="Ref: AGN Locksmith Services\nPDL: Y", from_number="+15552222222")
    client = StubClient(
        {"C_LUCKY": lucky_contact, "C_AGN": agn_contact},
        {"+15551111111": [lucky_msg], "+15552222222": [agn_msg]},
    )

    queued = poll_for_new_messages(conn, client, config=config)

    assert {q.group_label for q in queued} == {"Lucky", "AGN"}
    assert client.get_contact_calls == ["C_LUCKY", "C_AGN"]


def test_contact_with_no_phone_number_is_skipped(conn, config):
    empty_contact = Contact(id="C_LUCKY", first_name="Lucky", last_name=None, company=None, phone_numbers=[])
    client = StubClient({"C_LUCKY": empty_contact, "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")}, {})

    queued = poll_for_new_messages(conn, client, config=config)

    assert queued == []
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

    assert second == []  # still deduped, checkpoint/processed-ids survived


def test_message_with_no_text_is_marked_processed_but_not_queued(conn, config):
    empty_msg = make_message("MSG1", text=None)
    client = StubClient(
        {"C_LUCKY": make_contact("C_LUCKY", "Lucky"), "C_AGN": make_contact("C_AGN", "AGN", "+15559999999")},
        {"+15551234567": [empty_msg]},
    )

    queued = poll_for_new_messages(conn, client, config=config)
    assert queued == []
    assert storage.is_quo_message_processed(conn, "MSG1") is True
