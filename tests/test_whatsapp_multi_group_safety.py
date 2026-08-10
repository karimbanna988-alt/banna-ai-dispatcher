"""Multi-group shadow mode safety guards (Phase 5 of the launch brief).
These prove, against the real storage layer (never mocked), that
expanding observation to many groups cannot cross-contaminate jobs,
technicians, or companies, and that no observation -- regardless of its
registry role -- can mutate committed workflow state on its own."""

import pytest

from dispatcher import storage
import whatsapp_bridge_adapter as adapter


@pytest.fixture()
def conn(tmp_path):
    return storage.connect(tmp_path / "safety_test.db")


def _registry(conn, group_id, role, **kw):
    storage.upsert_group_registry_entry(conn, {
        "group_id": group_id, "exact_name": kw.pop("exact_name", group_id), "role": role, **kw,
    })


def _observe(conn, message_id, group_id, group_name, text, extracted_ticket=None, quoted_message_id=None):
    storage.add_whatsapp_observation(conn, {
        "message_id": message_id, "group_id": group_id, "group_name": group_name,
        "sender": "1555", "direction": "incoming", "timestamp": "1700000000",
        "text": text, "extracted_ticket": extracted_ticket, "quoted_message_id": quoted_message_id,
    })


# ---------------------------------------------------------- TICKET ISOLATION
def test_two_different_companies_reusing_the_same_ticket_number_never_auto_correlate(conn):
    """A ticket number matching across groups must never itself create a
    correlation -- only an explicit quoted_message_id chain (already
    attached to a real job by a human) does that."""
    _registry(conn, "gmi@g.us", "COMPANY_JOB_INTAKE", company="GMI")
    _registry(conn, "agn@g.us", "COMPANY_JOB_INTAKE", company="AGN")
    _observe(conn, "m1", "gmi@g.us", "GMI Adams job receiving", "PDL: SAME123 -- new job", extracted_ticket="SAME123")
    _observe(conn, "m2", "agn@g.us", "AGN Delaware CLOSINGS", "PDL: SAME123 -- closed $80", extracted_ticket="SAME123")
    rows = {r["message_id"]: r for r in storage.list_whatsapp_pending_observations(conn)}
    assert rows["m1"]["correlated_job_id"] is None
    assert rows["m2"]["correlated_job_id"] is None
    assert rows["m1"]["job_id"] is None
    assert rows["m2"]["job_id"] is None


def test_same_k_reply_in_two_different_technician_dispatch_groups_never_cross_matches(conn):
    _registry(conn, "toto@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Toto", service_area="Greenville")
    _registry(conn, "joe@g.us", "TECH_DISPATCH", technician_name_unconfirmed="Joe", service_area="Greenville")
    _observe(conn, "m1", "toto@g.us", "Toto Greenville Dispatch", "k")
    _observe(conn, "m2", "joe@g.us", "Joe Greenville Dispatch/Saturday", "k")
    rows = {r["message_id"]: r for r in storage.list_whatsapp_pending_observations(conn)}
    # Two independent rows, two independent (unset) correlations -- never merged
    assert len(rows) == 2
    assert rows["m1"]["correlated_job_id"] is None
    assert rows["m2"]["correlated_job_id"] is None
    assert rows["m1"]["group_technician_name"] == "Toto"
    assert rows["m2"]["group_technician_name"] == "Joe"


def test_quoted_message_correlation_only_follows_an_explicit_attach_never_a_ticket_guess(conn):
    """The ONE real correlation mechanism (quoted_message_id -> an
    observation already carrying a job_id) still requires a prior human
    attach -- ticket similarity plays no part in it."""
    _observe(conn, "m1", "g@g.us", "Some Dispatch", "PDL: XYZ -- job")
    storage.resolve_whatsapp_observation(conn, "m1", "attached", job_id=42)
    _observe(conn, "m2", "g@g.us", "Some Dispatch", "k on the way", quoted_message_id="m1")
    row = storage.get_whatsapp_observation(conn, "m2")
    assert row["correlated_job_id"] == 42  # correlation via the real reply chain, not the ticket text
    assert row["job_id"] is None  # still just a hint -- never auto-attached


# ------------------------------------------------- ROLE-BASED NON-MUTATION
@pytest.mark.parametrize("role", ["COMPANY_JOB_INTAKE", "COMPANY_CLOSINGS", "TECH_DISPATCH", "TECH_CLOSINGS", "UNMAPPED_WORK", "IGNORE"])
def test_no_registry_role_ever_auto_sets_job_id_or_status_on_ingest(conn, role):
    """Regardless of role -- including a confirmed TECH_CLOSINGS or
    COMPANY_CLOSINGS group -- storing an observation never itself creates,
    attaches to, or closes a job. job_id and status only ever change via
    the explicit human-driven resolve_whatsapp_observation call."""
    _registry(conn, "g@g.us", role, confirmed_by_owner=True, is_active=True)
    _observe(conn, f"m-{role}", "g@g.us", "Some Group", "PDL: T1 -- job done, paid $100")
    row = storage.get_whatsapp_observation(conn, f"m-{role}")
    assert row["job_id"] is None
    assert row["status"] == "pending"


def test_unmapped_work_observation_cannot_be_distinguished_from_confirmed_ones_by_mutation_capability(conn):
    """UNMAPPED_WORK and a fully-confirmed TECH_DISPATCH group go through
    the exact same storage function with the exact same non-mutating
    guarantees -- classification only changes what's DISPLAYED, never
    what CAN happen automatically."""
    _registry(conn, "unmapped@g.us", "UNMAPPED_WORK", confirmed_by_owner=False, is_active=True)
    _registry(conn, "confirmed@g.us", "TECH_DISPATCH", confirmed_by_owner=True, is_active=True, technician_id=None)
    _observe(conn, "m1", "unmapped@g.us", "Unknown Group", "urgent job now")
    _observe(conn, "m2", "confirmed@g.us", "Sebai Augusta Dispatch", "urgent job now")
    for mid in ("m1", "m2"):
        row = storage.get_whatsapp_observation(conn, mid)
        assert row["job_id"] is None
        assert row["status"] == "pending"


# --------------------------------------------------------- OUTGOING ISOLATION
def test_outgoing_audit_from_any_group_role_never_enters_the_actionable_queue(conn):
    for role in ("COMPANY_JOB_INTAKE", "TECH_DISPATCH", "UNMAPPED_WORK"):
        gid = f"{role.lower()}@g.us"
        _registry(conn, gid, role, confirmed_by_owner=True, is_active=True)
        storage.add_whatsapp_outgoing_audit(conn, {
            "message_id": f"out-{role}", "group_id": gid, "group_name": role, "text": "sent by dispatcher",
        })
    assert storage.list_whatsapp_pending_observations(conn, include_resolved=True) == []
    assert len(storage.list_whatsapp_outgoing_audit(conn)) == 3


# ----------------------------------------------- NO WRITE PATH TO jobs/events
def test_no_function_in_storage_whatsapp_module_updates_jobs_or_events_tables():
    """Static guard over the whatsapp-specific functions: none of them may
    contain an UPDATE/INSERT against the jobs or events tables -- only
    whatsapp_pending_observations / whatsapp_group_registry /
    whatsapp_outgoing_audit."""
    import inspect

    for fn in (
        storage.add_whatsapp_observation,
        storage.resolve_whatsapp_observation,
        storage.upsert_group_registry_entry,
        storage.add_whatsapp_outgoing_audit,
    ):
        src = inspect.getsource(fn)
        assert "INTO jobs" not in src
        assert "UPDATE jobs" not in src
        assert "INTO events" not in src
        assert "UPDATE events" not in src


def test_adapter_still_never_writes_jobs_or_events_with_outgoing_audit_added(clean_adapter_state_module_level=None):
    import inspect

    for fn in (adapter.add_observation, adapter.add_outgoing_audit, adapter.validate_observation, adapter.validate_outgoing_audit):
        src = inspect.getsource(fn)
        assert "DispatcherService" not in src
        assert "INTO jobs" not in src
        assert "INTO events" not in src
