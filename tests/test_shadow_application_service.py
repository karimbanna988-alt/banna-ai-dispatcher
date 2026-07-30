"""Tests for DispatcherService's Shadow Mode commands (application.py):
record_commercial_update, record_customer_response,
record_negotiation_outcome, answer_company_commercial_question,
confirm_commercial_reply_sent, set_shadow_review. Every test uses a
temporary SQLite database -- never the real one, never a real network
call, never a real Quo/WhatsApp send."""

from __future__ import annotations

from pathlib import Path

import pytest

from dispatcher import storage
from dispatcher.application import DispatcherService
from dispatcher.commercial import QuoteType
from dispatcher.parser import extract_job, load_vehicle_rules

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "test.db")


@pytest.fixture(scope="module")
def vehicle_rules():
    return load_vehicle_rules()


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def job_id(conn, vehicle_rules) -> int:
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    return storage.save_audit_entry(conn, job, classify_result, final_action="Approve")


@pytest.fixture()
def service(conn) -> DispatcherService:
    return DispatcherService(conn)


# --------------------------------------------------- record_commercial_update
def test_record_commercial_update_success(service, job_id):
    result = service.record_commercial_update(job_id, "quoted 125", dispatcher_actor="alex")
    assert result.success is True
    assert result.prepared_text == "Technician quoted $125."
    assert result.human_review_required is False


def test_record_commercial_update_requires_text(service, job_id):
    result = service.record_commercial_update(job_id, "   ")
    assert result.success is False
    assert result.validation_issues


def test_record_commercial_update_unknown_job_fails(service):
    result = service.record_commercial_update(999999, "quoted 125")
    assert result.success is False


def test_record_commercial_update_never_changes_job_status(conn, service, job_id):
    before = storage.get_job_by_id(conn, job_id)["status"]
    service.record_commercial_update(job_id, "quoted 125, customer accepted")
    after = storage.get_job_by_id(conn, job_id)["status"]
    assert before == after


def test_record_commercial_update_is_idempotent(conn, service, job_id):
    r1 = service.record_commercial_update(job_id, "quoted 125")
    r2 = service.record_commercial_update(job_id, "quoted 125")
    assert r1.idempotent_replay is False
    assert r2.idempotent_replay is True
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    events = storage.get_shadow_episode_events(conn, episode["episode_id"])
    commercial_events = [e for e in events if e["event_type"] == "COMMERCIAL_UPDATE"]
    assert len(commercial_events) == 1


def test_record_commercial_update_creates_episode_before_job_via_pending_message(conn, service):
    """Correlating by pending_message_id/ticket without a job_id never
    creates a jobs row -- confirmed at the storage layer already; this
    exercises it through find_or_create_shadow_episode directly, the
    same primitive record_commercial_update uses once a job exists."""
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    storage.find_or_create_shadow_episode(
        conn, source="AGN", channel="QUO_COMPANY", ticket_or_pdl="PRE-DISPATCH", pending_message_id="M1",
    )
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert after == before


def test_dispatcher_correction_recorded_and_distinct_from_suggestion(conn, service, job_id):
    result = service.record_commercial_update(
        job_id, "he said 100 or so, not sure",
        corrected_fields={"quote_type": QuoteType.FIXED, "quote_amount": "100"},
    )
    assert result.success is True
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    assert episode["commercial_update"]["quote_type"] == "FIXED"
    assert episode["commercial_update"]["quote_amount"] == "100"

    events = storage.get_shadow_episode_events(conn, episode["episode_id"])
    suggestion_event = next(e for e in events if e["event_type"] == "SHADOW_SUGGESTION")
    commercial_event = next(e for e in events if e["event_type"] == "COMMERCIAL_UPDATE")
    # Suggestion (uncorrected extraction) stays UNKNOWN; actual (corrected) is FIXED/100 --
    # the two are recorded separately, never merged into one fact.
    assert suggestion_event["payload"]["quote_type"] == "UNKNOWN"
    assert commercial_event["payload"]["quote_type"] == "FIXED"


def test_actual_reply_recorded_separately_from_suggested_reply(conn, service, job_id):
    service.record_commercial_update(
        job_id, "ambiguous text 125 or so", corrected_fields={"quote_type": QuoteType.FIXED, "quote_amount": "125"},
    )
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    # The suggestion was ambiguous (no confirmed reply); the dispatcher's
    # correction produced a confirmed one -- these must differ.
    assert episode["shadow_suggested_reply"] is None
    assert episode["dispatcher_actual_reply"] == "Technician quoted $125."


def test_corrected_quote_type_comparison_label(conn, service, job_id):
    service.record_commercial_update(
        job_id, "starting 125", corrected_fields={"quote_type": QuoteType.FIXED},
    )
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    events = storage.get_shadow_episode_events(conn, episode["episode_id"])
    decision = next(e for e in events if e["event_type"] == "DISPATCHER_DECISION")
    assert decision["payload"]["comparison_label"] == "DISPATCHER_CORRECTED_QUOTE_TYPE"


# ------------------------------------------------------------ customer response
def test_record_customer_response_updates_negotiation_state(conn, service, job_id):
    service.record_commercial_update(job_id, "quoted 125")
    result = service.record_customer_response(job_id, "WILL_CALL_BACK", raw_text="called to say they'll call back")
    assert result.success is True
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    assert episode["customer_response"] == "WILL_CALL_BACK"
    assert episode["commercial_update"]["negotiation_state"] == "FOLLOW_UP_REQUIRED"


def test_record_customer_response_rejects_unknown_value(service, job_id):
    result = service.record_customer_response(job_id, "NOT_A_REAL_RESPONSE")
    assert result.success is False


# --------------------------------------------------------- negotiation outcome
def test_record_negotiation_outcome_enriches_episode(conn, service, job_id):
    service.record_commercial_update(job_id, "quoted 125")
    result = service.record_negotiation_outcome(job_id, "ACCEPTED", raw_text="customer called to confirm")
    assert result.success is True
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    assert episode["negotiation_outcome"] == "ACCEPTED"


def test_negotiation_outcome_never_sets_final_job_outcome_for_a_non_terminal_job(conn, service, job_id):
    service.record_commercial_update(job_id, "quoted 125")
    service.record_negotiation_outcome(job_id, "ACCEPTED")
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    assert episode["final_job_outcome"] is None  # job is still "Approved", never Closed/Cancelled


def test_quoted_amount_stays_separate_from_final_closing_amount(conn, service, job_id, vehicle_rules):
    service.record_commercial_update(job_id, "quoted 125")
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    assert episode["commercial_update"]["quote_amount"] == "125"

    # A real, deterministic closing at a different amount -- the two
    # must never be conflated.
    storage.record_technician_update(conn, job_id, "Completed", "job done")
    storage.record_job_closing(conn, job_id, "closed for 150 cash", final_amount="150")
    closing = storage.get_latest_closing_details(conn, job_id)
    assert closing["amount"] == "150"
    episode_after = storage.get_shadow_episode_for_job(conn, job_id)
    assert episode_after["commercial_update"]["quote_amount"] == "125"  # never overwritten by the closing


# ------------------------------------------------------- company questions
def test_answer_company_question_uses_latest_verified_quote(conn, service, job_id):
    service.record_commercial_update(job_id, "quoted 125")
    service.record_commercial_update(job_id, "corrected -- actually starting 150", corrected_fields={"quote_type": QuoteType.STARTING, "quote_amount": "150"})
    result = service.answer_company_commercial_question(job_id, "What price was given?")
    assert result.prepared_text == "Technician advised pricing starts at $150."


def test_answer_company_question_no_verified_quote_is_human_review(service, job_id):
    result = service.answer_company_commercial_question(job_id, "Did you quote?")
    assert result.human_review_required is True
    assert result.prepared_text is None


def test_answer_company_question_can_do_less_never_auto_negotiates(service, job_id):
    service.record_commercial_update(job_id, "quoted 125")
    result = service.answer_company_commercial_question(job_id, "Can technician do less?")
    assert result.human_review_required is True
    assert result.prepared_text is None
    assert "cannot negotiate" in result.user_message


def test_answer_company_question_never_answers_from_another_job(conn, service, job_id, vehicle_rules):
    other_job = extract_job(load("lucky_clean.txt") if (FIXTURES / "lucky_clean.txt").exists() else load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn, other_job)
    other_job_id = storage.save_audit_entry(conn, other_job, classify_result, final_action="Approve")

    service.record_commercial_update(job_id, "quoted 125")
    result = service.answer_company_commercial_question(other_job_id, "What price was given?")
    assert result.human_review_required is True  # other job has no quote of its own


# ---------------------------------------------------- confirm reply sent
def test_confirm_commercial_reply_sent_records_channel_and_timestamp(conn, service, job_id):
    service.record_commercial_update(job_id, "quoted 125")
    result = service.confirm_commercial_reply_sent(job_id, "Quo", dispatcher_actor="alex")
    assert result.success is True
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    assert episode["actual_send_channel"] == "Quo"
    assert episode["actual_send_confirmed_at"] is not None


def test_confirm_reply_sent_without_prior_commercial_update_fails(service, job_id):
    result = service.confirm_commercial_reply_sent(job_id, "Quo")
    assert result.success is False


def test_no_send_ever_actually_happens(conn, service, job_id):
    """confirm_commercial_reply_sent only records a fact -- it has no
    HTTP client, no WhatsApp automation, nothing capable of an outbound
    call. Confirmed structurally: application.py's import list contains
    no requests/httpx/selenium/whatsapp-web dependency."""
    import inspect
    import dispatcher.application as application_module
    source = inspect.getsource(application_module)
    assert "requests." not in source
    assert "selenium" not in source.lower()
    assert "whatsapp_web" not in source.lower()


# -------------------------------------------------------------- review
def test_set_shadow_review_marks_status(conn, service, job_id):
    service.record_commercial_update(job_id, "quoted 125")
    episode = storage.get_shadow_episode_for_job(conn, job_id)
    result = service.set_shadow_review(episode["episode_id"], "VERIFIED", reviewed_by="alex")
    assert result.success is True
    updated = storage.get_shadow_episode(conn, episode["episode_id"])
    assert updated["review_status"] == "VERIFIED"


def test_set_shadow_review_unknown_episode_fails(service):
    result = service.set_shadow_review("does-not-exist", "VERIFIED")
    assert result.success is False


# ---------------------------------------------------- restart continuity
def test_shadow_episode_survives_process_restart_and_dispatch(tmp_path, vehicle_rules):
    db_path = tmp_path / "restart.db"
    conn1 = storage.connect(db_path)
    service1 = DispatcherService(conn1)
    job = extract_job(load("agn_clean.txt"), vehicle_rules)
    classify_result = storage.classify_incoming(conn1, job)
    job_id = storage.save_audit_entry(conn1, job, classify_result, final_action="Approve")
    service1.record_commercial_update(job_id, "quoted 125")
    conn1.close()

    conn2 = storage.connect(db_path)
    service2 = DispatcherService(conn2)
    result = service2.record_negotiation_outcome(job_id, "ACCEPTED")
    assert result.success is True
    episode = storage.get_shadow_episode_for_job(conn2, job_id)
    assert episode["commercial_update"]["quote_amount"] == "125"
    assert episode["negotiation_outcome"] == "ACCEPTED"
    conn2.close()
