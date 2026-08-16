"""Per-message company identity.

The property under test throughout: a technician destination is not a
company identity, and no signal may silently overwrite a conflicting one.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatcher import company_resolver as cr


SEBAI_DISPATCH = "120363424227833077@g.us"      # owner-confirmed single-company
SEBAI_CLOSING = "120363426584048614@g.us"
ASHRAF_DISPATCH = "120363428199635701@g.us"
ADAM_INTAKE = "120363424464824773@g.us"
MIXED_GROUP = "120363999999999999@g.us"          # NOT allowlisted

AGN_TEXT = "Ref: AGN Locksmith Services\nPDL: ZN6YF\nPh: 555\nAddr: 1 Oak St"
LUCKY_TEXT = "Co: LUCKY LOCKSMITH LLC\nPDL: WJFBT\nPh: 555\nAddr: 2 Elm St"
MIKE_TEXT = "Joy Locksmith\nTicket: JOSW-X369\nPh: 555\nAddr: 3 Pine St"


def ctx(**kw):
    return cr.ResolverContext(**kw)


def obs(text="", group_id=MIXED_GROUP, **kw):
    row = {"message_id": kw.pop("message_id", "m1"), "group_id": group_id, "text": text}
    row.update(kw)
    return row


def registry(group_id, role, company=None, confirmed=1):
    return {"group_id": group_id, "role": role, "company": company,
            "confirmed_by_owner": confirmed}


# ---------------------------------------------------------------- 1-4 --
def test_exact_agn_marker_resolves_agn():
    r = cr.resolve_company(obs(AGN_TEXT), ctx())
    assert (r["company"], r["confidence"]) == (cr.AGN, cr.EXACT)


def test_exact_lucky_marker_resolves_lucky():
    r = cr.resolve_company(obs(LUCKY_TEXT), ctx())
    assert (r["company"], r["confidence"]) == (cr.LUCKY, cr.EXACT)


@pytest.mark.parametrize("line", ["Joy Locksmith", "  Joy Locksmith  ", "joy locksmith"])
def test_exact_joy_locksmith_resolves_mike(line):
    r = cr.resolve_company(obs(line + "\nTicket: JOSW-X369"), ctx())
    assert (r["company"], r["confidence"]) == (cr.MIKE_JOBS, cr.EXACT)


def test_exact_locksmith_pros_resolves_mike():
    r = cr.resolve_company(obs("Locksmith Pros\nTicket: JOSW-X369"), ctx())
    assert r["company"] == cr.MIKE_JOBS


# ------------------------------------------- rejected as company proof --
@pytest.mark.parametrize("text", [
    "Power By Ucallz App\nTicket: JOSW-X369\nPh: 555",   # format, not identity
    "Ticket: JOSW-X369\nPh: 555\nAddr: 1 Oak St",        # bare Main Leads shape
    "PDL: ZN6YF\nPh: 555",                               # bare AGN/Lucky shape
    "Locksmith\nTicket: JOSW-X369",                      # generic word
    "Sebai\nTicket: JOSW-X369",                          # technician identity
    "Augusta\nTicket: JOSW-X369",                        # area
    "Ticket: JOSW-X369",                                 # a pending ticket alone
    "please handle this one",                            # arbitrary shape
])
def test_these_are_never_company_proof(text):
    r = cr.resolve_company(obs(text), ctx())
    assert r["company"] is None
    assert r["confidence"] == cr.UNRESOLVED


@pytest.mark.parametrize("line", [
    "Birmingham Locksmith pros",     # larger phrase, in the live corpus
    "Joy Locksmith Pros",            # larger phrase, in the live corpus
    "Joy Locksmiths",
])
def test_marker_variants_are_not_promoted(line):
    """A near miss must go to review, not become Mike."""
    r = cr.resolve_company(obs(line + "\nTicket: JOSW-X369"), ctx())
    assert r["company"] is None, line


# ------------------------------------------------------------------ 5 --
def test_owner_confirmed_intake_provenance():
    c = ctx(registry_rows=[registry(ADAM_INTAKE, "COMPANY_JOB_INTAKE", cr.MIKE_JOBS)])
    r = cr.resolve_company(obs("Ticket: JOSW-X369", group_id=ADAM_INTAKE), c)
    assert (r["company"], r["confidence"]) == (cr.MIKE_JOBS, cr.EXACT)


# ---------------------------------------------------------------- 6-7 --
def test_quoted_pending_parent_inheritance():
    parent = obs(AGN_TEXT, message_id="p1")
    c = ctx(observations=[parent])
    r = cr.resolve_company(obs("on my way", message_id="c1", quoted_message_id="p1"), c)
    assert (r["company"], r["confidence"]) == (cr.AGN, cr.LINKED)


def test_quoted_outgoing_audit_parent_inheritance():
    c = ctx(outgoing_audit=[{"message_id": "a1", "group_id": MIXED_GROUP, "text": LUCKY_TEXT}])
    r = cr.resolve_company(obs("done", message_id="c1", quoted_message_id="a1"), c)
    assert (r["company"], r["confidence"]) == (cr.LUCKY, cr.LINKED)


def test_missing_parent_is_absent_not_fabricated():
    r = cr.resolve_company(obs("done", quoted_message_id="never-captured"), ctx())
    assert r["company"] is None
    assert r["confidence"] == cr.UNRESOLVED


def test_missing_parent_does_not_erase_a_real_marker():
    """An uncaptured parent is silence, so the message's own exact marker
    still stands."""
    r = cr.resolve_company(obs(AGN_TEXT, quoted_message_id="never-captured"), ctx())
    assert r["company"] == cr.AGN


# --------------------------------------------------------------- 8-12 --
def job(job_id, source, ticket):
    return {"id": job_id, "source": source, "ticket_number": ticket}


def test_valid_job_id_inheritance():
    c = ctx(jobs=[job(7, "Main Leads", "JOSW-X369")])
    r = cr.resolve_company(obs("update", job_id=7, extracted_ticket="JOSW-X369"), c)
    assert (r["company"], r["confidence"]) == (cr.MIKE_JOBS, cr.LINKED)


def test_valid_correlated_job_id_inheritance():
    c = ctx(jobs=[job(8, "AGN", "ZN6YF")])
    r = cr.resolve_company(obs("update", correlated_job_id=8, extracted_ticket="ZN6YF"), c)
    assert r["company"] == cr.AGN


def test_nonexistent_linked_job_is_unresolved():
    r = cr.resolve_company(obs("update", job_id=999), ctx())
    assert r["company"] is None and r["confidence"] == cr.UNRESOLVED


def test_mismatched_linked_ticket_is_unresolved():
    c = ctx(jobs=[job(7, "Main Leads", "JOSW-X369")])
    r = cr.resolve_company(obs("update", job_id=7, extracted_ticket="OTHER-9999"), c)
    assert r["company"] is None


def test_conflicting_linked_jobs_are_unresolved():
    c = ctx(jobs=[job(7, "Main Leads", "JOSW-X369"), job(8, "AGN", "JOSW-X369")])
    r = cr.resolve_company(
        obs("update", job_id=7, correlated_job_id=8, extracted_ticket="JOSW-X369"), c)
    assert r["company"] is None
    assert "CONFLICT" in r["basis"]


def test_broken_link_beats_an_exact_marker():
    """Fail closed: a link pointing at nothing is corruption, and a
    corrupt row must not be resolved on the strength of its text."""
    r = cr.resolve_company(obs(AGN_TEXT, job_id=999), ctx())
    assert r["company"] is None


# ------------------------------------------------------------- 13-14 --
def test_parent_cycle_is_unresolved():
    a = obs("a", message_id="a", quoted_message_id="b")
    b = obs("b", message_id="b", quoted_message_id="a")
    r = cr.resolve_company(a, ctx(observations=[a, b]))
    assert r["company"] is None
    assert "cycle" in r["basis"]


def test_self_quote_is_unresolved():
    a = obs("a", message_id="a", quoted_message_id="a")
    r = cr.resolve_company(a, ctx(observations=[a]))
    assert r["company"] is None


def test_parent_conflict_propagates():
    parent = obs(AGN_TEXT + "\nJoy Locksmith", message_id="p1")
    c = ctx(observations=[parent])
    r = cr.resolve_company(obs("ok", message_id="c1", quoted_message_id="p1"), c)
    assert r["company"] is None
    assert "unresolved" in r["basis"] or "CONFLICT" in r["basis"]


# ------------------------------------------------------------- 15-16 --
@pytest.mark.parametrize("gid", [SEBAI_DISPATCH, SEBAI_CLOSING, ASHRAF_DISPATCH])
def test_allowlisted_single_company_group(gid):
    r = cr.resolve_company(obs("Ticket: JOSW-X369\nPh: 555", group_id=gid), ctx())
    assert (r["company"], r["confidence"]) == (cr.MIKE_JOBS, cr.EXACT)


def test_static_company_on_non_allowlisted_tech_group_is_not_trusted():
    """The registry currently carries static company values on technician
    groups. Outside the owner-confirmed allowlist those must NOT act as
    single-company evidence -- that is exactly the assumption that lets a
    mixed group suppress another company's job."""
    c = ctx(registry_rows=[registry(MIXED_GROUP, "TECH_DISPATCH", cr.MIKE_JOBS)])
    r = cr.resolve_company(obs("Ticket: JOSW-X369\nPh: 555", group_id=MIXED_GROUP), c)
    assert r["company"] is None
    assert r["confidence"] == cr.UNRESOLVED


# ------------------------------------------------------------- 18-19 --
def test_mixed_destination_group_resolves_per_message():
    c = ctx()
    assert cr.resolve_company(obs(AGN_TEXT, group_id=MIXED_GROUP), c)["company"] == cr.AGN
    assert cr.resolve_company(obs(LUCKY_TEXT, group_id=MIXED_GROUP), c)["company"] == cr.LUCKY
    assert cr.resolve_company(obs(MIKE_TEXT, group_id=MIXED_GROUP), c)["company"] == cr.MIKE_JOBS
    assert cr.resolve_company(obs("Ticket: X\nPh: 5", group_id=MIXED_GROUP), c)["company"] is None


@pytest.mark.parametrize("text,other", [(AGN_TEXT, cr.AGN), (LUCKY_TEXT, cr.LUCKY)])
def test_competitor_marker_inside_mike_group_is_a_conflict(text, other):
    """13 real messages in the live corpus do exactly this. Neither
    forcing Mike (the group) nor forcing the competitor (the marker) is
    acceptable -- the operator decides."""
    r = cr.resolve_company(obs(text, group_id=SEBAI_DISPATCH), ctx())
    assert r["company"] is None
    assert r["confidence"] == cr.UNRESOLVED
    assert "CONFLICT" in r["basis"]
    assert other in r["basis"] and cr.MIKE_JOBS in r["basis"]


def test_agreeing_signals_are_not_a_conflict():
    """Mike marker inside a Mike-confirmed group stays resolved."""
    r = cr.resolve_company(obs(MIKE_TEXT, group_id=SEBAI_DISPATCH), ctx())
    assert (r["company"], r["confidence"]) == (cr.MIKE_JOBS, cr.EXACT)


# ---------------------------------------------------------------- 17 --
def test_job_source_is_only_a_company_through_a_persisted_job():
    assert cr.company_for_job_source("Main Leads") == cr.MIKE_JOBS
    assert cr.company_for_job_source("AGN") == cr.AGN
    assert cr.company_for_job_source("Lucky") == cr.LUCKY
    assert cr.company_for_job_source("") is None
    assert cr.company_for_job_source(None) is None
    assert cr.company_for_job_source("Locksmith") is None
    # ...and raw text in that format never reaches it.
    assert cr.resolve_company(obs("Power By Ucallz App\nTicket: X"), ctx())["company"] is None


def test_resolver_is_pure():
    """No SQL, no IO, no clock: the context is the only input."""
    import inspect
    src = inspect.getsource(cr)
    for forbidden in ("import sqlite3", "execute(", "urllib", "requests",
                      "open(", "datetime.now", "time.time"):
        assert forbidden not in src, forbidden


# ============ RE-REVIEW BLOCKER 2: marker anchoring ===================
@pytest.mark.parametrize("line", [
    "NotRef: AGN Locksmith Services",
    "SomeRef: AGN Locksmith Services",
    "Ref: AGN Locksmith Services LLC",
    "Ref: AGN Locksmith Services extra text",
    "xRef: AGN Locksmith Services",
])
def test_agn_near_misses_do_not_resolve(line):
    """A prefix or suffix nobody confirmed is not the marker."""
    r = cr.resolve_company(obs(line + "\nPDL: ZN6YF"), ctx())
    assert r["company"] is None, line


@pytest.mark.parametrize("line", [
    "Co: LUCKY LOCKSMITHERS",
    "Co: LUCKY LOCKSMITH EXTRA TEXT",
    "NotCo: LUCKY LOCKSMITH",
    "Co: LUCKY LOCKSMITH LLC INC",
])
def test_lucky_near_misses_do_not_resolve(line):
    r = cr.resolve_company(obs(line + "\nPDL: WJFBT"), ctx())
    assert r["company"] is None, line


@pytest.mark.parametrize("line,expected", [
    ("Ref: AGN Locksmith Services", cr.AGN),
    ("ref: agn locksmith services", cr.AGN),
    ("   Ref: AGN Locksmith Services   ", cr.AGN),
    ("Co: LUCKY LOCKSMITH", cr.LUCKY),
    ("Co: LUCKY LOCKSMITH LLC", cr.LUCKY),          # owner-confirmed variant
    ("co: lucky locksmith llc", cr.LUCKY),
])
def test_confirmed_marker_lines_still_resolve(line, expected):
    r = cr.resolve_company(obs(line + "\nPDL: ZN6YF"), ctx())
    assert r["company"] == expected, line


def test_marker_must_own_its_line():
    """Embedded mid-line is not a confirmed form."""
    r = cr.resolve_company(obs("please see Ref: AGN Locksmith Services below"), ctx())
    assert r["company"] is None


# ====== RE-REVIEW BLOCKER 3: corrupt parent poisons inheritance ======
MIKE_MARKER = "Joy Locksmith\nTicket: JOSW-X369"
LUCKY_MARKER = "Co: LUCKY LOCKSMITH LLC\nPDL: WJFBT"


def test_A_parent_with_nonexistent_job_poisons_a_mike_child():
    parent = obs("update", message_id="p1", job_id=999)
    c = ctx(observations=[parent])
    r = cr.resolve_company(
        obs(MIKE_MARKER, message_id="c1", quoted_message_id="p1"), c)
    assert r["company"] is None, "a corrupt parent must not be out-voted by the child"
    assert r["confidence"] == cr.UNRESOLVED
    assert "does not exist" in r["basis"]


def test_B_parent_with_ticket_mismatch_poisons_a_lucky_child():
    parent = obs("update", message_id="p1", job_id=7, extracted_ticket="OTHER-9999")
    c = ctx(observations=[parent],
            jobs=[{"id": 7, "source": "Main Leads", "ticket_number": "JOSW-X369"}])
    r = cr.resolve_company(
        obs(LUCKY_MARKER, message_id="c1", quoted_message_id="p1"), c)
    assert r["company"] is None
    assert "mismatch" in r["basis"]


def test_C_parent_with_conflicting_job_links_poisons_the_child():
    parent = obs("update", message_id="p1", job_id=7, correlated_job_id=8,
                 extracted_ticket="JOSW-X369")
    c = ctx(observations=[parent],
            jobs=[{"id": 7, "source": "Main Leads", "ticket_number": "JOSW-X369"},
                  {"id": 8, "source": "AGN", "ticket_number": "JOSW-X369"}])
    r = cr.resolve_company(obs(MIKE_MARKER, message_id="c1", quoted_message_id="p1"), c)
    assert r["company"] is None
    assert "CONFLICT" in r["basis"]


def test_D_clean_parent_is_inherited():
    parent = obs(AGN_TEXT, message_id="p1")
    c = ctx(observations=[parent])
    r = cr.resolve_company(obs("on my way", message_id="c1", quoted_message_id="p1"), c)
    assert (r["company"], r["confidence"]) == (cr.AGN, cr.LINKED)


def test_E_absent_parent_leaves_a_valid_child_marker_standing():
    """Absent is not corrupt. The child's own exact marker still holds."""
    r = cr.resolve_company(
        obs(AGN_TEXT, message_id="c1", quoted_message_id="never-captured"), ctx())
    assert (r["company"], r["confidence"]) == (cr.AGN, cr.EXACT)


def test_conflicted_parent_still_poisons():
    parent = obs(AGN_TEXT + "\nJoy Locksmith", message_id="p1")
    c = ctx(observations=[parent])
    r = cr.resolve_company(obs(LUCKY_MARKER, message_id="c1", quoted_message_id="p1"), c)
    assert r["company"] is None


def test_unsafe_and_absent_are_distinguishable():
    """The flag the inheritance rule depends on."""
    absent = cr.resolve_company(obs("nothing here"), ctx())
    corrupt = cr.resolve_company(obs("x", job_id=999), ctx())
    assert absent["unsafe"] is False
    assert corrupt["unsafe"] is True
