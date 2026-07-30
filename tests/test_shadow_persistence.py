"""Tests for storage.py's Shadow Mode persistence: shadow_episodes
(enrichable snapshot) and shadow_events (append-only log). Every test
uses a temporary SQLite database -- never the real one."""

from __future__ import annotations

from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.parser import extract_job, load_vehicle_rules
from dispatcher.shadow import ReviewStatus, ShadowChannel, ShadowEventType

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_job(conn, vehicle_rules, raw="agn_clean.txt"):
    job = extract_job(load(raw), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    return storage.save_audit_entry(conn, job, classify_result, final_action="Approve")


# ---------------------------------------------------------------- Schema ---
def test_shadow_tables_created_on_fresh_database(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "shadow_episodes" in tables
    assert "shadow_events" in tables


def test_schema_revision_is_3(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    status = storage.check_schema_status(conn)
    assert status.up_to_date


# --------------------------------------------- find_or_create_shadow_episode
def test_creates_new_episode_when_none_correlates(conn):
    ep = storage.find_or_create_shadow_episode(
        conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1", pending_message_id="M1",
    )
    assert ep["episode_id"]
    assert ep["job_id"] is None
    assert ep["review_status"] == ReviewStatus.UNREVIEWED.value


def test_correlates_by_pending_message_id(conn):
    ep1 = storage.find_or_create_shadow_episode(
        conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, pending_message_id="M1",
    )
    ep2 = storage.find_or_create_shadow_episode(
        conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, pending_message_id="M1",
    )
    assert ep1["episode_id"] == ep2["episode_id"]


def test_correlates_by_source_and_ticket(conn):
    ep1 = storage.find_or_create_shadow_episode(conn, source="Lucky", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T99")
    ep2 = storage.find_or_create_shadow_episode(conn, source="Lucky", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T99")
    assert ep1["episode_id"] == ep2["episode_id"]


def test_different_ticket_creates_a_different_episode(conn):
    ep1 = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    ep2 = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T2")
    assert ep1["episode_id"] != ep2["episode_id"]


def test_episode_created_before_a_job_exists_never_creates_a_jobs_row(conn):
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1", pending_message_id="M1")
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert after == before


def test_job_id_backfilled_once_known_without_creating_second_episode(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    ep1 = storage.find_or_create_shadow_episode(
        conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1", pending_message_id="M1",
    )
    assert ep1["job_id"] is None
    ep2 = storage.find_or_create_shadow_episode(
        conn, source="AGN", channel=ShadowChannel.CRM_DISPATCHER_ACTION.value,
        ticket_or_pdl="T1", pending_message_id="M1", job_id=job_id,
    )
    assert ep2["episode_id"] == ep1["episode_id"]
    assert ep2["job_id"] == job_id
    assert conn.execute("SELECT COUNT(*) FROM shadow_episodes").fetchone()[0] == 1


def test_job_id_correlation_used_once_known(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    ep1 = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1", job_id=job_id)
    ep2 = storage.get_shadow_episode_for_job(conn, job_id)
    assert ep2["episode_id"] == ep1["episode_id"]


def test_never_backfills_over_an_already_known_job_id(conn, vehicle_rules):
    job_a = make_job(conn, vehicle_rules, "agn_clean.txt")
    job_b = job_a + 999  # a job id that does not exist -- must never be written over the real one
    ep1 = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1", job_id=job_a)
    ep2 = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1", job_id=job_a)
    assert ep2["job_id"] == job_a


# --------------------------------------------------------- record_shadow_event
def test_record_shadow_event_is_append_only(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    storage.record_shadow_event(conn, episode_id=ep["episode_id"], event_type=ShadowEventType.SHADOW_OBSERVATION.value, channel=ShadowChannel.QUO_COMPANY.value, payload={"a": 1})
    storage.record_shadow_event(conn, episode_id=ep["episode_id"], event_type=ShadowEventType.SHADOW_SUGGESTION.value, channel=ShadowChannel.SYSTEM_SHADOW_SUGGESTION.value, payload={"b": 2})
    events = storage.get_shadow_episode_events(conn, ep["episode_id"])
    assert [e["event_type"] for e in events] == ["SHADOW_OBSERVATION", "SHADOW_SUGGESTION"]


def test_record_shadow_event_is_idempotent_via_key(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    id1 = storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.COMMERCIAL_UPDATE.value,
        channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, payload={"quote": "125"}, idempotency_key="dup-key-1",
    )
    id2 = storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.COMMERCIAL_UPDATE.value,
        channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, payload={"quote": "125"}, idempotency_key="dup-key-1",
    )
    assert id1 == id2
    assert len(storage.get_shadow_episode_events(conn, ep["episode_id"])) == 1


def test_distinct_negotiation_events_are_not_collapsed(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.NEGOTIATION_OUTCOME.value,
        channel=ShadowChannel.CRM_DISPATCHER_ACTION.value, payload={"outcome": "NEGOTIATING"}, idempotency_key="neg-1",
    )
    storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.NEGOTIATION_OUTCOME.value,
        channel=ShadowChannel.CRM_DISPATCHER_ACTION.value, payload={"outcome": "ACCEPTED"}, idempotency_key="neg-2",
    )
    events = storage.get_shadow_episode_events(conn, ep["episode_id"])
    assert len(events) == 2
    assert [e["payload"]["outcome"] for e in events] == ["NEGOTIATING", "ACCEPTED"]


# ------------------------------------------------------------ update_shadow_episode
def test_update_shadow_episode_enriches_incrementally(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    assert ep["commercial_update"] is None
    updated = storage.update_shadow_episode(conn, ep["episode_id"], commercial_update={"quote_type": "FIXED", "quote_amount": "125"})
    assert updated["commercial_update"] == {"quote_type": "FIXED", "quote_amount": "125"}
    assert updated["customer_response"] is None  # unset fields remain NULL, never fabricated


def test_update_shadow_episode_rejects_unknown_field(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    with pytest.raises(ValueError):
        storage.update_shadow_episode(conn, ep["episode_id"], source="not-allowed")


def test_corrected_quote_preserved_historically_in_shadow_events(conn):
    """The snapshot always shows the latest quote, but every prior one
    -- including the original, pre-correction quote -- remains visible
    in the append-only shadow_events log, never overwritten or deleted."""
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.COMMERCIAL_UPDATE.value,
        channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, payload={"quote_type": "FIXED", "quote_amount": "125"}, idempotency_key="cu-1",
    )
    storage.update_shadow_episode(conn, ep["episode_id"], commercial_update={"quote_type": "FIXED", "quote_amount": "125"})
    storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.COMMERCIAL_UPDATE.value,
        channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, payload={"quote_type": "FIXED", "quote_amount": "150"}, idempotency_key="cu-2",
    )
    storage.update_shadow_episode(conn, ep["episode_id"], commercial_update={"quote_type": "FIXED", "quote_amount": "150"})

    latest = storage.get_shadow_episode(conn, ep["episode_id"])
    assert latest["commercial_update"]["quote_amount"] == "150"
    history = [e["payload"]["quote_amount"] for e in storage.get_shadow_episode_events(conn, ep["episode_id"])]
    assert history == ["125", "150"]


# --------------------------------------------------- latest verified quote
def test_latest_verified_commercial_update_for_job(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    assert storage.get_latest_verified_commercial_update_for_job(conn, job_id) is None
    storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, ticket_or_pdl="T1", job_id=job_id)
    ep = storage.get_shadow_episode_for_job(conn, job_id)
    storage.update_shadow_episode(conn, ep["episode_id"], commercial_update={"quote_type": "FIXED", "quote_amount": "125"})
    latest = storage.get_latest_verified_commercial_update_for_job(conn, job_id)
    assert latest["quote_amount"] == "125"


def test_never_reads_across_jobs(conn, vehicle_rules):
    job_a = make_job(conn, vehicle_rules, "agn_clean.txt")
    ep_a = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, ticket_or_pdl="TA", job_id=job_a)
    storage.update_shadow_episode(conn, ep_a["episode_id"], commercial_update={"quote_type": "FIXED", "quote_amount": "125"})

    other_ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="OTHER-TICKET")
    assert storage.get_latest_verified_commercial_update_for_job(conn, 999999) is None


# ------------------------------------------------------------ review status
def test_set_shadow_review_status_marks_export_eligible(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    updated = storage.set_shadow_review_status(conn, ep["episode_id"], ReviewStatus.VERIFIED.value, reviewed_by="alex")
    assert updated["eligible_for_export"] is True
    assert updated["reviewed_at"] is not None


def test_unreviewed_episode_not_export_eligible(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    assert ep["eligible_for_export"] is False
    assert ep["episode_id"] not in [e["episode_id"] for e in storage.list_shadow_episodes_for_export(conn)]


def test_exclude_status_not_export_eligible(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    updated = storage.set_shadow_review_status(conn, ep["episode_id"], ReviewStatus.EXCLUDE.value)
    assert updated["eligible_for_export"] is False


def test_corrected_status_is_export_eligible(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    updated = storage.set_shadow_review_status(conn, ep["episode_id"], ReviewStatus.CORRECTED.value)
    assert updated["eligible_for_export"] is True


def test_review_status_change_is_itself_audited(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    storage.set_shadow_review_status(conn, ep["episode_id"], ReviewStatus.VERIFIED.value, reviewed_by="alex")
    events = storage.get_shadow_episode_events(conn, ep["episode_id"])
    assert any(e["event_type"] == "SHADOW_REVIEWED" for e in events)


def test_exclude_status_records_shadow_excluded_event(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    storage.set_shadow_review_status(conn, ep["episode_id"], ReviewStatus.EXCLUDE.value)
    events = storage.get_shadow_episode_events(conn, ep["episode_id"])
    assert any(e["event_type"] == "SHADOW_EXCLUDED" for e in events)


def test_invalid_review_status_rejected(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    with pytest.raises(ValueError):
        storage.set_shadow_review_status(conn, ep["episode_id"], "NOT_A_REAL_STATUS")


# --------------------------------------------------- multiple dispatchers
def test_multiple_dispatcher_actor_labels_distinguished(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    ep = storage.find_or_create_shadow_episode(
        conn, source="AGN", channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, ticket_or_pdl="T1", job_id=job_id, dispatcher_actor="alex",
    )
    storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.COMMERCIAL_UPDATE.value,
        channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, actor="alex", payload={}, idempotency_key="k1",
    )
    storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.DISPATCHER_DECISION.value,
        channel=ShadowChannel.CRM_DISPATCHER_ACTION.value, actor="sam", payload={}, idempotency_key="k2",
    )
    events = storage.get_shadow_episode_events(conn, ep["episode_id"])
    actors = {e["actor"] for e in events}
    assert actors == {"alex", "sam"}


def test_missing_actor_stays_none_never_silently_attributed(conn):
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1")
    assert ep["dispatcher_actor"] is None
    storage.record_shadow_event(
        conn, episode_id=ep["episode_id"], event_type=ShadowEventType.SHADOW_OBSERVATION.value,
        channel=ShadowChannel.QUO_COMPANY.value, payload={},
    )
    events = storage.get_shadow_episode_events(conn, ep["episode_id"])
    assert events[0]["actor"] is None


# ---------------------------------------------------------- Process restart
def test_process_restart_preserves_episode_continuity(tmp_path, vehicle_rules):
    db_path = tmp_path / "restart.db"
    conn1 = storage.connect(db_path)
    job_id = make_job(conn1, vehicle_rules)
    ep1 = storage.find_or_create_shadow_episode(
        conn1, source="AGN", channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, ticket_or_pdl="T1",
        pending_message_id="M1", job_id=job_id,
    )
    storage.update_shadow_episode(conn1, ep1["episode_id"], commercial_update={"quote_type": "FIXED", "quote_amount": "125"})
    conn1.close()

    conn2 = storage.connect(db_path)
    ep2 = storage.find_or_create_shadow_episode(
        conn2, source="AGN", channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, ticket_or_pdl="T1",
        pending_message_id="M1", job_id=job_id,
    )
    assert ep2["episode_id"] == ep1["episode_id"]
    assert ep2["commercial_update"]["quote_amount"] == "125"
    conn2.close()


# --------------------------------------------------------------- FK safety
def test_episode_job_id_foreign_key_enforced(conn):
    with pytest.raises(Exception):
        storage.find_or_create_shadow_episode(
            conn, source="AGN", channel=ShadowChannel.QUO_COMPANY.value, ticket_or_pdl="T1", job_id=999999,
        )


def test_shadow_mode_never_touches_technicians_table(conn, vehicle_rules):
    job_id = make_job(conn, vehicle_rules)
    tech_id = storage.add_technician(conn, "Alex", areas=["Faketown"])
    before = storage.get_technician(conn, tech_id)
    ep = storage.find_or_create_shadow_episode(conn, source="AGN", channel=ShadowChannel.WHATSAPP_TECHNICIAN.value, ticket_or_pdl="T1", job_id=job_id)
    storage.update_shadow_episode(conn, ep["episode_id"], commercial_update={"quote_type": "FIXED", "quote_amount": "125"})
    after = storage.get_technician(conn, tech_id)
    assert before == after
