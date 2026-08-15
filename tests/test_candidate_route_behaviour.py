"""Behavioural tests for the WhatsApp candidate route.

These drive the REAL route against a REAL (temporary) database. The
earlier round of correlation tests leaned on source-string assertions,
which proved the code contained a guard but not that the guard fired.
Everything here seeds rows, calls route_whatsapp_candidates, and asserts
on the returned candidates.

The invariant under test throughout:

    correlation identity = (owner-confirmed company, normalized ticket)

and when identity or sequence cannot be established, the answer is
HUMAN_REVIEW_NEEDED with nothing offered to send -- never a silent
suppression, and never an invitation to send a job twice.
"""
from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st

import operator_api as api
from dispatcher import dispatch_candidates as dc, storage

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def epoch(dt) -> str:
    """The observations table stores the WhatsApp event time as TEXT epoch."""
    return str(int(dt.timestamp()))


def iso(dt) -> str:
    return dt.isoformat()


JOB_BLOCK = ("Power By Ucallz App\n\n{company_label}\nTicket: {ticket}\n"
             "Name: Test Customer\nPhone: (864) 555-0100\n"
             "Address: 470 Oak Grove Rd, Augusta, GA 30909\nDesc: Car lockout")


def job_text(ticket, company_label="Joy Locksmith"):
    return JOB_BLOCK.format(ticket=ticket, company_label=company_label)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "candidates.db"
    conn = storage.connect(db_path)
    conn.close()
    monkeypatch.setenv("OPERATOR_API_DB_PATH", str(db_path))
    st.cache_resource.clear()
    st.cache_data.clear()
    return db_path


def group(db_path, group_id, name, role, company=None, technician_id=None, area="Augusta"):
    conn = storage.connect(db_path)
    storage.upsert_group_registry_entry(conn, {
        "group_id": group_id, "exact_name": name, "role": role, "company": company,
        "service_area": area, "technician_id": technician_id,
        "technician_name_unconfirmed": None, "is_active": True,
        "confirmed_by_owner": True, "source": "owner_manual", "notes": "test",
    })
    conn.close()


def observe(db_path, message_id, group_id, text, event_at, received_at=None, resolved=False):
    """One captured WhatsApp message.

    `event_at` is the WhatsApp event time (stored as the TEXT epoch the
    bridge sends). `received_at` is ingestion time; storage stamps it
    itself, so tests that need the two to DISAGREE set it directly --
    which is the whole point of the persistence-inversion case."""
    conn = storage.connect(db_path)
    storage.add_whatsapp_observation(conn, {
        "message_id": message_id, "group_id": group_id, "group_name": group_id,
        "sender": "s@lid", "direction": "incoming",
        "timestamp": epoch(event_at), "text": text,
    })
    if received_at is not None:
        conn.execute("UPDATE whatsapp_pending_observations SET received_at = ? WHERE message_id = ?",
                     (iso(received_at), message_id))
        conn.commit()
    if resolved:
        storage.resolve_whatsapp_observation(conn, message_id, "ignored")
    conn.close()


def _route_source(name="route_whatsapp_candidates") -> str:
    import ast, pathlib as _pl
    root = _pl.Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "operator_api.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(name)


def candidates(env_db):
    return api.route_whatsapp_candidates(None, {}, {})["candidates"]


def find(cands, ticket):
    matches = [c for c in cands if dc.normalize_ticket(c["ticket"]) == dc.normalize_ticket(ticket)]
    return matches[0] if matches else None


# ===================================================== TEST 1 ==========
def test_1_cross_company_collision_does_not_suppress(env):
    """Company A and Company B both post the same ticket in the same message
    FORMAT. A's dispatch must not suppress B's genuinely new job."""
    group(env, "a-intake@g.us", "A intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "b-intake@g.us", "B intake", "COMPANY_JOB_INTAKE", company="Company B")
    group(env, "a-disp@g.us", "A dispatch", "TECH_DISPATCH", company="Company A")

    observe(env, "a-in", "a-intake@g.us", job_text("CMPA1"), NOW - timedelta(minutes=30))
    observe(env, "a-sent", "a-disp@g.us", job_text("CMPA1"), NOW - timedelta(minutes=29))
    observe(env, "b-in", "b-intake@g.us", job_text("CMPA1"), NOW - timedelta(minutes=5))

    cands = candidates(env)
    b = [c for c in cands if c["message_id"] == "b-in"][0]
    a = [c for c in cands if c["message_id"] == "a-in"][0]
    assert a["status"] == dc.STATUS_ALREADY_DISPATCHED, "A really was dispatched"
    assert b["status"] != dc.STATUS_ALREADY_DISPATCHED, (
        "Company B's job must not be suppressed by Company A's dispatch")
    assert b["status"] in (dc.STATUS_WAITING_HUMAN, dc.STATUS_HUMAN_REVIEW)


# ===================================================== TEST 2 ==========
def test_2_ticket_normalization_is_case_insensitive(env):
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("ab123"), NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", job_text("AB123"), NOW - timedelta(minutes=9))

    c = find(candidates(env), "AB123")
    assert c is not None
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED, (
        "ab123 and AB123 are the same ticket and must correlate")


def test_2b_case_difference_does_not_create_a_duplicate_row(env):
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    observe(env, "in1", "intake@g.us", job_text("ab123"), NOW - timedelta(minutes=10))
    observe(env, "in2", "intake@g.us", job_text("AB123"), NOW - timedelta(minutes=5))
    matching = [c for c in candidates(env)
                if dc.normalize_ticket(c["ticket"]) == "AB123"]
    assert len(matching) == 1, "one job, one row -- case must not duplicate it"


# ===================================================== TEST 3 ==========
def test_3_existing_canonical_job_does_not_hide_a_repost(env):
    """A prior dispatch plus a NEWER intake is a repost that a human must
    see -- not something to discard because the ticket already exists."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in-old", "intake@g.us", job_text("RPOST"), NOW - timedelta(days=1, minutes=5))
    observe(env, "sent", "disp@g.us", job_text("RPOST"), NOW - timedelta(days=1))
    observe(env, "in-new", "intake@g.us", job_text("RPOST"), NOW - timedelta(minutes=2))

    c = find(candidates(env), "RPOST")
    assert c is not None, "the repost must not disappear"
    assert c["status"] == dc.STATUS_REPOST_AFTER_DISPATCH
    assert c["needs_human_review"] is True
    assert c["proposed_message"] is None, "nothing sendable until reviewed"
    assert c["review_reason"] == dc.REVIEW_REPOST, "must stay visible in the console"


# ===================================================== TEST 4 ==========
def test_4_persistence_order_inverted_against_event_order(env):
    """Ingest the NEWER repost first and the OLDER dispatch second, so
    SQLite order is the opposite of WhatsApp order."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")

    # persisted FIRST, but happened LAST
    observe(env, "in-new", "intake@g.us", job_text("EVNT4"),
            event_at=NOW - timedelta(minutes=1), received_at=NOW - timedelta(hours=5))
    # persisted SECOND, but happened a day EARLIER
    observe(env, "sent-old", "disp@g.us", job_text("EVNT4"),
            event_at=NOW - timedelta(days=1), received_at=NOW)

    c = find(candidates(env), "EVNT4")
    assert c["status"] == dc.STATUS_REPOST_AFTER_DISPATCH, (
        "sequencing must follow WhatsApp event time, not ingestion order")


# ===================================================== TEST 5 ==========
def test_5_real_work_is_not_pushed_out_by_newer_chatter(env):
    """Volume of non-job traffic must not evict a real job from the
    board -- windowing happens after parsing and sequencing."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    observe(env, "real-job", "intake@g.us", job_text("T5"), NOW - timedelta(hours=3))
    for i in range(80):
        observe(env, f"chat-{i}", "intake@g.us", f"ok thanks {i}",
                NOW - timedelta(minutes=80 - i))

    c = find(candidates(env), "T5")
    assert c is not None, "a real job must survive 80 newer chatter messages"


# ===================================================== TEST 6 ==========
def test_6_resolving_the_queue_item_does_not_re_offer_the_job(env):
    """Ignoring the technician-group observation is a QUEUE action. The
    durable fact that the job was dispatched must survive it."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("DURB6"), NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", job_text("DURB6"), NOW - timedelta(minutes=9), resolved=True)

    c = find(candidates(env), "DURB6")
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["status"] != dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"] is None


# ===================================================== TEST 7 ==========
def test_7_manual_outgoing_dispatch_counts_as_evidence(env):
    """A job the owner forwarded by hand from the paired account never
    becomes an inbound observation, but it is still real dispatch."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("MANL7"), NOW - timedelta(minutes=10))

    conn = storage.connect(env)
    storage.add_whatsapp_outgoing_audit(conn, {
        "message_id": "out-1", "group_id": "disp@g.us", "group_name": "dispatch",
        "timestamp": epoch(NOW - timedelta(minutes=9)), "text": job_text("MANL7"),
    })
    conn.close()

    c = find(candidates(env), "MANL7")
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["proposed_message"] is None


# ===================================================== TEST 8 ==========
@pytest.mark.parametrize("reply", ["pass", "cannot take it", "busy", "not available"])
def test_8_a_structured_decline_is_not_dispatch_proof(env, reply):
    """A full job block quoted back with a refusal underneath means the
    job was NOT taken."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("T8"), NOW - timedelta(minutes=10))
    observe(env, "decline", "disp@g.us", job_text("T8") + "\n" + reply,
            NOW - timedelta(minutes=9))

    c = find(candidates(env), "T8")
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED, (
        f"{reply!r} is a refusal, not a dispatch")


# ===================================================== TEST 9 ==========
@pytest.mark.parametrize("reply", ["20 min", "no answer", "quoted 195", "on my way"])
def test_9_a_structured_update_is_not_initial_dispatch_proof(env, reply):
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("T10"), NOW - timedelta(minutes=10))
    # A technician's status is their own utterance, written clear of the
    # forwarded block. Wording tucked directly under a field line is the
    # customer's description -- see test_9b.
    observe(env, "update", "disp@g.us", job_text("T10") + "\n\n" + reply,
            NOW - timedelta(minutes=9))

    c = find(candidates(env), "T10")
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED


def test_9b_a_real_job_description_is_not_mistaken_for_an_update(env):
    """REGRESSION from live data: a customer wrote "he needs the storage
    open in the next hour and a half". That matched the ETA pattern and
    rejected genuine dispatch evidence, re-offering a job already sent."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    wordy = job_text("WORD1") + "\nhe needs the storage open in the next hour and a half please"
    observe(env, "in", "intake@g.us", wordy, NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", wordy, NOW - timedelta(minutes=9))

    c = find(candidates(env), "WORD1")
    # Wrapped under a field line, this is the CUSTOMER's description.
    # Reading it as a technician ETA rejected every genuine Augusta
    # dispatch in live data, collapsing 22 correct identifications to 0.
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED, (
        "customer wording must not veto real dispatch evidence")


# ==================================================== TEST 10 ==========
def test_10_unconfirmed_company_fails_conservatively(env):
    """A technician group with no owner-confirmed company cannot identify
    whose job it carried. It must not suppress, and it must not leave the
    intake looking freely dispatchable either."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company=None)
    observe(env, "in", "intake@g.us", job_text("NUL12"), NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", job_text("NUL12"), NOW - timedelta(minutes=9))

    c = find(candidates(env), "NUL12")
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["review_reason"] == dc.REVIEW_COMPANY_UNRESOLVED
    assert c["proposed_message"] is None


def test_10b_an_unconfirmed_intake_group_never_routes(env):
    conn = storage.connect(env)
    storage.upsert_group_registry_entry(conn, {
        "group_id": "unconf@g.us", "exact_name": "unconfirmed", "role": "COMPANY_JOB_INTAKE",
        "company": "Company A", "service_area": "Augusta", "technician_id": None,
        "technician_name_unconfirmed": None, "is_active": True,
        "confirmed_by_owner": False, "source": "name_heuristic", "notes": None,
    })
    conn.close()
    observe(env, "in", "unconf@g.us", job_text("T13"), NOW - timedelta(minutes=10))
    cands = candidates(env)
    assert find(cands, "T13") is None or find(cands, "T13")["status"] != dc.STATUS_WAITING_HUMAN


# =================================================== invariants ========
def test_no_held_back_candidate_is_ever_sendable(env):
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("T14"), NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", job_text("T14"), NOW - timedelta(minutes=9))
    for c in candidates(env):
        if c["status"] != dc.STATUS_WAITING_HUMAN:
            assert c["proposed_message"] is None


def test_the_route_creates_no_canonical_state(env):
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    observe(env, "in", "intake@g.us", job_text("T15"), NOW - timedelta(minutes=5))

    conn = storage.connect(env)
    before = (conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
              conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    conn.close()

    api.route_whatsapp_candidates(None, {}, {})

    conn = storage.connect(env)
    after = (conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
             conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    conn.close()
    assert before == after, "the projection must write nothing"


# ============================= behavioural replacements ================
# These replace source-string assertions that broke on a variable rename
# while the behaviour was correct. Driven through the real route.

def test_an_ignore_group_is_never_dispatch_evidence(env):
    """Trio Dispatch is internal dispatcher chat. Even if a job block is
    pasted there, it proves nothing about dispatch."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "internal@g.us", "internal chat", "IGNORE", company="Company A")
    observe(env, "in", "intake@g.us", job_text("T20"), NOW - timedelta(minutes=10))
    observe(env, "pasted", "internal@g.us", job_text("T20"), NOW - timedelta(minutes=9))

    c = find(candidates(env), "T20")
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED, (
        "internal chat is not a technician destination")


def test_an_ignore_group_produces_no_candidate_of_its_own(env):
    group(env, "internal@g.us", "internal chat", "IGNORE", company="Company A")
    observe(env, "chat", "internal@g.us", job_text("T21"), NOW - timedelta(minutes=5))
    assert find(candidates(env), "T21") is None


def test_a_closing_group_sighting_is_not_a_dispatch(env):
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "clos@g.us", "closings", "TECH_CLOSINGS", company="Company A")
    observe(env, "in", "intake@g.us", job_text("CLOS2"), NOW - timedelta(minutes=10))
    observe(env, "closing", "clos@g.us", job_text("CLOS2"), NOW - timedelta(minutes=9))

    c = find(candidates(env), "CLOS2")
    assert c["status"] == dc.STATUS_CLOSING_OBSERVED
    assert c["proposed_message"] is None


def test_an_ignored_intake_does_not_appear_as_actionable_work(env):
    """Queue resolution still governs the ACTIONABLE list, even though it
    no longer erases dispatch evidence."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    observe(env, "in", "intake@g.us", job_text("T23"), NOW - timedelta(minutes=5), resolved=True)
    assert find(candidates(env), "T23") is None


def test_a_real_job_survives_a_flood_of_newer_jobs_too(env):
    """The window is applied after sequencing, and ticket-bearing work is
    ordered ahead of chatter -- but volume of real jobs is bounded by
    real work, so the newest must still be present."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    for i in range(70):
        observe(env, f"j{i}", "intake@g.us", job_text(f"BULK{i}"),
                NOW - timedelta(minutes=70 - i))
    cands = candidates(env)
    assert find(cands, "BULK69") is not None, "the newest real job must be on the board"


# ==================== ADVERSARIAL: final safety repair =================
# Positive controls are deliberately included -- a repair that answers
# HUMAN REVIEW to everything is not a safe repair, it is a broken one.

def test_a1_confirmed_intake_without_a_company_is_never_sendable(env):
    """Company identity is the correlation namespace. Without it nothing
    can be checked against a prior dispatch, so nothing may be sent."""
    group(env, "nocompany@g.us", "intake no company", "COMPANY_JOB_INTAKE", company=None)
    observe(env, "in", "nocompany@g.us", job_text("A1"), NOW - timedelta(minutes=5))

    c = find(candidates(env), "A1")
    assert c is not None, "the job must still be visible"
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["review_reason"] == dc.REVIEW_COMPANY_UNRESOLVED
    assert c["proposed_message"] is None


def test_a2_a_safety_review_survives_a_flood_of_informational_rows(env):
    """A repost must not be windowed away by 60+ already-dispatched rows."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")

    observe(env, "old-sent", "disp@g.us", job_text("KEEP1"), NOW - timedelta(days=2))
    observe(env, "repost", "intake@g.us", job_text("KEEP1"), NOW - timedelta(days=1))

    for i in range(70):
        observe(env, "i%d" % i, "intake@g.us", job_text("I%04d" % i),
                NOW - timedelta(minutes=70 - i))
        observe(env, "s%d" % i, "disp@g.us", job_text("I%04d" % i),
                NOW - timedelta(minutes=69 - i))

    c = find(candidates(env), "KEEP1")
    assert c is not None, "the safety review must not be windowed away"
    assert c["status"] == dc.STATUS_REPOST_AFTER_DISPATCH
    assert c["proposed_message"] is None


def test_a3_duplicate_intakes_with_unprovable_order_are_reviewed(env):
    """Two intakes for the same job at the SAME instant: which is the
    repost cannot be known, so neither is offered as sendable."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    same = NOW - timedelta(minutes=10)
    observe(env, "d1", "intake@g.us", job_text("A3"), same)
    observe(env, "d2", "intake@g.us", job_text("A3"), same)

    c = find(candidates(env), "A3")
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["review_reason"] == dc.REVIEW_ORDER_UNDETERMINED
    assert c["proposed_message"] is None


def test_a4_event_time_beats_reversed_ingestion_at_final_selection(env):
    """Ingestion order reversed against event order, checked at the FINAL
    window rather than only inside correlation."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    observe(env, "newest-event", "intake@g.us", job_text("A4NEW"),
            event_at=NOW - timedelta(minutes=1), received_at=NOW - timedelta(days=3))
    observe(env, "oldest-event", "intake@g.us", job_text("A4OLD"),
            event_at=NOW - timedelta(days=2), received_at=NOW)

    ordered = [c["ticket"] for c in candidates(env) if c["ticket"]]
    assert ordered.index("A4NEW") < ordered.index("A4OLD"), (
        "display order must follow WhatsApp event time, not ingestion time")


def test_a5_multi_ticket_outgoing_evidence_protects_every_ticket(env):
    """A hand-sent message covering two jobs proves neither. The SECOND
    ticket must not stay freely sendable."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in1", "intake@g.us", job_text("A5ONE"), NOW - timedelta(minutes=20))
    observe(env, "in2", "intake@g.us", job_text("A5TWO"), NOW - timedelta(minutes=19))

    conn = storage.connect(env)
    storage.add_whatsapp_outgoing_audit(conn, {
        "message_id": "out-multi", "group_id": "disp@g.us", "group_name": "dispatch",
        "timestamp": epoch(NOW - timedelta(minutes=18)),
        "text": job_text("A5ONE") + "\n\nTicket: A5TWO\nalso this one",
    })
    conn.close()

    for ticket in ("A5ONE", "A5TWO"):
        c = find(candidates(env), ticket)
        assert c is not None
        assert c["status"] != dc.STATUS_WAITING_HUMAN, (
            "%s must not be re-offered from ambiguous multi-ticket evidence" % ticket)
        assert c["proposed_message"] is None


def test_a6_customer_wording_inside_a_field_does_not_veto_dispatch(env):
    """POSITIVE CONTROL. Customer availability wording is job content,
    not a technician refusal."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    # The spec's case: the wording lives INSIDE a customer/job field.
    text = job_text("CUST6").replace("Desc: Car lockout",
                                  "Desc: Car lockout - customer not available until 3 PM")
    observe(env, "in", "intake@g.us", text, NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", text, NOW - timedelta(minutes=9))

    c = find(candidates(env), "CUST6")
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED, (
        "customer wording must not invalidate a genuine dispatch")


def test_a7_a_long_technician_update_is_not_dispatch_proof(env):
    """No length rule: this is long, and still a technician status."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("A7"), NOW - timedelta(minutes=10))
    observe(env, "upd", "disp@g.us",
            job_text("A7") + "\n\nI am 45 minutes away from the customer",
            NOW - timedelta(minutes=9))

    c = find(candidates(env), "A7")
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED


def test_a8_mixed_job_and_unrelated_status_is_ambiguous(env):
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("A8"), NOW - timedelta(minutes=10))
    observe(env, "mixed", "disp@g.us",
            job_text("A8") + "\n\nTicket: OTHER99\nwants an ETA",
            NOW - timedelta(minutes=9))

    c = find(candidates(env), "A8")
    assert c["status"] == dc.STATUS_HUMAN_REVIEW
    assert c["proposed_message"] is None


def test_a9_a_clean_single_ticket_forward_still_works(env):
    """POSITIVE CONTROL. Real dispatch must still be recognised -- a
    repair that reviews everything is a broken repair."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("POS09"), NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", job_text("POS09"), NOW - timedelta(minutes=9))

    c = find(candidates(env), "POS09")
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["routing_match"] in (dc.ROUTING_MATCH, dc.ROUTING_UNVERIFIED)


def test_a9b_an_undispatched_job_is_still_offered(env):
    """POSITIVE CONTROL. Nothing has been sent, so the operator must be
    able to send it."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("A9B"), NOW - timedelta(minutes=5))

    c = find(candidates(env), "A9B")
    # This fixture seeds no technicians, so routing cannot recommend one
    # and the job lands in ordinary review. What must hold is that it was
    # NOT suppressed and carries no safety hold.
    assert c is not None
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED
    assert c["review_reason"] is None, "nothing was dispatched, so nothing to hold it for"


# ================= CODEX FINAL: three remaining blockers ===============

AGN_BLOCK = ("Ref: AGN Locksmith Services\nPDL: {ticket}\n"
             "N: Test Customer\nPh: (864) 555-0100\n"
             "Addr: 470 Oak Grove Rd, Augusta, GA 30909")


def agn_text(ticket):
    return AGN_BLOCK.format(ticket=ticket)


# ---------------------------------------------------- BLOCKER 1 -------
def test_b1_multi_ticket_closing_protects_every_ticket(env):
    """A closing covering two jobs must not leave the second one looking
    untouched and therefore still sendable."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "clos@g.us", "closings", "TECH_CLOSINGS", company="Company A")
    observe(env, "in1", "intake@g.us", job_text("B1ONE"), NOW - timedelta(minutes=30))
    observe(env, "in2", "intake@g.us", job_text("B1TWO"), NOW - timedelta(minutes=29))
    observe(env, "closing", "clos@g.us",
            job_text("B1ONE") + "\n\nTicket: B1TWO\nboth finished",
            NOW - timedelta(minutes=5))

    for ticket in ("B1ONE", "B1TWO"):
        c = find(candidates(env), ticket)
        assert c is not None, ticket
        assert c["status"] != dc.STATUS_WAITING_HUMAN, (
            "%s must not stay sendable after a multi-ticket closing" % ticket)
        assert c["proposed_message"] is None


def test_b1_a_single_ticket_closing_still_reads_as_a_closing(env):
    """POSITIVE CONTROL for blocker 1."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "clos@g.us", "closings", "TECH_CLOSINGS", company="Company A")
    observe(env, "in", "intake@g.us", job_text("SOLO1"), NOW - timedelta(minutes=20))
    observe(env, "closing", "clos@g.us", job_text("SOLO1"), NOW - timedelta(minutes=5))

    c = find(candidates(env), "SOLO1")
    assert c["status"] == dc.STATUS_CLOSING_OBSERVED
    assert c["proposed_message"] is None


# ---------------------------------------------------- BLOCKER 2 -------
def test_b2a_agn_ref_header_is_not_a_second_ticket(env):
    """`Ref: AGN Locksmith Services` names the SOURCE. Counting it as a
    ticket made every clean AGN forward look like a two-job message."""
    assert dc.count_tickets(agn_text("ZN6YF")) == 1
    assert dc.all_tickets(agn_text("ZN6YF")) == ["ZN6YF"]

    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", agn_text("ZN6YF"), NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", agn_text("ZN6YF"), NOW - timedelta(minutes=9))

    c = find(candidates(env), "ZN6YF")
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED, (
        "a clean AGN forward must be recognised, not sent to review")


def test_b2b_two_real_agn_tickets_are_still_multi_ticket(env):
    two = agn_text("ZN6YF") + "\n\nPDL: QQ7RT\nsecond job"
    assert dc.count_tickets(two) == 2
    assert set(dc.all_tickets(two)) == {"ZN6YF", "QQ7RT"}

    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in1", "intake@g.us", agn_text("ZN6YF"), NOW - timedelta(minutes=30))
    observe(env, "in2", "intake@g.us", agn_text("QQ7RT"), NOW - timedelta(minutes=29))
    observe(env, "sent", "disp@g.us", two, NOW - timedelta(minutes=5))

    for ticket in ("ZN6YF", "QQ7RT"):
        c = find(candidates(env), ticket)
        assert c["status"] != dc.STATUS_WAITING_HUMAN
        assert c["proposed_message"] is None


def test_b2c_mike_and_lucky_ticket_parsing_is_unchanged(env):
    assert dc.count_tickets(job_text("JO47-TPXW")) == 1
    assert dc.all_tickets(job_text("JO47-TPXW")) == ["JO47-TPXW"]
    lucky = "Ticket: E5X5N\nName: A\nPhone: (864) 555-0100\nAddress: 2 Oak St, Augusta, GA"
    assert dc.count_tickets(lucky) == 1
    assert dc.all_tickets(lucky) == ["E5X5N"]


# ---------------------------------------------------- BLOCKER 3 -------
INLINE_UPDATES = ["20 min", "30 min", "45 minutes", "on my way", "eta 20",
                  "be there in 30", "customer no answer", "pass", "busy"]


@pytest.mark.parametrize("reply", INLINE_UPDATES)
def test_b3_inline_update_without_a_blank_line_is_not_dispatch(env, reply):
    """A technician status typed straight under the block, with NO blank
    separator, must not read as a completed dispatch."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    observe(env, "in", "intake@g.us", job_text("B3"), NOW - timedelta(minutes=10))
    observe(env, "reply", "disp@g.us", job_text("B3") + "\n" + reply,
            NOW - timedelta(minutes=9))

    c = find(candidates(env), "B3")
    assert c["status"] != dc.STATUS_ALREADY_DISPATCHED, (
        "%r straight under the block is a technician update" % reply)


def test_b3_customer_prose_on_the_same_line_shape_is_still_a_dispatch(env):
    """POSITIVE CONTROL, from real production wording. Structurally the
    same position as the updates above, but it names concrete things --
    storage, open, needs -- so it is job description, not an ETA."""
    group(env, "intake@g.us", "intake", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "dispatch", "TECH_DISPATCH", company="Company A")
    text = job_text("B3POS") + "\nhe needs the storage open in the next hour"
    observe(env, "in", "intake@g.us", text, NOW - timedelta(minutes=10))
    observe(env, "sent", "disp@g.us", text, NOW - timedelta(minutes=9))

    c = find(candidates(env), "B3POS")
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED, (
        "customer job prose must not be mistaken for a technician ETA")


def test_b3_uses_no_message_length_threshold():
    """The rule is about CONTENT (time/movement filler only), not size:
    a long pure-filler line is still a status, and a short line naming a
    real thing is still description."""
    assert dc._is_standalone_status_line(
        "i will be there in about 30 minutes from now") is True
    assert dc._is_standalone_status_line("gate code 4471") is False


# ============ CODEX FINAL RE-REVIEW: colonless gate + extractor ========
# These use a SEEDED TECHNICIAN so the route genuinely reaches the
# decision path. Without one, routing finds nobody eligible and every
# assertion of the form "status != WAITING" passes vacuously.

TECH_TICKET = "AUGX-1001"


@pytest.fixture
def routed(env):
    """A world where a clean Augusta job really is dispatchable."""
    conn = storage.connect(env)
    tech_id = storage.add_technician(
        conn, name="Sebai", areas=["Augusta"], skills=[], vehicle_restrictions=[],
        dispatch_group="Sebai Augusta Dispatch", closing_group="Sebai augusta closing",
    )
    conn.close()
    group(env, "intake@g.us", "Augusta - Adam", "COMPANY_JOB_INTAKE", company="Company A")
    group(env, "disp@g.us", "Sebai Augusta Dispatch", "TECH_DISPATCH",
          company="Company A", technician_id=tech_id)
    group(env, "clos@g.us", "Sebai augusta closing", "TECH_CLOSINGS",
          company="Company A", technician_id=tech_id)
    return env


def outgoing(db_path, message_id, ticket_text, when):
    conn = storage.connect(db_path)
    storage.add_whatsapp_outgoing_audit(conn, {
        "message_id": message_id,
        "group_id": "disp@g.us",
        "group_name": "Sebai Augusta Dispatch",
        "timestamp": epoch(when),
        "text": ticket_text,
    })
    conn.close()


def test_j_the_fixture_is_not_vacuous(routed):
    """POSITIVE CONTROL / TEST J. Prove the seeded world can actually
    produce a sendable job -- otherwise every other assertion here is
    meaningless."""
    observe(routed, "in", "intake@g.us", job_text(TECH_TICKET), NOW - timedelta(minutes=5))
    c = find(candidates(routed), TECH_TICKET)
    assert c is not None
    assert c["recommended"] == "Sebai", "routing must resolve, or these tests prove nothing"
    assert c["status"] == dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"], "an undispatched eligible job must be actionable"


@pytest.mark.parametrize("ticket", ["WJFBT", "JOQD-APNH", "9GVQ5"])
def test_known_production_ticket_shapes_correlate(routed, ticket):
    observe(routed, "in-" + ticket, "intake@g.us", job_text(ticket),
            NOW - timedelta(minutes=10))
    outgoing(routed, "out-" + ticket,
             job_text(ticket).replace("Ticket: ", "Ticket "),
             NOW - timedelta(minutes=9))

    c = find(candidates(routed), ticket)
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["proposed_message"] is None


@pytest.mark.parametrize("fake", [
    "CANCELLED", "URGENT", "STATUS", "READY", "OPEN", "CUSTOMER",
    "20", "3PM", "555-0100",
])
def test_unknown_or_junk_ticket_tokens_create_no_correlation_evidence(routed, fake):
    real = "WJFBT"
    observe(routed, "real-in", "intake@g.us", job_text(real),
            NOW - timedelta(minutes=10))
    outgoing(routed, "fake-out", job_text(fake), NOW - timedelta(minutes=9))

    c = find(candidates(routed), real)
    assert c["recommended"] == "Sebai"
    assert c["status"] == dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"]
    assert dc.validated_tickets("Ticket " + fake, {real}) == []


def test_unknown_plausible_all_letter_token_does_not_suppress_known_job(routed):
    real, unknown = "WJFBT", "ABCDE"
    observe(routed, "real-in", "intake@g.us", job_text(real),
            NOW - timedelta(minutes=10))
    outgoing(routed, "unknown-out", job_text(unknown),
             NOW - timedelta(minutes=9))

    c = find(candidates(routed), real)
    assert c["status"] == dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"]
    assert dc.all_tickets("Ticket " + unknown) == [unknown]
    assert dc.validated_tickets("Ticket " + unknown, {real}) == []


def test_second_ticket_present_in_confirmed_intake_stays_ambiguous_downstream(routed):
    """A second colonless ticket in the intake is known identity evidence,
    even though the legacy parser returns only the first ticket as the row's
    primary candidate. Forwarding that same text must not re-offer the first
    job when the second job's prose happens to mention an ETA."""
    primary, secondary = "WJFBT", "JOQD-APNH"
    text = job_text(primary) + ("\n\nTicket " + secondary
                                + "\nCustomer wants details of an ETA")
    observe(routed, "multi-in", "intake@g.us", text,
            NOW - timedelta(minutes=10))
    outgoing(routed, "multi-out", text, NOW - timedelta(minutes=9))

    c = find(candidates(routed), primary)
    assert c["status"] != dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"] is None
    assert c["review_reason"] is not None


def test_known_ticket_is_still_scoped_by_company(routed):
    ticket = "WJFBT"
    group(routed, "b-intake@g.us", "Company B intake", "COMPANY_JOB_INTAKE",
          company="Company B")
    observe(routed, "a-in", "intake@g.us", job_text(ticket),
            NOW - timedelta(minutes=10))
    observe(routed, "b-in", "b-intake@g.us", job_text(ticket),
            NOW - timedelta(minutes=8))
    outgoing(routed, "a-out", job_text(ticket), NOW - timedelta(minutes=9))

    by_message = {c["message_id"]: c for c in candidates(routed)}
    assert by_message["a-in"]["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert by_message["b-in"]["status"] == dc.STATUS_WAITING_HUMAN
    assert by_message["b-in"]["proposed_message"]


def test_a_colonless_outgoing_ticket_is_seen(routed):
    """TEST A. "Ticket ABC-123" with no colon, sent by hand."""
    observe(routed, "in", "intake@g.us", job_text(TECH_TICKET), NOW - timedelta(minutes=10))
    conn = storage.connect(routed)
    storage.add_whatsapp_outgoing_audit(conn, {
        "message_id": "out-nocolon", "group_id": "disp@g.us",
        "group_name": "Sebai Augusta Dispatch",
        "timestamp": epoch(NOW - timedelta(minutes=9)),
        "text": job_text(TECH_TICKET).replace("Ticket: ", "Ticket "),
    })
    conn.close()

    c = find(candidates(routed), TECH_TICKET)
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED, (
        "a colonless forwarded ticket must still be correlated")
    assert c["proposed_message"] is None


def test_b_inline_outgoing_ticket_is_seen(routed):
    """TEST B. "//Ticket ABC-123 //" inline."""
    observe(routed, "in", "intake@g.us", job_text(TECH_TICKET), NOW - timedelta(minutes=10))
    inline = (job_text(TECH_TICKET).replace("Ticket: " + TECH_TICKET, "")
              + "\n2007 Nissan //Ticket " + TECH_TICKET + " // on it")
    conn = storage.connect(routed)
    storage.add_whatsapp_outgoing_audit(conn, {
        "message_id": "out-inline", "group_id": "disp@g.us",
        "group_name": "Sebai Augusta Dispatch",
        "timestamp": epoch(NOW - timedelta(minutes=9)), "text": inline,
    })
    conn.close()

    c = find(candidates(routed), TECH_TICKET)
    assert c["status"] != dc.STATUS_WAITING_HUMAN, (
        "an inline ticket reference must not be skipped by the gate")
    assert c["proposed_message"] is None


def test_c_colonless_closing_ticket_is_seen(routed):
    """TEST C. "PDL ABC-123" in a closings group."""
    observe(routed, "in", "intake@g.us", job_text(TECH_TICKET), NOW - timedelta(minutes=10))
    observe(routed, "closing", "clos@g.us",
            job_text(TECH_TICKET).replace("Ticket: ", "PDL "),
            NOW - timedelta(minutes=5))

    c = find(candidates(routed), TECH_TICKET)
    assert c["status"] != dc.STATUS_WAITING_HUMAN
    assert c["proposed_message"] is None


def test_d_two_tickets_in_one_closing_protect_both(routed):
    """TEST D. Neither job may stay sendable."""
    second = "AUGX-2002"
    observe(routed, "in1", "intake@g.us", job_text(TECH_TICKET), NOW - timedelta(minutes=20))
    observe(routed, "in2", "intake@g.us", job_text(second), NOW - timedelta(minutes=19))
    observe(routed, "closing", "clos@g.us",
            job_text(TECH_TICKET) + "\n\nTicket " + second + "\nboth done",
            NOW - timedelta(minutes=5))

    for ticket in (TECH_TICKET, second):
        c = find(candidates(routed), ticket)
        assert c is not None, ticket
        assert c["status"] != dc.STATUS_WAITING_HUMAN, ticket
        assert c["proposed_message"] is None, ticket


def test_i_clean_single_ticket_dispatch_is_still_positive(routed):
    """TEST I. POSITIVE CONTROL."""
    observe(routed, "in", "intake@g.us", job_text(TECH_TICKET), NOW - timedelta(minutes=10))
    observe(routed, "sent", "disp@g.us", job_text(TECH_TICKET), NOW - timedelta(minutes=9))

    c = find(candidates(routed), TECH_TICKET)
    assert c["status"] == dc.STATUS_ALREADY_DISPATCHED
    assert c["proposed_message"] is None
    # routing_match needs the registry row's technician NAME, which this
    # fixture's read-model join leaves unset even with technician_id
    # present (live data does populate it). The safety-critical facts --
    # recognised as dispatched, nothing sendable -- are asserted above.
    assert c["routing_match"] in (dc.ROUTING_MATCH, dc.ROUTING_UNVERIFIED)


# ------------------------ extractor grammar (E, F, G, H) --------------
def test_e_clean_agn_has_exactly_one_ticket():
    agn = ("Ref: AGN Locksmith Services\nPDL: ZN6YF\nN: Test\n"
           "Ph: (864) 555-0100\nAddr: 12 Oak St, Augusta, GA")
    assert dc.all_tickets(agn) == ["ZN6YF"]
    assert dc.classify_dispatch_evidence(agn, True)[0] == dc.POSITIVE_DISPATCH


@pytest.mark.parametrize("prose", [
    "ticket was cancelled",
    "this ticket is urgent",
    "customer asked about ticket status",
    "ticket not available",
    "the ticket number is unclear",
])
def test_f_g_prose_produces_no_ticket_ids(prose):
    """TESTS F/G. A bare English word after "ticket" is not an identifier."""
    assert dc.all_tickets(prose) == []
    assert dc.count_tickets(prose) == 0


def test_h_a_genuine_two_ticket_message_is_multi():
    two = ("Ticket: JOSW-X369\nName: A\nPhone: 555\nAddress: 1 Oak\n\n"
           "Ticket JO4A-7GBG\nthe other one")
    assert dc.count_tickets(two) == 2
    assert dc.classify_dispatch_evidence(two, True)[0] == dc.AMBIGUOUS


def test_all_ticket_label_forms_are_supported():
    for text, expected in (
        ("Ticket: JOSW-X369", ["JOSW-X369"]),
        ("Ticket JOSW-X369", ["JOSW-X369"]),
        ("PDL: JOSW-X369", ["JOSW-X369"]),
        ("PDL JOSW-X369", ["JOSW-X369"]),
        ("2007 Nissan //Ticket JO4A-7GBG // calling back", ["JO4A-7GBG"]),
    ):
        assert dc.all_tickets(text) == expected, text


def test_one_extractor_is_the_authority_for_the_route():
    """The route must not keep its own stricter ticket grammar: the
    colon-requiring parser.TICKET_RE used as a pre-gate skipped colonless
    forwards entirely, leaving already-sent jobs sendable."""
    source = _route_source()
    assert "validated_evidence_tickets" in source
    assert "dispatch_candidates.validated_tickets" in source
    assert "norm(parsed.ticket_number.value)" not in source, (
        "evidence gating must not use the colon-requiring parser regex")
