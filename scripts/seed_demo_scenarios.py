"""Seeds a throwaway, on-disk SQLite database with a small set of named,
end-to-end demo scenarios -- each one drives a job through the real
route handlers / DispatcherService methods (operator_api.route_*,
service.confirm_technician_accepted, etc.), never hand-built rows, so
the seeded data exercises the real deterministic pipeline exactly like
scripts/seed_command_center_demo_db.py already does. NEVER touches
data/dispatcher.db -- pass a path under a temp/demo directory.

Scenarios:
  A -- Happy path: AGN job all the way through to COMPLETE.
  B -- Human review: no eligible technician for the job's area/service.
  C -- Duplicate/correction: a second message for the same ticket
       updates the existing job (upsert by source+ticket), never
       creates a second row.
  D -- Technician restriction: an ineligible technician is rejected
       with a visible reason; a genuinely eligible one is still
       assignable right after.
  E -- Incomplete closing: a closing recorded without a structured
       amount never reaches "ready to send" -- prepare_company_closing()
       refuses with a specific missing-fields message.
  F -- Technician decline: a captured "declined" reply is confirmed,
       moving the job to HUMAN_REVIEW_NEEDED (needs reassignment).

Usage:
    python scripts/seed_demo_scenarios.py --db-path /tmp/demo.db
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))


def future_ts(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def agn_message(ticket: str, area: str = "Charlotte, NC") -> str:
    return f"Ref: AGN Locksmith Services\nPDL: {ticket}\nPh: 1-555-010-3000\nAddr: 100 Demo St, {area}\nDesc: Car Lockout\n"


def main_leads_message(ticket: str, area: str = "Nowhere We Cover, ZZ") -> str:
    """No source-level K friction (Main Leads never requires one -- see
    docs/workflow.md section 1) -- used for Scenario B so the "no
    eligible technician" blocker is the one actually being demonstrated,
    not masked behind an unrelated, unacknowledged K step."""
    return (
        f"Power By Ucallz App\n\nDemo Locksmith Pro\nTicket: {ticket}\n"
        f"Name: Demo Customer\nPhone: (555) 010-2100\nAddress: 900 Nowhere Rd, {area}\nDesc: House Lockout\n"
    )


def _prepare_env(db_path: Path) -> Path:
    from dispatcher.db_safety import resolve_db_path

    db_path = resolve_db_path("demo", db_path)
    os.environ["LUXOR_DEMO_MODE"] = "true"
    os.environ["OPERATOR_API_DB_PATH"] = str(db_path)
    return db_path


def seed_scenario_a_happy_path(db_path: Path, ticket: str = "SCEN-A") -> dict:
    """Steps 1-18 of the non-negotiable workflow, end to end, via real
    route/service calls only. Returns identifying info for assertions."""
    db_path = _prepare_env(db_path)
    import operator_api as api
    import whatsapp_bridge_adapter
    from dispatcher import storage
    from dispatcher.app_models import ClosingCommand, TechnicianUpdateCommand
    from dispatcher.rules.service_areas import load_service_areas

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    tech_id = storage.add_technician(
        conn, name="Demo Scenario Tech", areas=["Charlotte"], skills=["car lockout"],
        dispatch_group="Demo Scenario Dispatch", closing_group="Demo Scenario Closings",
    )
    conn.close()

    import luxor_ops_shared as shared

    message_id = f"scen-a-{ticket}"
    # 1-2. AGN job arrives, gets parsed/reviewed (real ingestion + classify path).
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id=message_id, conversation_id=f"c-{message_id}",
        group_label="AGN", raw_text=agn_message(ticket), from_number="+15550109900", received_at=future_ts(1),
    )
    # Multiple scenarios may run back-to-back in the same process, inside
    # luxor_ops_shared's 5-second queue cache TTL -- clear explicitly so
    # this scenario's freshly-inserted message is never served stale.
    shared.clear_pending_caches()
    # 3-4. Company K prepared (implicit -- the text is already renderable
    # from k_required/policy.py) and mock-marked sent.
    ack_result = api.route_acknowledge(None, {"message_id": message_id}, {})
    assert ack_result["success"], ack_result

    # 5-7. Technician dispatch prepared, mock-marked sent (confirm_whatsapp_dispatch
    # both commits the job AND records the dispatch milestone).
    api.route_prepare_dispatch(None, {"message_id": message_id}, {})
    dispatch_result = api.route_confirm_dispatch(None, {"message_id": message_id}, {})
    job_id = dispatch_result["job_id"]

    # "Technician selected" -- assign_technician() is the real, separate,
    # explicit action for this (see docs/CURRENT_SPRINT_CHECKPOINT.md's
    # prior-sprint note on why this isn't folded into dispatch).
    conn = storage.connect(db_path)
    assignment = storage.assign_technician(conn, job_id, tech_id, load_service_areas())
    conn.close()
    assert assignment.outcome == "assigned", assignment

    # 8-9. Technician K: a captured WhatsApp observation, classified, confirmed accepted.
    k_message_id = f"scen-a-{ticket}-k"
    whatsapp_bridge_adapter.add_observation({
        "event_type": "incoming_message", "message_id": k_message_id, "group_id": "g-scen-a",
        "group_name": "Demo Scenario Dispatch", "sender": "1555", "timestamp": "1700000000",
        "text": "K", "extracted_ticket": ticket,
    })
    conn = storage.connect(db_path)
    service = api._service(conn)
    accept_result = service.confirm_technician_accepted(job_id, k_message_id)
    conn.close()
    assert accept_result.success, accept_result

    # 10. Technician update recorded.
    conn = storage.connect(db_path)
    service = api._service(conn)
    update_result = service.record_technician_update(
        TechnicianUpdateCommand(job_id=job_id, update_type="Going", raw_update="on my way")
    )
    conn.close()
    assert update_result.success, update_result

    # 11-12. Company update prepared, mock-marked sent.
    conn = storage.connect(db_path)
    service = api._service(conn)
    prepare_update = service.prepare_company_update(job_id, "Going")
    assert prepare_update.success, prepare_update
    confirm_update = service.confirm_company_update_sent(job_id, prepare_update.prepared_text)
    conn.close()
    assert confirm_update.success, confirm_update

    # 13-14. Closing received, final amount validated (record_closing IS the validation step).
    conn = storage.connect(db_path)
    service = api._service(conn)
    closing_result = service.record_closing(
        ClosingCommand(job_id=job_id, raw_closing="Closed $125 CC", final_amount="125", payment_method="CC")
    )
    conn.close()
    assert closing_result.success, closing_result

    # 15-16. Closing prepared, mock-marked sent.
    conn = storage.connect(db_path)
    service = api._service(conn)
    prepare_closing = service.prepare_company_closing(job_id)
    assert prepare_closing.success, prepare_closing
    confirm_closing = service.confirm_company_closing_sent(job_id, prepare_closing.prepared_text)
    conn.close()
    assert confirm_closing.success, confirm_closing

    # 17. Job completed.
    conn = storage.connect(db_path)
    service = api._service(conn)
    complete_result = service.complete_job(job_id)
    conn.close()
    assert complete_result.success, complete_result

    # 18. KPI/audit: read back and confirm the audit trail + reconciliation state.
    conn = storage.connect(db_path)
    job = storage.get_job_by_id(conn, job_id)
    history = storage.get_audit_timeline_by_job_id(conn, job_id)
    conn.close()
    assert job["status"] == "Closed"
    assert job["reconciliation_state"] == "Settled"

    return {"job_id": job_id, "ticket": ticket, "status": job["status"], "audit_event_count": len(history)}


def seed_scenario_b_human_review(db_path: Path, ticket: str = "SCEN-B") -> dict:
    """No technician is configured/eligible for this job's area at all
    -- the pending-message projection must return HUMAN_REVIEW_NEEDED,
    never a guessed assignment."""
    db_path = _prepare_env(db_path)
    import operator_api as api
    from dispatcher import storage

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    # Deliberately NO technician added -- eligible_count will be 0 for
    # any area/service, matching the "no automatic assignment" requirement.

    import luxor_ops_shared as shared

    message_id = f"scen-b-{ticket}"
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id=message_id, conversation_id=f"c-{message_id}",
        group_label="MAIN LEADS", raw_text=main_leads_message(ticket), from_number="+15550109901", received_at=future_ts(1),
    )
    shared.clear_pending_caches()
    from dispatcher.workflow_projection import derive_pending_message_next_action
    import luxor_desk_read_model as rm

    conn = storage.connect(db_path)
    live, _backlog, _other = rm.get_live_dispatch_queue(conn)
    entry = next(e for e in live if e.job.ticket_number.value == ticket)
    dto = api._new_job_dto(conn, entry)
    conn.close()
    projection = derive_pending_message_next_action({
        "needs_review": dto["needs_review"], "k_required": dto["k_required"], "k_sent": dto["k_sent"],
        "eligible_count": dto["eligible_count"], "dispatched": dto["dispatched"],
    })
    return {"ticket": ticket, "message_id": message_id, "next_action": projection.next_action, "human_review_needed": projection.human_review_needed}


def seed_scenario_f_technician_decline(db_path: Path, ticket: str = "SCEN-F") -> dict:
    """A technician K candidate is confirmed *declined* -- the acceptance
    state must record the decline, never a false accepted/completed
    state, and the job's next action must move to HUMAN_REVIEW_NEEDED
    (needs reassignment), not silently stay WAIT_TECHNICIAN_K."""
    db_path = _prepare_env(db_path)
    import operator_api as api
    import whatsapp_bridge_adapter
    from dispatcher import storage
    from dispatcher.rules.service_areas import load_service_areas

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    tech_id = storage.add_technician(
        conn, name="Demo Decline Tech", areas=["Charlotte"], skills=["car lockout"],
        dispatch_group="Demo Decline Dispatch", closing_group="Demo Decline Closings",
    )
    conn.close()

    import luxor_ops_shared as shared

    message_id = f"scen-f-{ticket}"
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id=message_id, conversation_id=f"c-{message_id}",
        group_label="AGN", raw_text=agn_message(ticket), from_number="+15550109902", received_at=future_ts(1),
    )
    shared.clear_pending_caches()
    api.route_acknowledge(None, {"message_id": message_id}, {})
    api.route_prepare_dispatch(None, {"message_id": message_id}, {})
    job_id = api.route_confirm_dispatch(None, {"message_id": message_id}, {})["job_id"]

    conn = storage.connect(db_path)
    storage.assign_technician(conn, job_id, tech_id, load_service_areas())
    conn.close()

    k_message_id = f"scen-f-{ticket}-k"
    whatsapp_bridge_adapter.add_observation({
        "event_type": "incoming_message", "message_id": k_message_id, "group_id": "g-scen-f",
        "group_name": "Demo Decline Dispatch", "sender": "1555", "timestamp": "1700000000",
        "text": "can't", "extracted_ticket": ticket,
    })
    conn = storage.connect(db_path)
    service = api._service(conn)
    decline_result = service.confirm_technician_declined(job_id, k_message_id, "Technician can't make it")
    conn.close()
    assert decline_result.success, decline_result

    from dispatcher.workflow_projection import derive_next_action
    conn = storage.connect(db_path)
    service = api._service(conn)
    job = storage.get_job_by_id(conn, job_id)
    import luxor_desk_read_model as rm
    all_jobs = rm.get_active_jobs_detailed(conn)
    this_job = next(j for j in all_jobs if j["id"] == job_id)
    projection = derive_next_action(api._build_workflow_context(conn, service, this_job, all_jobs))
    ta = storage.get_technician_acceptance(conn, job_id)
    conn.close()

    return {
        "job_id": job_id, "ticket": ticket, "next_action": projection.next_action,
        "accepted_at": ta["accepted_at"], "declined_at": ta["declined_at"],
    }


def seed_scenario_c_duplicate_and_correction(db_path: Path, ticket: str = "SCEN-C") -> dict:
    """A second raw message for the same (source, ticket) arrives after
    the job is already dispatched -- must update the existing job row,
    never create a second one (storage.save_audit_entry()'s
    ON CONFLICT(source, ticket_number) upsert is the real mechanism
    this proves, not a hand-rolled dedup check here)."""
    db_path = _prepare_env(db_path)
    import operator_api as api
    import luxor_ops_shared as shared
    from dispatcher import storage
    from dispatcher.rules.service_areas import load_service_areas

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    tech_id = storage.add_technician(
        conn, name="Demo Dup Tech", areas=["Charlotte"], skills=["car lockout"],
        dispatch_group="Demo Dup Dispatch", closing_group="Demo Dup Closings",
    )
    conn.close()

    # First arrival -- dispatched normally.
    message_id_1 = f"scen-c-{ticket}-1"
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id=message_id_1, conversation_id=f"c-{message_id_1}",
        group_label="AGN", raw_text=agn_message(ticket), from_number="+15550109910", received_at=future_ts(1),
    )
    shared.clear_pending_caches()
    api.route_acknowledge(None, {"message_id": message_id_1}, {})
    api.route_prepare_dispatch(None, {"message_id": message_id_1}, {})
    first_job_id = api.route_confirm_dispatch(None, {"message_id": message_id_1}, {})["job_id"]
    conn = storage.connect(db_path)
    storage.assign_technician(conn, first_job_id, tech_id, load_service_areas())
    conn.close()

    # Second arrival, same ticket, a corrected phone number -- a
    # genuine "correction" message, not a byte-identical retry. Goes
    # through the exact same NEW-message pipeline; the upsert-by-ticket
    # key is what keeps this from becoming a second job.
    message_id_2 = f"scen-c-{ticket}-2"
    corrected_text = agn_message(ticket).replace("1-555-010-3000", "1-555-010-9999")
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id=message_id_2, conversation_id=f"c-{message_id_2}",
        group_label="AGN", raw_text=corrected_text, from_number="+15550109910", received_at=future_ts(2),
    )
    shared.clear_pending_caches()
    # This second message re-enters at the New Jobs review stage (a
    # fresh NEW-type classification for the same ticket) -- approve it
    # the same way a dispatcher reviewing a correction would.
    ack2 = api.route_acknowledge(None, {"message_id": message_id_2}, {})
    api.route_prepare_dispatch(None, {"message_id": message_id_2}, {})
    second_result = api.route_confirm_dispatch(None, {"message_id": message_id_2}, {})
    second_job_id = second_result["job_id"]

    conn = storage.connect(db_path)
    all_jobs_with_ticket = conn.execute(
        "SELECT id FROM jobs WHERE source = 'AGN' AND ticket_number = ?", (ticket,)
    ).fetchall()
    history = storage.get_audit_timeline_by_job_id(conn, first_job_id)
    latest_job = storage.get_job_by_id(conn, first_job_id)
    conn.close()

    return {
        "ticket": ticket, "first_job_id": first_job_id, "second_job_id": second_job_id,
        "distinct_job_row_count": len(all_jobs_with_ticket), "audit_event_count": len(history),
        "latest_phone_on_record": latest_job["fields"].get("phone"),
    }


def seed_scenario_d_technician_restriction(db_path: Path, ticket: str = "SCEN-D") -> dict:
    """The first technician configured is deliberately ineligible for
    this job's area -- assign_technician() must reject them with a
    visible reason (never silently succeed), and a second, genuinely
    eligible technician must still be assignable right after."""
    db_path = _prepare_env(db_path)
    import operator_api as api
    from dispatcher import storage
    from dispatcher.rules.service_areas import load_service_areas

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    wrong_tech_id = storage.add_technician(
        conn, name="Demo Wrong-Area Tech", areas=["Savannah"], skills=["car lockout"],
        dispatch_group="Demo Wrong Dispatch", closing_group="Demo Wrong Closings",
    )
    right_tech_id = storage.add_technician(
        conn, name="Demo Right-Area Tech", areas=["Charlotte"], skills=["car lockout"],
        dispatch_group="Demo Right Dispatch", closing_group="Demo Right Closings",
    )
    conn.close()

    message_id = f"scen-d-{ticket}"
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id=message_id, conversation_id=f"c-{message_id}",
        group_label="AGN", raw_text=agn_message(ticket, area="Charlotte, NC"), from_number="+15550109911", received_at=future_ts(1),
    )
    import luxor_ops_shared as shared
    shared.clear_pending_caches()
    api.route_acknowledge(None, {"message_id": message_id}, {})
    api.route_prepare_dispatch(None, {"message_id": message_id}, {})
    job_id = api.route_confirm_dispatch(None, {"message_id": message_id}, {})["job_id"]

    service_areas = load_service_areas()
    conn = storage.connect(db_path)
    rejected = storage.assign_technician(conn, job_id, wrong_tech_id, service_areas)
    conn.close()

    conn = storage.connect(db_path)
    accepted = storage.assign_technician(conn, job_id, right_tech_id, service_areas)
    conn.close()

    return {
        "job_id": job_id, "ticket": ticket,
        "rejected_outcome": rejected.outcome, "rejected_reason": rejected.reason,
        "accepted_outcome": accepted.outcome, "accepted_technician": accepted.technician_name,
    }


def seed_scenario_e_incomplete_closing(db_path: Path, ticket: str = "SCEN-E") -> dict:
    """A closing is recorded with the technician's raw text but no
    structured final amount -- prepare_company_closing() (the real gate
    on the "send to company" step) must refuse with a clear, specific
    "missing: amount, payment method" message rather than fabricate
    either. The job stays Closed (a real closing WAS recorded) but never
    reaches "ready to send.\""""
    db_path = _prepare_env(db_path)
    import operator_api as api
    import luxor_ops_shared as shared
    from dispatcher import storage
    from dispatcher.app_models import ClosingCommand
    from dispatcher.rules.service_areas import load_service_areas

    conn = storage.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO console_activation_state (id, live_queue_started_at, updated_at) VALUES (1, ?, ?)",
        ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    tech_id = storage.add_technician(
        conn, name="Demo Incomplete Tech", areas=["Charlotte"], skills=["car lockout"],
        dispatch_group="Demo Incomplete Dispatch", closing_group="Demo Incomplete Closings",
    )
    conn.close()

    message_id = f"scen-e-{ticket}"
    storage.save_pending_and_mark_processed(
        storage.connect(db_path), message_id=message_id, conversation_id=f"c-{message_id}",
        group_label="AGN", raw_text=agn_message(ticket), from_number="+15550109912", received_at=future_ts(1),
    )
    shared.clear_pending_caches()
    api.route_acknowledge(None, {"message_id": message_id}, {})
    api.route_prepare_dispatch(None, {"message_id": message_id}, {})
    job_id = api.route_confirm_dispatch(None, {"message_id": message_id}, {})["job_id"]
    conn = storage.connect(db_path)
    storage.assign_technician(conn, job_id, tech_id, load_service_areas())
    conn.close()

    conn = storage.connect(db_path)
    service = api._service(conn)
    # The technician's actual message is recorded verbatim -- final_amount
    # and payment_method are deliberately omitted, never invented.
    closing_result = service.record_closing(
        ClosingCommand(job_id=job_id, raw_closing="Done, will confirm the amount later", final_amount=None, payment_method=None)
    )
    assert closing_result.success, closing_result
    prepare_result = service.prepare_company_closing(job_id)
    job = storage.get_job_by_id(conn, job_id)
    conn.close()

    return {
        "job_id": job_id, "ticket": ticket, "job_status": job["status"],
        "prepare_succeeded": prepare_result.success, "human_review_reason": prepare_result.human_review_reason.detail if prepare_result.human_review_reason else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()
    p = Path(args.db_path)
    result_a = seed_scenario_a_happy_path(p)
    result_b = seed_scenario_b_human_review(p)
    result_c = seed_scenario_c_duplicate_and_correction(p)
    result_d = seed_scenario_d_technician_restriction(p)
    result_e = seed_scenario_e_incomplete_closing(p)
    result_f = seed_scenario_f_technician_decline(p)
    print(f"Scenario A (happy path): {result_a}")
    print(f"Scenario B (human review): {result_b}")
    print(f"Scenario C (duplicate/correction): {result_c}")
    print(f"Scenario D (technician restriction): {result_d}")
    print(f"Scenario E (incomplete closing): {result_e}")
    print(f"Scenario F (technician decline): {result_f}")
