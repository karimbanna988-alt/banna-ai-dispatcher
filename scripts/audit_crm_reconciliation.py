"""Reconcile REAL source activity against what the CRM reports.

READ-ONLY. This script opens the database, reads, and prints. It writes
nothing -- not to SQLite, not to the API, not to the network.

The point is to avoid validating Banna against Banna. Dataset A is built
from raw persisted observations using the production classification rules
(which group is an intake group, what counts as a ticket, which company
the resolver establishes). Dataset B is what the CRM reporting read-model
independently says about the same window. If those two disagree, the
disagreement is the finding.

Correlation identity stays (resolved_company, normalized_ticket) exactly
as production defines it, so the same ticket string posted by two
different companies is two jobs and must not be collapsed.

Usage:
    python scripts/audit_crm_reconciliation.py --start ISO --end ISO
    python scripts/audit_crm_reconciliation.py --since-recovery
"""

import argparse
import pathlib
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import operator_api as api                                   # noqa: E402
from dispatcher import (company_resolver as cr,               # noqa: E402
                        crm_summary as crm,
                        dispatch_candidates as dc,
                        storage)

NY = ZoneInfo(crm.OPERATIONAL_TIMEZONE)


def _ny(stamp):
    return stamp.astimezone(NY).strftime("%m-%d %H:%M") if stamp else "-"


def dataset_a(conn, start, end):
    """REAL source activity: job postings actually captured in the window.

    Built from raw observations, NOT from the CRM. Excluded on purpose:
    outgoing messages, technician chatter, closings, anything without a
    ticket, and anything from a group that is not an owner-confirmed job
    intake. Duplicate source message ids cannot occur (message_id is the
    stored key) but repeated tickets within a company collapse to one
    job, which is what the business means by "a job".
    """
    observations = storage.list_whatsapp_pending_observations(conn, include_resolved=True)
    context = cr.ResolverContext(
        registry_rows=storage.list_group_registry(conn),
        observations=observations,
        outgoing_audit=storage.list_whatsapp_outgoing_audit(conn),
        jobs=[], normalize_ticket=dc.normalize_ticket)

    rows, seen_messages = [], set()
    for obs in observations:
        if obs.get("group_role") != "COMPANY_JOB_INTAKE":
            continue                                   # not a job source
        if (obs.get("direction") or "").lower().startswith("out"):
            continue                                   # outgoing is not intake
        stamp = crm._parse(obs.get("timestamp")) or crm._parse(obs.get("received_at"))
        if stamp is None or not (start <= stamp < end):
            continue
        message_id = obs.get("message_id")
        if message_id in seen_messages:
            continue                                   # exact source duplicate
        seen_messages.add(message_id)
        tickets = dc.all_tickets(obs.get("text") or "")
        if not tickets:
            continue                                   # chatter, not a job
        company = cr.resolve_company(obs, context)["company"]
        rows.append({
            "at": stamp, "message_id": message_id, "ticket": tickets[0],
            "all_tickets": tickets, "group": obs.get("group_name"),
            "company": company, "confirmed": bool(obs.get("group_mapping_confirmed")),
        })

    rows.sort(key=lambda r: r["at"])
    jobs = {}
    for row in rows:
        key = (row["company"], dc.normalize_ticket(row["ticket"]))
        jobs.setdefault(key, []).append(row)
    return rows, jobs


def dataset_b(conn, start, end):
    """What the CRM reporting read-model says about the same window.

    Uses the SAME projection and the SAME bucket rules the product ships,
    with explicit bounds instead of a named range, so the comparison is
    against real behaviour rather than a re-implementation.
    """
    projection = api.route_whatsapp_candidates(None, {}, {})
    jobs, seen = {}, set()
    for candidate in projection.get("candidates") or []:
        stamp = crm._parse(candidate.get("event_at")) or crm._parse(candidate.get("received_at"))
        if stamp is None or not (start <= stamp < end):
            continue
        if not candidate.get("ticket"):
            continue
        key = (candidate.get("source"), str(candidate.get("ticket")).strip().upper())
        if key in seen:
            continue
        seen.add(key)
        jobs[key] = {
            "at": stamp, "message_id": candidate.get("message_id"),
            "ticket": candidate.get("ticket"), "company": candidate.get("source"),
            "area": ", ".join(p for p in (candidate.get("city"), candidate.get("state")) if p),
            "service": candidate.get("service"),
            "technician": candidate.get("recommended"),
            "observed_destination": candidate.get("actual_destination"),
            "status": candidate.get("status"), "bucket": crm._bucket(candidate),
        }
    return jobs


def reconcile(source_jobs, crm_jobs):
    results = []
    for key in sorted(set(source_jobs) | set(crm_jobs), key=lambda k: (str(k[0]), str(k[1]))):
        company, ticket = key
        a_rows, b = source_jobs.get(key), crm_jobs.get(key)
        if a_rows and b:
            verdict = "MATCH"
            if len(a_rows) > 1:
                verdict = "MATCH (repost x%d collapsed)" % len(a_rows)
            results.append({"key": key, "at": a_rows[0]["at"], "group": a_rows[0]["group"],
                            "company": company, "ticket": ticket, "crm": True,
                            "dupes": len(a_rows), "verdict": verdict, "b": b})
        elif a_rows and not b:
            results.append({"key": key, "at": a_rows[0]["at"], "group": a_rows[0]["group"],
                            "company": company, "ticket": ticket, "crm": False,
                            "dupes": len(a_rows), "verdict": "MISSING_FROM_CRM", "b": None})
        else:
            results.append({"key": key, "at": b["at"], "group": "-", "company": company,
                            "ticket": ticket, "crm": True, "dupes": 0,
                            "verdict": "EXTRA_IN_CRM", "b": b})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="ISO start (UTC)")
    parser.add_argument("--end", help="ISO end (UTC)")
    parser.add_argument("--since-recovery", action="store_true",
                        help="from the first observation after the capture outage to now")
    args = parser.parse_args()

    conn = api.get_conn()
    try:
        if args.since_recovery:
            start = crm._parse(conn.execute(
                "SELECT MIN(received_at) FROM whatsapp_pending_observations "
                "WHERE received_at > '2026-08-17T21:00'").fetchone()[0])
            end = datetime.now(timezone.utc)
        else:
            start, end = crm._parse(args.start), crm._parse(args.end)
        if not start or not end:
            parser.error("need --start/--end or --since-recovery")

        print("VALIDATION WINDOW")
        print("  UTC : %s  ->  %s" % (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")))
        print("  %s : %s  ->  %s" % (crm.OPERATIONAL_TIMEZONE, _ny(start), _ny(end)))

        source_rows, source_jobs = dataset_a(conn, start, end)
        crm_jobs = dataset_b(conn, start, end)

        print("\nDATASET A (raw source)  intake postings=%d  distinct jobs=%d"
              % (len(source_rows), len(source_jobs)))
        print("DATASET B (CRM view)    jobs=%d" % len(crm_jobs))

        results = reconcile(source_jobs, crm_jobs)
        print("\n| # | Source time | Ticket | Group | Company | CRM | Dup | Result |")
        print("|---|-------------|--------|-------|---------|-----|-----|--------|")
        for i, r in enumerate(results, 1):
            print("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
                i, _ny(r["at"]), r["ticket"], (r["group"] or "-")[:24],
                r["company"] or "UNRESOLVED", "yes" if r["crm"] else "NO",
                r["dupes"], r["verdict"]))

        counts = Counter(r["verdict"].split(" ")[0] for r in results)
        print("\nRESULT TOTALS:", dict(counts))

        print("\nBY COMPANY")
        a_co = Counter((k[0] or "Unknown") for k in source_jobs)
        b_co = Counter((k[0] or "Unknown") for k in crm_jobs)
        for name in sorted(set(a_co) | set(b_co)):
            flag = "" if a_co[name] == b_co[name] else "   <-- MISMATCH"
            print("  %-14s source=%-4d crm=%-4d%s" % (name, a_co[name], b_co[name], flag))

        print("\nBY AREA (CRM view; source rows carry no parsed area of their own)")
        for area, n in Counter((j["area"] or "Unknown") for j in crm_jobs.values()).most_common():
            print("  %-24s %d" % (area, n))

        print("\nTECHNICIAN / STATUS")
        for key, j in sorted(crm_jobs.items(), key=lambda kv: kv[1]["at"]):
            if j["observed_destination"]:
                state = ("TECHNICIAN MATCH" if j["technician"] and
                         j["technician"].strip().lower() == j["observed_destination"].strip().lower()
                         else "TECHNICIAN MISMATCH" if j["technician"] else "NOT COMPARABLE (no recommendation)")
            else:
                state = "NOT YET DISPATCHED" if not j["technician"] else "TECHNICIAN UNKNOWN downstream"
            print("  %-12s %-14s banna=%-10s observed=%-10s %s | status=%s"
                  % (j["ticket"], (j["company"] or "?")[:14], j["technician"] or "-",
                     j["observed_destination"] or "-", state, j["status"]))

        print("\nCLOSINGS / REVENUE")
        with_amount = conn.execute("SELECT COUNT(*) FROM events WHERE closing_amount IS NOT NULL").fetchone()[0]
        print("  events carrying a verified closing amount:", with_amount)
        print("  verified revenue:", "UNAVAILABLE" if not with_amount else
              conn.execute("SELECT SUM(closing_amount) FROM events").fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
