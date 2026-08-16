"""Per-message company identity.

Mike Jobs, Lucky and AGN are competitors who happen to share a
dispatcher. Correlation identity is (company, normalized ticket), so
deciding WHICH company a message belongs to is a safety decision: get it
wrong in one direction and one company's dispatch suppresses another
company's genuinely new job; get it wrong in the other and an
already-forwarded job is offered for sending twice.

The rule this module exists to enforce:

    A TECHNICIAN DESTINATION IS NOT A COMPANY IDENTITY.

Several technician dispatch and closing groups carry work from more than
one company. A group therefore cannot name the company unless the owner
has confirmed that exact group carries a single company's work -- and
even then, explicit contrary evidence inside the message wins by
producing a conflict rather than being overruled.

Everything here is deterministic and pure: no SQL, no network, no clock,
no model. The caller builds one ResolverContext per request and every
lookup is an index hit, so a request costs no query per message.

WHAT IS NOT A COMPANY SIGNAL, deliberately:

  * parser format ("Main Leads", a bare "Ticket:"/"PDL:" field,
    "Power By Ucallz App"). Format describes the TEMPLATE a message was
    written in. Two companies can post in the same template, so format
    can never stand in for identity. jobs.source is translated to a
    company ONLY through an explicit persisted-job link.
  * technician identity, area, or group-name similarity.
  * the generic word "Locksmith".
  * a static company value sitting on a technician group that the owner
    has not confirmed as single-company.
"""

import re
from typing import Optional

MIKE_JOBS = "Mike Jobs"
LUCKY = "Lucky"
AGN = "AGN"

# Confidence of the answer, not of a guess -- there are no guesses here.
#   EXACT      an owner-confirmed marker, provenance or allowlisted group
#   LINKED     inherited through a quoted parent or a persisted job
#   UNRESOLVED no deterministic signal, or signals that disagree
EXACT = "EXACT"
LINKED = "LINKED"
UNRESOLVED = "UNRESOLVED"

# jobs.source stores a parser FORMAT name. Translating it to a company is
# legitimate only when reached through an explicit persisted job or link
# context, never from raw text that merely looks like that format.
JOB_SOURCE_COMPANY = {
    "Main Leads": MIKE_JOBS,
    "AGN": AGN,
    "Lucky": LUCKY,
}

# Exact groups the owner has confirmed carry ONE company's work. Listed by
# exact group id: not by technician name, not by area, not by group-name
# similarity. Adding a group here is an owner decision, never an inference.
SINGLE_COMPANY_GROUPS = {
    "120363424227833077@g.us": MIKE_JOBS,   # Sebai Augusta Dispatch
    "120363426584048614@g.us": MIKE_JOBS,   # Sebai Augusta Closing
    "120363428199635701@g.us": MIKE_JOBS,   # Ashraf Charlotte Dispatch
    "120363428143815156@g.us": MIKE_JOBS,   # Ashraf Charlotte Closings
    "120363424464824773@g.us": MIKE_JOBS,   # Adam - Greenville (intake)
}

# --------------------------------------------------------------------
# Exact company markers.
#
# EVERY marker must match a WHOLE LINE. Prefix matching would let
# "NotRef: AGN Locksmith Services" or "Co: LUCKY LOCKSMITHERS" assert a
# company identity that nobody confirmed -- a near miss must resolve to
# nothing and go to human review, which is the safe way to be wrong.
#
# The live corpus bears this out: the labelled markers appear only as
# their own complete lines (30 occurrences each), so anchoring costs
# nothing real while closing the near-miss hole.
#
# Only owner-confirmed forms are listed. "Co: LUCKY LOCKSMITH LLC" is
# here because the owner confirmed that exact variant; larger phrases
# like "Joy Locksmith Pros" and "Birmingham Locksmith pros" -- both
# present in the live corpus -- are deliberately absent and stay
# unresolved until separately confirmed.
#
# Case and surrounding whitespace are normalized; nothing else is.
# --------------------------------------------------------------------
_EXACT_LINE_MARKERS = {
    "ref: agn locksmith services": (AGN, 'exact marker line "Ref: AGN Locksmith Services"'),
    "co: lucky locksmith": (LUCKY, 'exact marker line "Co: LUCKY LOCKSMITH"'),
    "co: lucky locksmith llc": (LUCKY, 'exact marker line "Co: LUCKY LOCKSMITH LLC"'),
    "joy locksmith": (MIKE_JOBS, 'exact marker line "Joy Locksmith"'),
    "locksmith pros": (MIKE_JOBS, 'exact marker line "Locksmith Pros"'),
}

_MAX_PARENT_DEPTH = 12


def text_markers(text: str) -> list:
    """Every exact owner-confirmed company marker in the text.

    Returns [(company, basis), ...]. Two different companies here is a
    real conflict, not a precedence question.
    """
    found, seen = [], set()
    for line in (text or "").splitlines():
        hit = _EXACT_LINE_MARKERS.get(" ".join(line.split()).lower())
        if hit and hit[0] not in seen:
            seen.add(hit[0])
            found.append(hit)
    return found


def company_for_job_source(source: Optional[str]) -> Optional[str]:
    """Company for a PERSISTED job's source. Only ever called with a real
    job row -- never with a format guessed from raw text."""
    return JOB_SOURCE_COMPANY.get((source or "").strip()) if source else None


class ResolverContext:
    """Request-scoped indexes. Built once, read many times.

    The caller owns all SQL; this object only holds already-fetched rows
    keyed for O(1) lookup, which is what keeps resolution from degrading
    into a query per message or a recursive SQL walk.
    """

    def __init__(self, registry_rows=None, observations=None,
                 outgoing_audit=None, jobs=None,
                 single_company_groups=None, normalize_ticket=None):
        self.registry = {}
        for row in registry_rows or []:
            if row.get("group_id"):
                self.registry[row["group_id"]] = row

        self.observations = {}
        for row in observations or []:
            if row.get("message_id"):
                self.observations.setdefault(row["message_id"], row)

        self.audit = {}
        for row in outgoing_audit or []:
            if row.get("message_id"):
                self.audit.setdefault(row["message_id"], row)

        self.jobs = {}
        for row in jobs or []:
            if row.get("id") is not None:
                self.jobs[row["id"]] = row

        self.single_company_groups = dict(
            SINGLE_COMPANY_GROUPS if single_company_groups is None
            else single_company_groups)
        self._norm = normalize_ticket or (lambda t: (t or "").strip().upper())

    # -- signal sources -------------------------------------------------
    def registry_company(self, group_id):
        """Owner-confirmed UPSTREAM provenance: an intake group states
        whose work it is. Technician destinations are excluded here --
        that is what the allowlist is for."""
        row = self.registry.get(group_id) or {}
        if (row.get("confirmed_by_owner") and row.get("company")
                and row.get("role") == "COMPANY_JOB_INTAKE"):
            return row["company"]
        return None

    def allowlisted_company(self, group_id):
        return self.single_company_groups.get(group_id)

    def parent_of(self, message_id):
        """A quoted parent may be a captured observation or a message the
        owner sent by hand. Missing is ABSENT, not an error."""
        if not message_id:
            return None, None
        row = self.observations.get(message_id)
        if row is not None:
            return row, "observation"
        row = self.audit.get(message_id)
        if row is not None:
            return row, "outgoing audit"
        return None, None

    def job(self, job_id):
        return self.jobs.get(job_id)

    def normalize_ticket(self, ticket):
        return self._norm(ticket)


def _result(company, basis, confidence, unsafe=False):
    return {"company": company, "basis": basis, "confidence": confidence,
            "unsafe": unsafe}


def _unresolved(basis):
    """Nothing deterministic was found. Silence, not corruption."""
    return _result(None, basis, UNRESOLVED)


def _unsafe(basis):
    """Signals that are present but CANNOT be trusted: a conflict, a quote
    cycle, or a link pointing at a job that is missing, mismatched or
    contradictory.

    The distinction from _unresolved matters for inheritance. An absent
    parent is silence and leaves the child's own evidence standing; a
    parent whose linkage is demonstrably corrupt poisons the child,
    because the corruption is evidence that we do not actually know what
    this thread of messages refers to.
    """
    return _result(None, basis, UNRESOLVED, unsafe=True)


def resolve_company(observation, context, _seen=None, _depth=0) -> dict:
    """Which company does this ONE message belong to?

    Collects every deterministic signal and only then decides. Signals
    are never allowed to silently overwrite each other: if two of them
    name different companies the answer is UNRESOLVED with the conflict
    spelled out, because "AGN says one thing and the group says another"
    is a fact the operator must see, not a tie for code to break.

    Returns {"company", "basis", "confidence"}; company is None whenever
    confidence is UNRESOLVED.
    """
    obs = observation or {}
    signals = []          # [(company, basis, confidence)]

    # (1) upstream owner-confirmed provenance
    upstream = context.registry_company(obs.get("group_id"))
    if upstream:
        signals.append((upstream, "owner-confirmed intake group provenance", EXACT))

    # (2) exact owner-confirmed markers in the message itself
    for company, basis in text_markers(obs.get("text") or ""):
        signals.append((company, basis, EXACT))

    # (5) exact owner-confirmed single-company destination group
    allowed = context.allowlisted_company(obs.get("group_id"))
    if allowed:
        signals.append((allowed, "owner-confirmed single-company group", EXACT))

    # (3) quoted parent inheritance -- fails closed
    quoted = obs.get("quoted_message_id")
    if quoted:
        seen = set(_seen or ())
        here = obs.get("message_id")
        if here:
            seen.add(here)
        if quoted in seen or _depth >= _MAX_PARENT_DEPTH:
            # A quote cycle proves nothing and must not spin.
            return _unsafe("quoted parent cycle")
        parent, where = context.parent_of(quoted)
        if parent is not None:
            inherited = resolve_company(parent, context, _seen=seen, _depth=_depth + 1)
            if inherited.get("unsafe"):
                # The parent EXISTS and its identity is demonstrably
                # broken -- conflicting markers, a cycle, or a link to a
                # job that is missing, mismatched or contradictory.
                # Letting the child's own weaker marker decide here would
                # quietly resolve a thread we know is corrupt.
                return _unsafe("quoted %s parent is unsafe: %s"
                               % (where, inherited["basis"]))
            if inherited["company"]:
                signals.append((inherited["company"],
                                "inherited from quoted %s parent" % where, LINKED))
        # A parent we never captured is simply an ABSENT signal -- silence,
        # not corruption. The child's own exact marker still stands.
        # Fabricating a company from a message we do not have would be a
        # guess; discarding the child's real evidence would be overreach.

    # (4) linked canonical job identity -- fails closed
    link_ids = [("job_id", obs.get("job_id")),
                ("correlated_job_id", obs.get("correlated_job_id"))]
    link_companies = {}
    for field, job_id in link_ids:
        if job_id is None:
            continue
        job = context.job(job_id)
        if job is None:
            return _unsafe("%s %r references a job that does not exist" % (field, job_id))
        obs_ticket = context.normalize_ticket(obs.get("extracted_ticket"))
        job_ticket = context.normalize_ticket(job.get("ticket_number"))
        if obs_ticket and job_ticket and obs_ticket != job_ticket:
            return _unsafe("%s ticket mismatch (%s vs linked job %s)"
                           % (field, obs_ticket, job_ticket))
        company = company_for_job_source(job.get("source"))
        if not company:
            return _unsafe("%s links a job whose source %r names no known company"
                           % (field, job.get("source")))
        link_companies[field] = company
    if len(set(link_companies.values())) > 1:
        return _unsafe("CONFLICT: job_id and correlated_job_id name different companies (%s)"
                       % " vs ".join(sorted(set(link_companies.values()))))
    for field, company in link_companies.items():
        signals.append((company, "linked canonical job via %s" % field, LINKED))

    # ---- decide -------------------------------------------------------
    if not signals:
        return _unresolved("no deterministic company signal")

    distinct = sorted({company for company, _b, _c in signals})
    if len(distinct) > 1:
        # Explicit conflict. Never resolved by precedence: an AGN header
        # inside a Mike-confirmed group means the owner has to look, not
        # that one signal is more important than the other.
        return _unsafe("CONFLICT: %s (%s)" % (
            " vs ".join(distinct),
            "; ".join("%s -> %s" % (basis, company) for company, basis, _c in signals)))

    company = distinct[0]
    exact = [s for s in signals if s[2] == EXACT]
    chosen = exact[0] if exact else signals[0]
    return _result(company, chosen[1], EXACT if exact else LINKED)
