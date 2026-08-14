"""Connected Operator UI V2 -- local-only JSON API + static file server.

Zero-subscription by construction: stdlib http.server only (no Flask, no
new pip dependency), no outbound network call anywhere in this module,
no WhatsApp library, no browser automation. Every state-changing route
is a thin wrapper around an existing, already-audited
dispatcher.application.DispatcherService method or a pure read -- this
file owns no SQL and no business logic of its own; see
dispatcher/application.py and luxor_desk_read_model.py, which it reuses
exactly as luxor_desk.py already does. (Structural guard:
tests/test_operator_api.py greps this file for raw-SQL call literals
and fails if any appear.)

WhatsApp itself is never touched programmatically: this server never
opens a socket to web.whatsapp.com, never drives a browser, never reads
a session cookie *from WhatsApp*. "Open WhatsApp" is a purely
client-side (JS) action; this server only records that a human later
confirmed a message was sent -- the same manual-confirmation contract
adapters.ManualWhatsAppHandoffAdapter already implements.

Authentication: one dispatcher, one password (LUXOR_DEMO_PASSWORD --
the exact same gitignored env var luxor_desk.py already uses, so there
is only ever one password to remember/rotate). Sessions are stateless,
HMAC-signed cookies (no server-side session store, no new database
table) verified against a separate, auto-generated, gitignored signing
secret (OPERATOR_API_SESSION_SECRET). See SecurityHeaders/AuthGate
below for the full contract: HttpOnly/SameSite cookies, CSRF tokens on
every mutating request, login-attempt throttling, and no-store caching
so no page or API response is ever cached with real customer data on it
after logout.

Run: python operator_api.py [--port 8512]
The default DB is the real one (dispatcher.storage.connect()'s default)
-- but that default is only ever reachable via _db_path() when running
in "live" mode (see dispatcher.db_safety.current_mode()). Under pytest,
or whenever LUXOR_DEMO_MODE=true, OPERATOR_API_DB_PATH becomes
mandatory and must not resolve to the real database -- _db_path()
raises UnsafeDatabasePathError otherwise. Tests always set it, and
always to a temporary path.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

import luxor_desk_read_model as rm  # noqa: E402
import luxor_desk_shared  # noqa: E402
import luxor_ops_shared as shared  # noqa: E402
import whatsapp_bridge_adapter  # noqa: E402
from dispatcher import commercial, kpi_read_model, storage  # noqa: E402
from dispatcher import db_safety  # noqa: E402
from dispatcher.db_safety import UnsafeDatabasePathError  # noqa: E402
from dispatcher.action_names import ActionName  # noqa: E402
from dispatcher.action_names import TECHNICIAN_UPDATE_TYPES  # noqa: E402
from dispatcher.app_models import ClosingCommand, TechnicianUpdateCommand  # noqa: E402
from dispatcher.application import DispatcherService  # noqa: E402
from dispatcher.closing import ClosingInput, render_closing_from_persisted_fields  # noqa: E402
from dispatcher.policy import get_policy  # noqa: E402
from dispatcher.updates import reply_for_update_type  # noqa: E402
from dispatcher import technician_ack  # noqa: E402
from dispatcher import message_patterns  # noqa: E402
from dispatcher.parser import extract_job  # noqa: E402
from dispatcher import pipeline  # noqa: E402
from dispatcher.workflow_projection import (  # noqa: E402
    WorkflowProjection, derive_lifecycle_steps, derive_next_action, derive_pending_message_next_action,
)

STATIC_DIR = REPO_ROOT / "operator_ui_v2_connected"
DISPATCHER_ACTOR = "dispatcher"
LIVE_WINDOW_MINUTES = luxor_desk_shared.NEW_JOBS_LIVE_WINDOW_SECONDS // 60  # 60 -- single source of truth

# get_live_dispatch_queue()'s "live" bucket is bounded only by the one-time
# console-activation boundary (console_activation_state.live_queue_started_at,
# set once and never advanced -- see storage.ensure_live_queue_activated's
# docstring). It is NOT a rolling recency window, so a raw quo_pending_messages
# row can sit there for days/weeks without ever aging out on its own.
# route_new_jobs already compensates for this with its own LIVE_WINDOW_MINUTES
# split (60 min) for the Jobs screen's "live" vs "older" tabs -- but
# _compute_action_center historically fed the whole unfiltered "live" bucket
# straight into Action Center, so a message from 10+ days ago could appear in
# ACTION NOW as if it were current work (see docs/ACTION_CENTER_STALE_MESSAGE_FIX.md).
#
# This is deliberately a MUCH more generous window than LIVE_WINDOW_MINUTES:
# LIVE_WINDOW_MINUTES marks "brand new, needs immediate triage" for the Jobs
# screen's own tabs; this constant marks "still plausibly today's operational
# work" for Action Center, which must not disappear an item the moment it
# turns 61 minutes old, only once it's genuinely stale backlog. 24 hours is
# a deliberate, explicit choice for this locksmith-dispatch business (jobs
# are expected to resolve same-day), not a value derived from any existing
# constant -- tune if that assumption changes.
#
# Applies ONLY to pre-commit raw-message items (INCOMING_MESSAGE / kind
# "message"). Committed jobs (kind "job") are never filtered by age here --
# their eligibility for Action Center is governed by jobs.status via
# derive_next_action(), exactly as before this fix; an old job that is
# genuinely still active (e.g. Approved with no technician assigned) must
# keep surfacing regardless of how long it has been waiting -- that is the
# whole point of Action Center for a job that's fallen behind.
ACTION_CENTER_MESSAGE_LOOKBACK_MINUTES = 24 * 60


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


DEMO_MODE_ENV_VAR = db_safety.DEMO_MODE_ENV_VAR  # set by scripts/start_demo.ps1 only -- never set for a normal/real run
_current_db_mode = db_safety.current_mode  # exposed under this name for tests/readability at call sites in this file


def _db_path() -> Optional[Path]:
    return db_safety.resolve_db_path_from_env()


def get_conn():
    return storage.connect(_db_path())


def _service(conn) -> DispatcherService:
    return DispatcherService(conn, queue_reader=rm.LuxorDeskQueueReader())


# =============================================================== AUTH ====
PASSWORD_ENV_VAR = luxor_desk_shared.PASSWORD_ENV_VAR  # "LUXOR_DEMO_PASSWORD" -- same one password, reused, never duplicated
SESSION_SECRET_ENV_VAR = "OPERATOR_API_SESSION_SECRET"
SESSION_COOKIE_NAME = "operator_session"
SESSION_TTL_SECONDS = 12 * 3600  # one dispatcher shift
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300


def _env_file_path() -> Path:
    override = os.environ.get("OPERATOR_API_ENV_FILE")
    return Path(override) if override else REPO_ROOT / ".env"


def _get_or_create_session_secret() -> bytes:
    """Reads OPERATOR_API_SESSION_SECRET, or generates one and appends
    it to .env (gitignored, never printed) the first time this runs --
    "a separate ignored session-signing secret", never hard-coded, never
    the same value as the dispatcher's own password."""
    secret = os.environ.get(SESSION_SECRET_ENV_VAR)
    if secret:
        return secret.encode("utf-8")
    generated = secrets.token_hex(32)
    env_path = _env_file_path()
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\n{SESSION_SECRET_ENV_VAR}={generated}\n")
    os.environ[SESSION_SECRET_ENV_VAR] = generated
    print(f"Generated a new {SESSION_SECRET_ENV_VAR} and appended it to {env_path.name} (value not printed).")
    return generated.encode("utf-8")


SESSION_SECRET = _get_or_create_session_secret()
_LOGIN_ATTEMPTS: dict = {}  # client ip -> list[float] of recent failed-attempt timestamps, in-memory only
_ACTIVE_SESSIONS: set = set()  # in-memory only -- a server restart itself acts as "log everyone out"


def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_token() -> str:
    """Issues a new signed session token AND records it as active --
    signature/expiry alone are not enough to satisfy "logout invalidates
    the session": a captured pre-logout cookie value must stop working
    the moment logout is called, not merely once it expires 12 hours
    later. See invalidate_session_token()."""
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    payload = str(expires_at)
    token = f"{payload}.{_sign(payload)}"
    _ACTIVE_SESSIONS.add(token)
    return token


def verify_session_token(token: Optional[str]) -> bool:
    if not token or token not in _ACTIVE_SESSIONS or "." not in token:
        return False
    payload, _, signature = token.rpartition(".")
    if not signature or not hmac.compare_digest(_sign(payload), signature):
        return False
    try:
        expires_at = int(payload)
    except ValueError:
        return False
    if time.time() >= expires_at:
        _ACTIVE_SESSIONS.discard(token)
        return False
    return True


def invalidate_session_token(token: Optional[str]) -> None:
    if token:
        _ACTIVE_SESSIONS.discard(token)


def csrf_token_for(session_token: str) -> str:
    return _sign(f"{session_token}:csrf")


def _check_login_rate_limit(client_ip: str) -> None:
    now = time.time()
    attempts = [t for t in _LOGIN_ATTEMPTS.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[client_ip] = attempts
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        raise ApiError(429, "Too many login attempts. Try again in a few minutes.")


def _record_failed_login(client_ip: str) -> None:
    _LOGIN_ATTEMPTS.setdefault(client_ip, []).append(time.time())


def _clear_login_attempts(client_ip: str) -> None:
    _LOGIN_ATTEMPTS.pop(client_ip, None)


# --------------------------------------------------------------- Helpers --
def _age_minutes(age_seconds: float) -> int:
    return max(0, int(age_seconds // 60))


def _find_new_job_entry(conn, message_id: str):
    """Re-derives (job, classify_result, result, tracking) for one
    pending message by re-running the exact same live-queue computation
    the Jobs list itself used -- never a second parse/cache of the raw
    text. Raises ApiError(404) if the message is no longer live
    (already dispatched, or aged past the boundary in a way that
    removed it -- callers should refresh their list). Search covers the
    full "live-since-activation" universe, not just the <=60-minute
    presentation window -- an older-unresolved job must still be
    actionable."""
    live, _backlog, _other = rm.get_live_dispatch_queue(conn)
    for entry in live:
        if entry.item["message_id"] == message_id:
            return entry
    raise ApiError(404, f"No live pending message with id {message_id!r}.")


def _new_job_dto(conn, entry) -> dict:
    job, classify_result, result, tracking = entry.job, entry.classify_result, entry.result, entry.tracking
    item = entry.item
    resolution, eligible, market_label = rm.resolve_job_routing(conn, job, item["raw_text"])
    destination = eligible[0]["dispatch_group"] if eligible else None
    technician_name = eligible[0]["name"] if len(eligible) == 1 else None
    is_closings_stream = item["group_label"] == "CLOSINGS"
    needs_review = bool(result.decision.missing_information) or bool(classify_result.conflicting_fields)
    review_problems = list(result.decision.missing_information) + [
        f"conflicting {f}" for f in classify_result.conflicting_fields
    ]

    k_required = False
    if not is_closings_stream:
        stream_key = rm.policy_stream_key(item["group_label"], job.source.value)
        policy = get_policy(stream_key, classify_result.message_type.value)
        k_required = bool(policy.acknowledgment_required)

    copied = tracking["copied_at"] is not None
    k_sent = tracking["acknowledged_at"] is not None
    if k_required and not k_sent:
        bucket = "needs_action"
    elif not copied:
        bucket = "needs_action"
    else:
        bucket = "waiting"

    return {
        "message_id": item["message_id"],
        "source": job.source.value,
        "ticket": job.ticket_number.value,
        "service": job.service_type.value,
        "city": job.city.value,
        "state": job.state.value,
        "age_minutes": _age_minutes(entry.age_seconds),
        "urgency": entry.urgency,
        "bucket": bucket,
        "is_closings_stream": is_closings_stream,
        "needs_review": needs_review,
        "review_problems": review_problems,
        "k_required": k_required,
        "k_sent": k_sent,
        "copied": copied,
        "whatsapp_opened": tracking["whatsapp_opened_at"] is not None,
        "dispatched": tracking["dispatched_manually_at"] is not None,
        "destination": destination,
        "technician_name": technician_name,
        "eligible_count": len(eligible),
        "luxor_message": result.luxor_message,
        "original_message_masked": shared.mask_sensitive(item["raw_text"]),
        "customer": job.customer_name.value,
        "phone": job.phone.value,
        "address": job.address.value,
        "vehicle": job.vehicle.value,
        "notes": job.notes.value,
        "market_label": market_label,
        "canonical_area": resolution.canonical_area,
        "steps": derive_lifecycle_steps({
            "entity_type": "INCOMING_MESSAGE", "needs_review": needs_review, "k_required": k_required,
            "k_sent": k_sent, "eligible_count": len(eligible), "dispatched": tracking["dispatched_manually_at"] is not None,
        }),
        "delivery_mode": _DELIVERY_MODE,
    }


def _technician_dto(t: dict) -> dict:
    return {
        "id": t["id"],
        "name": t["name"],
        "areas": t["areas"],
        "skills": t["skills"],
        "active": t["active"],
        "dispatch_group": t["dispatch_group"],
        "closing_group": t["closing_group"],
        "configured": bool((t["dispatch_group"] or "").strip()) and bool((t["closing_group"] or "").strip()),
    }


def _action_result_dto(result) -> dict:
    return dataclasses.asdict(result)


def _read_json_body(handler: "Handler") -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > 1_000_000:
        raise ApiError(400, "Request body too large.")
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(400, f"Invalid JSON body: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApiError(400, "JSON body must be an object.")
    return parsed


def _parse_query(path: str) -> dict:
    query = urlparse(path).query
    parsed = parse_qs(query)
    return {k: v[0] for k, v in parsed.items()}


def _require(body: dict, field: str) -> str:
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiError(400, f"{field!r} is required.")
    return value


# ---------------------------------------------------------------- AUTH ROUTES
def route_login(handler: "Handler", match: dict, body: dict) -> dict:
    client_ip = handler.client_ip if handler else "test"
    _check_login_rate_limit(client_ip)
    password = body.get("password") or ""
    expected = os.environ.get(PASSWORD_ENV_VAR)
    if not expected:
        raise ApiError(500, f"Login is not configured ({PASSWORD_ENV_VAR} is not set).")
    if not hmac.compare_digest(password, expected):
        _record_failed_login(client_ip)
        raise ApiError(401, "Invalid password.")
    _clear_login_attempts(client_ip)
    token = make_session_token()
    if handler:
        handler.set_session_cookie(token)
    return {"success": True, "csrf_token": csrf_token_for(token), "demo_mode": os.environ.get(DEMO_MODE_ENV_VAR, "").lower() == "true"}


def route_logout(handler: "Handler", match: dict, body: dict) -> dict:
    if handler:
        invalidate_session_token(handler._session_token())
        handler.clear_session_cookie()
    return {"success": True}


def route_me(handler: "Handler", match: dict, body: dict) -> dict:
    """Also returns a fresh csrf_token, same as route_login -- both are
    just csrf_token_for(session_token), a pure function of the session
    token this request already proved possession of via its (already
    authenticated) cookie, so re-deriving it here creates no new secret
    and no new capability. Without this, a page reload with a still-valid
    session cookie left the frontend's in-memory CSRF token empty
    (app.js's boot() calls checkSession(), not login()) -- every
    mutating action would then 403 with "Missing or invalid CSRF token"
    until the operator explicitly logged out and back in. Found while
    testing the Fast Path changes in this same sprint; fixed here rather
    than worked around, since a broken write path after any browser
    reload is a direct hit to dispatch speed, not a cosmetic issue."""
    token = handler._session_token() if handler else None
    return {
        "authenticated": True, "demo_mode": os.environ.get(DEMO_MODE_ENV_VAR, "").lower() == "true",
        "csrf_token": csrf_token_for(token) if token else None,
    }


def route_demo_status(handler: "Handler", match: dict, body: dict) -> dict:
    """Unauthenticated on purpose -- exposes nothing but a boolean, so the
    login screen itself can show a DEMO MODE indicator before any
    credential is entered. No PII, no secret, no job data."""
    return {"demo_mode": os.environ.get(DEMO_MODE_ENV_VAR, "").lower() == "true"}


# --------------------------------------------------------- Route handlers -
def route_new_jobs(handler: "Handler", match: dict, body: dict) -> dict:
    """Returns incoming Quo/WhatsApp messages that have not yet been
    turned into a real job, split into a recent window and an archive
    bucket. These are raw messages pending triage, NOT a count of real
    unresolved jobs (see docs/command_center.md's audit section) -- the
    frontend must never present "older"/"archive" as "N unresolved
    jobs". total_pending_messages is the real, uncapped backlog size
    (quo_pending_messages, status pending/loaded); "live" and "older"
    are drawn only from the newest-100-messages page
    get_live_dispatch_queue() operates on (see
    luxor_ops_shared.fetch_pending_messages's own docstring for why that
    cap exists) -- when total_pending_messages exceeds what "older"
    contains, the UI must say so rather than imply completeness."""
    conn = get_conn()
    try:
        live, _backlog, _other = rm.get_live_dispatch_queue(conn)
        dtos = [_new_job_dto(conn, e) for e in live]
        bucket_rank = {"needs_action": 0, "waiting": 1}
        live_window = sorted(
            (d for d in dtos if d["age_minutes"] <= LIVE_WINDOW_MINUTES),
            key=lambda d: (bucket_rank.get(d["bucket"], 2), d["age_minutes"]),
        )
        older_unresolved = sorted(
            (d for d in dtos if d["age_minutes"] > LIVE_WINDOW_MINUTES),
            key=lambda d: d["age_minutes"],
        )
        return {
            "live": live_window,
            "older": older_unresolved,
            "live_window_minutes": LIVE_WINDOW_MINUTES,
            "total_pending_messages": shared.count_pending_messages(conn),
        }
    finally:
        conn.close()


_INBOX_REVIEW_DEFAULT_PAGE_SIZE = 25


def route_inbox_review(handler: "Handler", match: dict, body: dict) -> dict:
    """Archive / Inbox Review: real, honest pagination over
    quo_pending_messages -- historical inbound messages and messages
    awaiting review, NEVER labeled as jobs. Unlike route_new_jobs (which
    feeds the Jobs screen's actionable-now triage queue via
    get_live_dispatch_queue's correlation/dedup gate), this route shows
    every raw message including ones that never correlate to a job --
    surfacing "unmatched messages" is the whole point of this screen.
    Never loads the whole backlog into the browser: page_size is capped
    server-side (luxor_ops_shared.MAX_ARCHIVE_PAGE_SIZE), and the total
    count returned is a real COUNT(*) of the matching set, not the size
    of the page fetched."""
    try:
        page = int(body.get("page") or 1)
    except (TypeError, ValueError):
        raise ApiError(400, "page must be an integer.")
    try:
        page_size = int(body.get("page_size") or _INBOX_REVIEW_DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        raise ApiError(400, "page_size must be an integer.")
    search = (body.get("search") or "").strip() or None
    unmatched_only = str(body.get("unmatched_only") or "").lower() in ("1", "true", "yes")

    conn = get_conn()
    try:
        rows, total = shared.paginated_pending_messages(
            conn, page=page, page_size=page_size, search=search, unmatched_only=unmatched_only,
        )
        messages = [
            {
                "message_id": r["message_id"],
                "source": r["group_label"],
                "text_preview": shared.mask_sensitive(r["raw_text"])[:280],
                "received_at": r["received_at"] or r["fetched_at"],
                "status": r["status"],
            }
            for r in rows
        ]
        return {
            "messages": messages,
            "page": page,
            "page_size": min(max(page_size, 1), shared.MAX_ARCHIVE_PAGE_SIZE),
            "total": total,
            "label": "Historical inbound messages -- not confirmed jobs.",
        }
    finally:
        conn.close()


def route_sync(handler: "Handler", match: dict, body: dict) -> dict:
    """Explicit manual ingestion -- the same dispatcher.quo_ingest.poll_for_new_messages()
    the Streamlit New Jobs "Sync" button already calls; nothing here
    fetches Quo automatically or on a timer. See
    docs/whatsapp_groups_readiness.md-adjacent note in the final report
    for why: no approved background poller is currently running (see
    scripts/background_poller.py's own docstring -- it must be started
    by a human and stays running only as long as that terminal does)."""
    from dispatcher import quo_client
    from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError
    from dispatcher.quo_ingest import poll_for_new_messages

    conn = get_conn()
    try:
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            raise ApiError(400, "Quo is not configured in this environment.")
        try:
            client = quo_client.QuoClient(api_key=api_key)
            outcome = poll_for_new_messages(conn, client)
        except QuoConfigError as exc:
            storage.record_poll_error(conn, str(exc))
            raise ApiError(400, "Quo is not configured.") from exc
        except QuoAPIError as exc:
            storage.record_poll_error(conn, str(exc))
            raise ApiError(502, "Quo temporarily unavailable.") from exc
        shared.clear_pending_caches()
        return {"success": True, "queued": len(outcome.queued)}
    finally:
        conn.close()


_ACTIVE_NEXT_ACTION_LABELS = (
    "Awaiting technician", "Add technician update", "Prepare company reply",
    "Waiting for customer", "Record closing",
)


def _active_next_action_label(conn, job: dict) -> str:
    """The fixed 5-label vocabulary Phase 4D requires -- derived from
    exactly the same facts luxor_desk_shared.active_job_next_step_sentence()
    already uses (never a second, competing next-action computation),
    just mapped onto shorter row labels instead of a full sentence."""
    latest = job.get("latest_technician_update")
    if latest is None:
        return "Awaiting technician"
    if latest.get("update_type") == "Custom":
        return "Add technician update"
    if latest.get("update_type") == "Completed":
        return "Record closing"
    if luxor_desk_shared._reply_already_sent_since(conn, job["id"], latest.get("created_at")):
        return "Waiting for customer"
    return "Prepare company reply"


def _group_waiting_jobs_by_technician(all_jobs: list) -> dict:
    """Precomputed once per request: technician_name -> [(job_id, ticket_number), ...]
    for every Approved job currently assigned to them. Profiling
    route_action_center at 500 synthetic jobs showed this exact
    "who else is this technician already juggling" computation was the
    dominant cost -- _technician_k_dto used to scan the full all_jobs
    list per job to build it, making the whole route effectively O(n^2)
    (5.1M dict.get() calls for 500 jobs x 10 reps in the profile). One
    grouping pass shared across every job turns that into O(n)."""
    by_tech: dict = {}
    for j in all_jobs:
        if j.get("status") == "Approved" and j.get("assigned_technician_name"):
            by_tech.setdefault(j["assigned_technician_name"], []).append((j["id"], j["ticket_number"]))
    return by_tech


def _technician_k_dto(
    conn, job: dict, all_jobs: list, pending_observations: Optional[list] = None,
    waiting_by_technician: Optional[dict] = None,
) -> Optional[dict]:
    """None means "not applicable right now": no technician assigned, or
    the job has already moved past the awaiting-K stage (a technician
    update has already been recorded). Otherwise returns either the
    already-confirmed accept/decline fact, or -- while still waiting --
    the classified candidate observations a human can act on. Every
    candidate's category/reasons come from dispatcher.technician_ack,
    never invented here.

    `pending_observations` lets a caller looping over many jobs in one
    request (route_active, route_action_center) fetch this list once
    and pass it in, instead of this function re-querying the whole
    table fresh for every single job -- same result, same query, just
    not repeated N times. `waiting_by_technician` does the equivalent
    for the other_waiting computation below -- see
    _group_waiting_jobs_by_technician's docstring. Pass nothing (the
    default) for either to compute them here, unchanged for any other
    caller."""
    technician_name = job.get("assigned_technician_name")
    if job.get("status") != "Approved" or not technician_name:
        return None
    schema_present = storage.technician_acceptance_schema_present(conn)
    if not schema_present:
        return None
    ta = storage.get_technician_acceptance(conn, job["id"], schema_present=schema_present)
    if ta["accepted_at"]:
        return {"status": "accepted", "accepted_at": ta["accepted_at"], "via_message_id": ta["accepted_via_message_id"]}
    if ta["declined_at"]:
        return {"status": "declined", "declined_at": ta["declined_at"], "reason": ta["decline_reason"]}

    # rm.get_active_jobs_detailed()'s SQL joins in the technician's name/
    # dispatch_group already (fetch_jobs() never selects the raw
    # assigned_technician_id column) -- build the minimal shape
    # classify_candidates() actually needs from those, rather than a
    # second lookup keyed by an id this dict doesn't carry.
    technician = {"name": technician_name, "dispatch_group": job.get("assigned_technician_dispatch_group")}
    if pending_observations is None:
        pending_observations = storage.list_whatsapp_pending_observations(conn, include_resolved=False)
    if waiting_by_technician is not None:
        other_waiting = [ticket for jid, ticket in waiting_by_technician.get(technician_name, []) if jid != job["id"]]
    else:
        other_waiting = [
            other["ticket_number"] for other in all_jobs
            if other["id"] != job["id"] and other.get("assigned_technician_name") == technician_name
            and other.get("status") == "Approved"
        ]
    matches = technician_ack.classify_candidates(job, technician, pending_observations, other_waiting_ticket_numbers=other_waiting)
    candidates = [
        {"message_id": m.message_id, "category": m.category, "is_decline_shaped": m.is_decline_shaped, "reasons": m.reasons}
        for m in matches if m.category != technician_ack.CATEGORY_UNMATCHED
    ]
    return {"status": "waiting", "candidates": candidates}


def route_active(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        service = _service(conn)
        jobs = rm.get_active_jobs_detailed(conn)
        # Fetched once for the whole request instead of once per job in
        # the loop below -- same table, same rows, N fewer queries for N
        # active jobs (this list doesn't depend on which job is being
        # processed, only on global pending-observation state).
        pending_observations = storage.list_whatsapp_pending_observations(conn, include_resolved=False)
        # See _group_waiting_jobs_by_technician's docstring -- was an
        # O(n) scan per job (O(n^2) overall) before this hoist.
        waiting_by_technician = _group_waiting_jobs_by_technician(jobs)
        entries = []
        for j in jobs:
            episode = storage.get_shadow_episode_for_job(conn, j["id"])
            # Computed once per job here, then handed to
            # _workflow_projection_dto below instead of letting it
            # recompute the same two DTOs a second time internally.
            technician_k = _technician_k_dto(conn, j, jobs, pending_observations, waiting_by_technician)
            company_update_owed = _company_update_owed_dto(conn, j)
            entries.append({
                "id": j["id"],
                "source": j["source"],
                "ticket": j["ticket_number"],
                "status": j["status"],
                "technician_name": j.get("assigned_technician_name"),
                "technician_dispatch_group": j.get("assigned_technician_dispatch_group"),
                "city": j.get("service_area"),
                "description": j.get("description"),
                "latest_technician_update": j.get("latest_technician_update"),
                "next_action": _active_next_action_label(conn, j),
                "bucket": luxor_desk_shared.active_job_bucket(j),
                "waiting_since": j.get("last_event_at") or j.get("updated_at"),
                "shadow_episode": _episode_summary(episode) if episode else None,
                "company_update_owed": company_update_owed,
                "technician_k": technician_k,
                "workflow": _workflow_projection_dto(
                    conn, service, j, jobs,
                    technician_k=technician_k, company_update_owed=company_update_owed,
                    pending_observations=pending_observations, waiting_by_technician=waiting_by_technician,
                ),
            })
        return {"entries": entries}
    finally:
        conn.close()


def route_technician_k_confirm_accepted(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    message_id = _require(body, "message_id")
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.confirm_technician_accepted(job_id, message_id)
        if not result.success:
            raise ApiError(400, result.user_message or "Could not confirm technician acceptance.")
        return _action_result_dto(result)
    finally:
        conn.close()


def route_technician_k_confirm_declined(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    message_id = _require(body, "message_id")
    reason = _require(body, "reason")
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.confirm_technician_declined(job_id, message_id, reason)
        if not result.success:
            raise ApiError(400, result.user_message or "Could not confirm technician decline.")
        return _action_result_dto(result)
    finally:
        conn.close()


_TECHNICIAN_UPDATE_TYPES_SET = frozenset(TECHNICIAN_UPDATE_TYPES)


def _company_update_owed_dto(conn, job: dict) -> Optional[dict]:
    """Whether this job has a technician status update that hasn't been
    answered to the company yet, plus the deterministic reply preview --
    computed fresh from reply_for_update_type() each time (a pure
    formatter, same pattern route_closing_confirm_sent already uses),
    never persisted speculatively. None means nothing is owed: either no
    technician update has ever been recorded, or the latest one already
    has a Company-Update-Sent-Manually note at or after it."""
    latest = job.get("latest_technician_update")
    if latest is None:
        return None
    if luxor_desk_shared._reply_already_sent_since(conn, job["id"], latest.get("created_at")):
        return None
    reply = reply_for_update_type(latest["update_type"], latest.get("eta_minutes"))
    return {
        "update_type": latest["update_type"],
        "eta_minutes": latest.get("eta_minutes"),
        "raw_update": latest.get("raw_message"),
        "recorded_at": latest.get("created_at"),
        "suggested_reply": reply.reply,
        "confirmed": reply.confirmed,
        "reason": reply.reason,
    }


def _closing_summary_for_projection(conn, service: DispatcherService, job: dict) -> Optional[dict]:
    """The subset of _closing_dto's fields dispatcher.workflow_projection
    actually needs, computed the same way _closing_dto does -- only
    meaningful once status == "Closed" (matches _closing_dto's own
    logic, which returns empty/None fields for any other status), so
    this returns None otherwise rather than doing the extra work for
    every job regardless of stage."""
    if job.get("status") != "Closed":
        return None
    full = _closing_dto(conn, service, job)
    return {
        "missing": full["missing"], "supported": full["supported"],
        "unsupported_reason": full["unsupported_reason"], "sent_confirmed": full["sent_confirmed"],
    }


# Sentinel distinguishing "caller didn't supply this" from a legitimate
# precomputed value of None (both technician_k and company_update_owed
# are routinely None -- "not applicable right now" is a real value, not
# an absence).
_UNSET = object()


def _build_workflow_context(
    conn, service: DispatcherService, job: dict, all_jobs: list, *,
    technician_k=_UNSET, company_update_owed=_UNSET, pending_observations: Optional[list] = None,
    waiting_by_technician: Optional[dict] = None,
) -> dict:
    """Gathers exactly the facts dispatcher.workflow_projection.derive_next_action()
    needs for one job, reusing the same per-job DTOs every existing
    screen already computes. A caller that already computed
    technician_k/company_update_owed for this job this request (e.g.
    route_active, which needs both independently of the projection) can
    pass them in to avoid computing them twice; pending_observations and
    waiting_by_technician let a caller looping over many jobs compute
    those once instead of once per job (see _technician_k_dto and
    _group_waiting_jobs_by_technician). See that module's docstring for
    the full precedence rules this feeds."""
    if technician_k is _UNSET:
        technician_k = _technician_k_dto(conn, job, all_jobs, pending_observations, waiting_by_technician)
    if company_update_owed is _UNSET:
        company_update_owed = _company_update_owed_dto(conn, job)
    return {
        "status": job.get("status"),
        "reconciliation_state": job.get("reconciliation_state"),
        "technician_name": job.get("assigned_technician_name"),
        "technician_k": technician_k,
        "latest_technician_update": job.get("latest_technician_update"),
        "company_update_owed": company_update_owed,
        "closing": _closing_summary_for_projection(conn, service, job),
    }


def _workflow_projection_dto(
    conn, service: DispatcherService, job: dict, all_jobs: list, *,
    technician_k=_UNSET, company_update_owed=_UNSET, pending_observations: Optional[list] = None,
    waiting_by_technician: Optional[dict] = None,
) -> dict:
    ctx = _build_workflow_context(
        conn, service, job, all_jobs,
        technician_k=technician_k, company_update_owed=company_update_owed, pending_observations=pending_observations,
        waiting_by_technician=waiting_by_technician,
    )
    projection = derive_next_action(ctx)
    return {
        "next_action": projection.next_action,
        "blocking_reason": projection.blocking_reason,
        "human_review_needed": projection.human_review_needed,
        "terminal": projection.terminal,
        "steps": derive_lifecycle_steps({**ctx, "entity_type": "JOB", "human_review_needed": projection.human_review_needed}),
        "delivery_mode": _DELIVERY_MODE,
    }


# ============================================================ ACTION CENTER
# The one operational "what needs a human right now" surface. Exactly
# ten sections, each 100% derived from dispatcher.workflow_projection --
# no section computes its own next-action signal. The first three draw
# from the pre-commit pending-message projection
# (derive_pending_message_next_action); the other seven draw from the
# committed-job projection (derive_next_action). Both projections emit
# the same normalized item shape via _normalize_action_center_item(), so
# Action Center itself never branches on "message vs. job" business
# logic -- only on which projection function produced the item.
_ACTION_CENTER_SECTION_MAX = 5

# (section_key, section_title, next_action_code) -- one row per one of
# the ten required sections, in the required order.
_ACTION_CENTER_SECTIONS = (
    ("new_jobs_to_review", "New jobs to review", "REVIEW_NEW_JOB"),
    ("company_k_owed", "Company K acknowledgments owed", "ACK_COMPANY_K"),
    ("assign_technician", "Technician assignment required", "ASSIGN_TECHNICIAN"),
    ("ready_to_dispatch", "Ready to send to technician", "SEND_JOB_TO_TECHNICIAN"),
    ("waiting_technician_k", "Waiting for technician K", "WAIT_TECHNICIAN_K"),
    ("technician_updates_to_review", "Technician updates to review", "REVIEW_TECHNICIAN_UPDATE"),
    ("company_updates_owed", "Company updates owed", "UPDATE_COMPANY"),
    ("closings_to_review", "Closings to review", "REVIEW_CLOSING"),
    ("closings_ready_to_send", "Closings ready to send", "SEND_CLOSING_TO_COMPANY"),
    ("human_review_required", "Human review required", "HUMAN_REVIEW_NEEDED"),
)

# Presentation-only grouping on top of the exact 10 sections above -- the
# 10 remain the source of truth for the API's `sections` key, for filters,
# and for every existing test; `groups` is an additional view for the
# operator so scanning isn't "10 equally-weighted buckets" when 8 of them
# are all "you have something to do right now," 1 is "you're waiting on
# someone else," and 1 is "this is blocked and needs a decision."
_ACTION_CENTER_GROUP_MAX = 10
_ACTION_CENTER_GROUPS = (
    ("action_now", "Action now", (
        "new_jobs_to_review", "company_k_owed", "assign_technician", "ready_to_dispatch",
        "technician_updates_to_review", "company_updates_owed", "closings_to_review", "closings_ready_to_send",
    )),
    ("waiting", "Waiting on someone else", ("waiting_technician_k",)),
    ("needs_review", "Needs review", ("human_review_required",)),
)
_SECTION_NEXT_ACTION = {key: code for key, _title, code in _ACTION_CENTER_SECTIONS}

# Every action-generating next_action always resolves to exactly one
# delivery mode in this build: a human copies text and explicitly
# confirms it was sent -- there is no code path capable of an automatic
# Quo or WhatsApp send, in demo mode or otherwise (see
# adapters.DisabledQuoOutboundAdapter / ManualWhatsAppHandoffAdapter).
# EXTERNAL_CONNECTOR_REQUIRED would apply once a sanctioned write API
# exists; MOCK_DEMO_DELIVERY would apply if demo mode ever grew a
# separate auto-confirm path (it deliberately hasn't -- see
# docs/COMMERCIAL_READINESS.md's "human approval always required" line).
_DELIVERY_MODE = "MANUAL COPY"


# ---------------------------------------------------- deterministic priority
# Transparent, integer-bucket priority -- never an AI/model judgment call,
# never a raw decimal score exposed to the UI. Every input is either a
# fact the projection functions already computed (human_review_needed,
# next_action) or a trivially-derivable age from an already-stored
# timestamp -- no urgency is invented, no appointment-time guess is made
# (this codebase has no reliably-parsed appointment time to use).
PRIORITY_URGENT = "URGENT"
PRIORITY_DUE = "DUE"
PRIORITY_NORMAL = "NORMAL"
PRIORITY_WAITING = "WAITING"
_PRIORITY_RANK = {PRIORITY_URGENT: 0, PRIORITY_DUE: 1, PRIORITY_NORMAL: 2, PRIORITY_WAITING: 3}

# Age thresholds in whole minutes. Chosen to roughly track this
# dispatch business's own existing operational target -- the same
# "not dispatched within target" framing kpi_read_model.get_command_center()
# already uses for its attention-needed KPI -- not a new invented notion
# of urgency.
_PRIORITY_URGENT_AGE_MINUTES = 60
_PRIORITY_DUE_AGE_MINUTES = 20
# A job waiting on the technician to reply to K only escalates out of
# WAITING once the wait itself has gone on long enough that a human
# should probably follow up -- the "technician response age" factor,
# not a prediction of whether/when the technician will actually answer.
_PRIORITY_WAITING_ESCALATE_AGE_MINUTES = 30


def _item_age_minutes(entity_type: str, age_source) -> Optional[int]:
    """Age in minutes for priority-bucket purposes only (display
    formatting stays the frontend's job, unchanged). Message items
    already carry a precomputed age_minutes int. Job items carry an ISO
    timestamp needing parsing -- same real-wall-clock comparison
    luxor_desk_read_model.py's age_seconds already uses for the New Jobs
    queue, not a new mechanism."""
    if entity_type == "INCOMING_MESSAGE":
        return age_source
    if not age_source:
        return None
    try:
        dt = datetime.fromisoformat(str(age_source).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 60))


def _priority_bucket(*, entity_type: str, age_source, human_review_needed: bool, next_action: str) -> str:
    if human_review_needed:
        return PRIORITY_URGENT
    age = _item_age_minutes(entity_type, age_source)
    if next_action == "WAIT_TECHNICIAN_K":
        if age is not None and age >= _PRIORITY_WAITING_ESCALATE_AGE_MINUTES:
            return PRIORITY_DUE
        return PRIORITY_WAITING
    if age is None:
        return PRIORITY_NORMAL
    if age >= _PRIORITY_URGENT_AGE_MINUTES:
        return PRIORITY_URGENT
    if age >= _PRIORITY_DUE_AGE_MINUTES:
        return PRIORITY_DUE
    return PRIORITY_NORMAL


def _normalize_action_center_item(
    *, entity_type: str, entity_id, ticket: Optional[str], source: Optional[str],
    technician: Optional[str], age_source: Optional[str], projection, destination: Optional[str], screen: str,
) -> dict:
    """The one normalized shape every Action Center item has, regardless
    of whether it came from the pending-message projection or the
    committed-job projection. `age_source` is either an ISO timestamp
    (job items) or a pre-computed minutes-ago string (message items,
    which only ever carry an age in minutes, not a timestamp) -- the
    frontend's ageText()/minutesSince() helpers already handle both."""
    priority = _priority_bucket(
        entity_type=entity_type, age_source=age_source,
        human_review_needed=projection.human_review_needed, next_action=projection.next_action,
    )
    return {
        "entity_type": entity_type, "entity_id": entity_id, "ticket": ticket, "source": source,
        "technician": technician, "stage": projection.next_action, "next_action": projection.next_action,
        "blocking_reason": projection.blocking_reason, "age": age_source,
        "human_review_needed": projection.human_review_needed, "destination": destination,
        "delivery_mode": _DELIVERY_MODE, "screen": screen, "priority": priority,
        # Back-compat aliases for the current frontend (operator_ui_v2_connected/js/app.js
        # reads "id"/"kind"/"waiting_since"/"age_minutes" today) -- both the
        # normalized and legacy names are populated from the same values,
        # not computed twice.
        "id": entity_id, "kind": "job" if entity_type == "JOB" else "message",
        "waiting_since": age_source if entity_type == "JOB" else None,
        "age_minutes": age_source if entity_type == "INCOMING_MESSAGE" else None,
    }


def _job_target_screen(job: dict) -> str:
    """Which screen a job's own detail actually lives on -- checked
    against each screen's REAL membership rule, not guessed. Active only
    covers rm.ACTIVE_JOB_WORKSPACE_STATUSES (New/Approved/In Progress/
    Needs Review); Closings covers "In Progress" and Closed-but-unsettled;
    Closed+Settled/Cancelled/Rejected only appears on the Completed
    (history) screen. Shared by Action Center, search, and Next
    Actionable Job so none of them ever silently land on a screen the
    job doesn't actually appear on.

    A "Handle Manually" job (assign_technician rejected every candidate,
    or an operator explicitly chose "Handle manually") falls into NONE
    of the three -- found as a real bug while testing search against
    Scenario D: routing it to "active" like everything else landed on a
    screen where it silently isn't listed, and the operator ends up
    looking at whatever job happens to be first in that list instead,
    with no error or indication anything went wrong. Action Center is
    the one place such a job is guaranteed to be visible (its
    `human_review_required` section), so that's the honest answer here
    -- this build has no dedicated single-job detail screen for this
    status yet."""
    status = job.get("status")
    if status in ("Cancelled", "Rejected"):
        return "history"
    if status == "Closed":
        return "history" if job.get("reconciliation_state") == "Settled" else "closings"
    if status in rm.ACTIVE_JOB_WORKSPACE_STATUSES:
        return "active"
    return "actioncenter"


def _action_center_job_item(
    conn, service: DispatcherService, job: dict, all_jobs: list, pending_observations: Optional[list] = None,
    waiting_by_technician: Optional[dict] = None,
) -> dict:
    job = dict(job)
    job["latest_technician_update"] = rm.get_latest_technician_update(conn, job["id"])
    projection = derive_next_action(
        _build_workflow_context(
            conn, service, job, all_jobs,
            pending_observations=pending_observations, waiting_by_technician=waiting_by_technician,
        )
    )
    return _normalize_action_center_item(
        entity_type="JOB", entity_id=job["id"], ticket=job["ticket_number"], source=job["source"],
        technician=job.get("assigned_technician_name"), age_source=job.get("last_event_at") or job.get("updated_at"),
        projection=projection, destination=job.get("assigned_technician_dispatch_group"), screen=_job_target_screen(job),
    )


def _action_center_message_item(d: dict) -> dict:
    ctx = {
        "needs_review": d["needs_review"], "k_required": d["k_required"], "k_sent": d["k_sent"],
        "eligible_count": d["eligible_count"], "dispatched": d["dispatched"],
    }
    projection = derive_pending_message_next_action(ctx)
    if d["review_problems"] and projection.next_action == "REVIEW_NEW_JOB":
        projection = WorkflowProjection(
            projection.next_action, "; ".join(d["review_problems"]), projection.human_review_needed, projection.terminal,
        )
    return _normalize_action_center_item(
        entity_type="INCOMING_MESSAGE", entity_id=d["message_id"], ticket=d["ticket"], source=d["source"],
        technician=d.get("technician_name"), age_source=d["age_minutes"],
        projection=projection, destination=d.get("destination"), screen="jobs",
    )


def _compute_action_center(conn) -> dict:
    """The one place Action Center's sections/groups are computed --
    route_action_center and route_next_actionable_job both call this
    rather than each running their own independent pass, so "what's
    next" always agrees with what the Action Center screen itself
    shows."""
    service = _service(conn)
    all_jobs = shared.fetch_jobs(conn)
    non_terminal = [j for j in all_jobs if j["status"] not in ("Cancelled", "Rejected")]
    # See route_active's identical comment: fetched once for the
    # whole request rather than once per job inside the loop below.
    pending_observations = storage.list_whatsapp_pending_observations(conn, include_resolved=False)
    # See _group_waiting_jobs_by_technician's docstring -- this was the
    # dominant cost at volume (an O(n) scan per job, O(n^2) overall)
    # before being hoisted to one O(n) pass shared by every job.
    waiting_by_technician = _group_waiting_jobs_by_technician(all_jobs)

    by_next_action: dict = {}
    for j in non_terminal:
        item = _action_center_job_item(conn, service, j, non_terminal, pending_observations, waiting_by_technician)
        by_next_action.setdefault(item["next_action"], []).append(item)

    live, _backlog, _other = rm.get_live_dispatch_queue(conn)
    # See ACTION_CENTER_MESSAGE_LOOKBACK_MINUTES's own comment: "live" here
    # is bounded only by a one-time activation boundary, not a rolling
    # window, so an old raw message must be explicitly excluded here or it
    # would appear in ACTION NOW indefinitely. It remains fully visible in
    # Inbox Review (route_inbox_review, unaffected by this filter) and in
    # the Jobs screen's "older" tab (route_new_jobs) -- never deleted,
    # never marked resolved, never hidden anywhere except this one screen.
    current_dtos = (
        d for d in (_new_job_dto(conn, e) for e in live)
        if d["age_minutes"] <= ACTION_CENTER_MESSAGE_LOOKBACK_MINUTES
    )
    for d in current_dtos:
        item = _action_center_message_item(d)
        by_next_action.setdefault(item["next_action"], []).append(item)

    sections = {}
    for section_key, title, next_action_code in _ACTION_CENTER_SECTIONS:
        items = by_next_action.get(next_action_code, [])
        sections[section_key] = {"title": title, "items": items[:_ACTION_CENTER_SECTION_MAX], "total": len(items)}

    groups = []
    for group_key, group_title, section_keys in _ACTION_CENTER_GROUPS:
        group_items = [
            item for sk in section_keys for item in by_next_action.get(_SECTION_NEXT_ACTION[sk], [])
        ]
        # Deterministic ordering only: priority bucket rank first
        # (URGENT before DUE before NORMAL before WAITING), then
        # oldest first within the same bucket. No AI ranking, no
        # decimal score -- ties are broken by insertion order, which
        # is itself deterministic (section order, then discovery
        # order within a section).
        group_items.sort(key=lambda i: (
            _PRIORITY_RANK.get(i["priority"], 2),
            -(_item_age_minutes(i["entity_type"], i["age"]) or 0),
        ))
        groups.append({
            "key": group_key, "title": group_title,
            "items": group_items[:_ACTION_CENTER_GROUP_MAX], "total": len(group_items),
        })

    return {"sections": sections, "groups": groups}


def route_action_center(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        return _compute_action_center(conn)
    finally:
        conn.close()


def route_next_actionable_job(handler: "Handler", match: dict, body: dict) -> dict:
    """Deterministic "what should I do next" control (Priority 5 of the
    operator-speed brief): the single highest-priority item from the
    ACTION NOW group -- the exact same ordering Action Center itself
    displays, never a separate ranking. Deliberately does NOT look at
    WAITING (nothing to action there) and deliberately DOES surface
    NEEDS REVIEW if that group is genuinely the highest-priority thing
    outstanding (an excluded_current_id lets the frontend ask for "the
    next one after this" once the current job stops being actionable,
    without the caller having to know why). Never mutates anything --
    purely a read that tells the frontend where to navigate next."""
    excluded_id = body.get("exclude_entity_id")
    conn = get_conn()
    try:
        ac = _compute_action_center(conn)
        candidates = [
            item for group in ac["groups"] if group["key"] in ("action_now", "needs_review")
            for item in group["items"]
        ]
        if excluded_id is not None:
            candidates = [c for c in candidates if str(c["entity_id"]) != str(excluded_id)]
        if not candidates:
            return {"found": False, "item": None}
        candidates.sort(key=lambda i: (
            _PRIORITY_RANK.get(i["priority"], 2),
            -(_item_age_minutes(i["entity_type"], i["age"]) or 0),
        ))
        return {"found": True, "item": candidates[0]}
    finally:
        conn.close()


# ---------------------------------------------------------------- SEARCH
# One global search across committed jobs and live pending messages.
# Deliberately excludes phone/address/customer name from both the query
# match and the result shape -- ticket/source/technician/city/service
# only, matching the brief's explicit "no unnecessary PII" instruction.
_SEARCH_RESULT_MAX = 20
_SEARCH_MIN_QUERY_LENGTH = 2


def _search_job_haystack(job: dict) -> list:
    return [job.get("ticket_number"), job.get("source"), job.get("assigned_technician_name"),
            job.get("service_area"), job.get("description")]


def _search_message_haystack(d: dict) -> list:
    return [d.get("ticket"), d.get("source"), d.get("technician_name"), d.get("city"), d.get("service")]


def _search_matches(haystack: list, q: str) -> bool:
    return any(h and q in str(h).lower() for h in haystack)


def _search_job_result(
    conn, service: DispatcherService, job: dict, all_jobs: list, pending_observations: list,
    waiting_by_technician: Optional[dict] = None,
) -> dict:
    job = dict(job)
    job["latest_technician_update"] = rm.get_latest_technician_update(conn, job["id"])
    projection = derive_next_action(
        _build_workflow_context(
            conn, service, job, all_jobs,
            pending_observations=pending_observations, waiting_by_technician=waiting_by_technician,
        )
    )
    return {
        "entity_type": "JOB", "entity_id": job["id"], "ticket": job["ticket_number"], "source": job["source"],
        "technician": job.get("assigned_technician_name"), "stage": job["status"], "next_action": projection.next_action,
        "age": job.get("last_event_at") or job.get("updated_at"), "screen": _job_target_screen(job),
    }


def _search_message_result(d: dict) -> dict:
    return {
        "entity_type": "INCOMING_MESSAGE", "entity_id": d["message_id"], "ticket": d["ticket"], "source": d["source"],
        "technician": d.get("technician_name"), "stage": "New", "next_action": "REVIEW_NEW_JOB",
        "age": d["age_minutes"], "screen": "jobs",
    }


def route_search(handler: "Handler", match: dict, body: dict) -> dict:
    q = (body.get("q") or "").strip().lower()
    if len(q) < _SEARCH_MIN_QUERY_LENGTH:
        return {"query": q, "results": []}
    conn = get_conn()
    try:
        service = _service(conn)
        all_jobs = shared.fetch_jobs(conn)
        pending_observations = storage.list_whatsapp_pending_observations(conn, include_resolved=False)
        waiting_by_technician = _group_waiting_jobs_by_technician(all_jobs)
        matched_jobs = [j for j in all_jobs if _search_matches(_search_job_haystack(j), q)]
        results = [
            _search_job_result(conn, service, j, all_jobs, pending_observations, waiting_by_technician)
            for j in matched_jobs[:_SEARCH_RESULT_MAX]
        ]
        if len(results) < _SEARCH_RESULT_MAX:
            live, _backlog, _other = rm.get_live_dispatch_queue(conn)
            dtos = [_new_job_dto(conn, e) for e in live]
            matched_messages = [d for d in dtos if _search_matches(_search_message_haystack(d), q)]
            results.extend(_search_message_result(d) for d in matched_messages[:_SEARCH_RESULT_MAX - len(results)])
        return {"query": q, "results": results, "truncated": len(matched_jobs) > _SEARCH_RESULT_MAX}
    finally:
        conn.close()


def route_technician_update_record(handler: "Handler", match: dict, body: dict) -> dict:
    """Records a technician's real status reply (Going/ETA/No Answer/No
    Show/Not Available/Customer Cancelled/Completed/Custom) against an
    already-committed job -- the dispatcher-typed status text, never
    inferred from free text. This is distinct from the Commercial
    Update (quote/negotiation) flow already wired on this same screen;
    that flow answers "what price was quoted," this one answers "what
    is the job status," per docs/workflow.md section 8."""
    job_id = int(match["id"])
    update_type = _require(body, "update_type")
    raw_update = _require(body, "raw_update")
    if update_type not in _TECHNICIAN_UPDATE_TYPES_SET:
        raise ApiError(400, f"update_type must be one of {sorted(_TECHNICIAN_UPDATE_TYPES_SET)}.")
    eta_minutes = body.get("eta_minutes")
    conn = get_conn()
    try:
        service = _service(conn)
        command = TechnicianUpdateCommand(
            job_id=job_id, update_type=update_type, raw_update=raw_update,
            eta_minutes=int(eta_minutes) if eta_minutes else None,
        )
        result = service.record_technician_update(command)
        if not result.success:
            raise ApiError(400, result.user_message or "Could not record this update.")
        return _action_result_dto(result)
    finally:
        conn.close()


def route_company_update_prepare(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    update_type = _require(body, "update_type")
    eta_minutes = body.get("eta_minutes")
    manual_text = body.get("manual_text") or None
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.prepare_company_update(
            job_id, update_type, eta_minutes=int(eta_minutes) if eta_minutes else None, manual_text=manual_text,
        )
        if not result.success:
            raise ApiError(400, result.user_message or "Could not prepare a company update.")
        return _action_result_dto(result)
    finally:
        conn.close()


def route_company_update_confirm_sent(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    prepared_text = _require(body, "prepared_text")
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.confirm_company_update_sent(job_id, prepared_text)
        if not result.success:
            raise ApiError(400, result.user_message or "Could not confirm this update as sent.")
        return _action_result_dto(result)
    finally:
        conn.close()


def route_technicians(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        technicians = storage.list_technicians(conn, active_only=False)
        dtos = [_technician_dto(t) for t in technicians]
        return {
            "technicians": dtos,
            "missing_dispatch_group": [t["name"] for t in dtos if not (t["dispatch_group"] or "").strip()],
            "missing_closing_group": [t["name"] for t in dtos if not (t["closing_group"] or "").strip()],
        }
    finally:
        conn.close()


def route_update_destinations(handler: "Handler", match: dict, body: dict) -> dict:
    technician_id = int(match["id"])
    dispatch_group = _require(body, "dispatch_group")
    closing_group = _require(body, "closing_group")
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.update_technician_destinations(technician_id, dispatch_group, closing_group)
        if not result.success:
            raise ApiError(400, result.user_message or "Update failed.")
        return _action_result_dto(result)
    finally:
        conn.close()


def route_job_workspace(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    conn = get_conn()
    try:
        service = _service(conn)
        job = service.get_job_workspace(job_id)
        if job is None:
            raise ApiError(404, f"No job with id {job_id}.")
        history = service.get_job_history(job_id)
        episode = storage.get_shadow_episode_for_job(conn, job_id)
        return {"job": job, "history": history, "shadow_episode": _episode_summary(episode) if episode else None}
    finally:
        conn.close()


def route_acknowledge(handler: "Handler", match: dict, body: dict) -> dict:
    message_id = match["message_id"]
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.acknowledge_job_manually(message_id)
        return _action_result_dto(result)
    finally:
        conn.close()


def route_prepare_dispatch(handler: "Handler", match: dict, body: dict) -> dict:
    message_id = match["message_id"]
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.prepare_technician_dispatch(message_id)
        return _action_result_dto(result)
    finally:
        conn.close()


def route_confirm_dispatch(handler: "Handler", match: dict, body: dict) -> dict:
    message_id = match["message_id"]
    conn = get_conn()
    try:
        entry = _find_new_job_entry(conn, message_id)
        service = _service(conn)
        result = service.confirm_whatsapp_dispatch(
            message_id, entry.job, entry.classify_result, entry.result.luxor_message,
        )
        shared.clear_pending_caches()
        return _action_result_dto(result)
    finally:
        conn.close()


def route_handle_manually(handler: "Handler", match: dict, body: dict) -> dict:
    message_id = match["message_id"]
    conn = get_conn()
    try:
        entry = _find_new_job_entry(conn, message_id)
        service = _service(conn)
        result = service.reject_pending_message(message_id, entry.job, entry.classify_result, entry.result.luxor_message)
        shared.clear_pending_caches()
        return _action_result_dto(result)
    finally:
        conn.close()


def route_pass(handler: "Handler", match: dict, body: dict) -> dict:
    message_id = match["message_id"]
    reason = _require(body, "reason")
    conn = get_conn()
    try:
        entry = _find_new_job_entry(conn, message_id)
        service = _service(conn)
        result = service.reject_pending_message(
            message_id, entry.job, entry.classify_result, entry.result.luxor_message, reason=reason,
        )
        shared.clear_pending_caches()
        return _action_result_dto(result)
    finally:
        conn.close()


_QUOTE_TYPE_VALUES = {e.value for e in commercial.QuoteType}
_CUSTOMER_RESPONSE_VALUES = {e.value for e in commercial.CustomerResponseType}
_CORRECTABLE_FIELDS = {
    "quote_type", "quote_amount", "quote_range_low", "quote_range_high", "currency", "customer_response",
}


def route_commercial_preview(handler: "Handler", match: dict, body: dict) -> dict:
    raw_text = _require(body, "raw_technician_text")
    suggestion = commercial.extract_commercial_update(raw_text)
    return _commercial_update_dto(suggestion)


def _commercial_update_dto(cu) -> dict:
    return {
        "raw_technician_text": cu.raw_technician_text,
        "quote_type": cu.quote_type.value,
        "quote_amount": cu.quote_amount,
        "quote_amount_text": cu.quote_amount_text,
        "quote_range_low": cu.quote_range_low,
        "quote_range_high": cu.quote_range_high,
        "currency": cu.currency,
        "customer_response": cu.customer_response.value,
        "customer_response_text": cu.customer_response_text,
        "follow_up_required": cu.follow_up_required,
        "follow_up_time_text": cu.follow_up_time_text,
        "negotiation_state": cu.negotiation_state.value,
        "confidence": cu.confidence,
        "validation_issues": cu.validation_issues,
        "human_review_required": cu.human_review_required,
    }


def _clean_corrected_fields(raw: Optional[dict]) -> dict:
    if not raw:
        return {}
    unknown = set(raw) - _CORRECTABLE_FIELDS
    if unknown:
        raise ApiError(400, f"Unknown corrected field(s): {sorted(unknown)}")
    cleaned = dict(raw)
    if "quote_type" in cleaned:
        if cleaned["quote_type"] not in _QUOTE_TYPE_VALUES:
            raise ApiError(400, f"Invalid quote_type: {cleaned['quote_type']!r}")
        cleaned["quote_type"] = commercial.QuoteType(cleaned["quote_type"])
    if "customer_response" in cleaned:
        if cleaned["customer_response"] not in _CUSTOMER_RESPONSE_VALUES:
            raise ApiError(400, f"Invalid customer_response: {cleaned['customer_response']!r}")
        cleaned["customer_response"] = commercial.CustomerResponseType(cleaned["customer_response"])
    return cleaned


def route_commercial_save(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    raw_text = _require(body, "raw_technician_text")
    corrected_fields = _clean_corrected_fields(body.get("corrected_fields"))
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.record_commercial_update(
            job_id, raw_text, corrected_fields=corrected_fields or None, dispatcher_actor=DISPATCHER_ACTOR,
        )
        return _action_result_dto(result)
    finally:
        conn.close()


def route_commercial_confirm_reply_sent(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    sent_channel = _require(body, "sent_channel")
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.confirm_commercial_reply_sent(job_id, sent_channel, dispatcher_actor=DISPATCHER_ACTOR)
        return _action_result_dto(result)
    finally:
        conn.close()


def _episode_summary(episode: dict) -> dict:
    return {
        "episode_id": episode.get("episode_id"),
        "normalized_intent": episode.get("normalized_intent"),
        "shadow_suggested_reply": episode.get("shadow_suggested_reply"),
        "dispatcher_actual_reply": episode.get("dispatcher_actual_reply"),
        "human_review_required": episode.get("human_review_required"),
        "customer_response": episode.get("customer_response"),
        "reply_sent_at": episode.get("actual_send_confirmed_at"),
        "reply_sent_channel": episode.get("actual_send_channel"),
    }


# ================================================================ CLOSINGS
def _closing_dto(conn, service: DispatcherService, job: dict) -> dict:
    workspace = service.get_job_workspace(job["id"])
    area, description = luxor_desk_shared._service_area_and_description(workspace["fields"])
    details = storage.get_latest_closing_details(conn, job["id"])

    missing = []
    supported = None
    unsupported_reason = None
    prepared_text = None
    if job["status"] == "Closed":
        if not details or not details.get("amount"):
            missing.append("amount")
        if not details or not details.get("payment_method"):
            missing.append("payment method")
        if not missing:
            closing_input = ClosingInput(
                amount=details["amount"], payment_method=details["payment_method"], parts_amount=details.get("parts_amount"),
            )
            result = render_closing_from_persisted_fields(job["source"], job["ticket_number"], workspace["fields"], closing_input)
            supported = result.supported
            if result.supported:
                prepared_text = result.text
            else:
                unsupported_reason = result.reason

    history = service.get_job_history(job["id"])
    sent_confirmed = any(e.get("final_action") == ActionName.COMPANY_CLOSING_SENT_MANUALLY.value for e in history)

    return {
        "id": job["id"],
        "source": job["source"],
        "ticket": job["ticket_number"],
        "status": job["status"],
        "technician_name": job.get("assigned_technician_name"),
        "area": area,
        "description": description,
        "bucket": luxor_desk_shared.closing_job_bucket(job),
        "closing_recorded": job["status"] == "Closed",
        "amount": details.get("amount") if details else None,
        "payment_method": details.get("payment_method") if details else None,
        "parts_amount": details.get("parts_amount") if details else None,
        "company_share": details.get("company_share") if details else None,
        "technician_share": details.get("technician_share") if details else None,
        "missing": missing,
        "supported": supported,
        "unsupported_reason": unsupported_reason,
        "prepared_text": prepared_text,
        "sent_confirmed": sent_confirmed,
    }


def route_closings(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        service = _service(conn)
        candidates = rm.get_closing_candidates(conn)
        return {"entries": [_closing_dto(conn, service, j) for j in candidates]}
    finally:
        conn.close()


def route_closing_record(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    raw_closing = _require(body, "raw_closing")
    conn = get_conn()
    try:
        service = _service(conn)
        command = ClosingCommand(
            job_id=job_id, raw_closing=raw_closing,
            final_amount=body.get("final_amount") or None, payment_method=body.get("payment_method") or None,
            parts=body.get("parts") or None, company_share=body.get("company_share") or None,
            technician_share=body.get("technician_share") or None,
        )
        result = service.record_closing(command)
        if not result.success:
            raise ApiError(400, result.user_message or "Could not record this closing.")
        return _action_result_dto(result)
    finally:
        conn.close()


def route_closing_prepare(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.prepare_company_closing(job_id)
        return _action_result_dto(result)
    finally:
        conn.close()


def route_closing_confirm_sent(handler: "Handler", match: dict, body: dict) -> dict:
    """Recomputes the company-specific closing text fresh, server-side,
    from currently-persisted fields -- exactly like the existing
    Streamlit Closings workspace does -- rather than trusting any
    client-supplied message text."""
    job_id = int(match["id"])
    conn = get_conn()
    try:
        service = _service(conn)
        job = service.get_job_workspace(job_id)
        if job is None:
            raise ApiError(404, f"No job with id {job_id}.")
        details = storage.get_latest_closing_details(conn, job_id)
        if not details or not details.get("amount") or not details.get("payment_method"):
            raise ApiError(400, "Missing amount or payment method -- cannot confirm sent yet.")
        closing_input = ClosingInput(
            amount=details["amount"], payment_method=details["payment_method"], parts_amount=details.get("parts_amount"),
        )
        result = render_closing_from_persisted_fields(job["source"], job["ticket_number"], job["fields"], closing_input)
        if not result.supported:
            raise ApiError(400, result.reason)
        action_result = service.confirm_company_closing_sent(job_id, result.text)
        if not action_result.success:
            raise ApiError(400, action_result.user_message)
        return _action_result_dto(action_result)
    finally:
        conn.close()


def route_closing_complete(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.complete_job(job_id)
        if not result.success:
            raise ApiError(400, result.user_message)
        return _action_result_dto(result)
    finally:
        conn.close()


# ================================================================= HISTORY
def _history_event_dto(e: dict) -> dict:
    return {
        "created_at": e.get("created_at"),
        "label": luxor_desk_shared.history_event_label(e),
        "note": shared.mask_sensitive(e.get("dispatcher_edits")),
    }


def route_history(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        completed = rm.get_completed_jobs(conn)
        search = (body.get("search") or "").strip().lower()
        source_filter = body.get("source") or "All"
        technician_filter = body.get("technician") or "All"
        status_filter = body.get("status") or "All"
        date_from = body.get("date_from")
        date_to = body.get("date_to")

        def matches(job: dict) -> bool:
            if search and search not in (job.get("ticket_number") or "").lower():
                return False
            if source_filter != "All" and job.get("source") != source_filter:
                return False
            if technician_filter != "All" and job.get("assigned_technician_name") != technician_filter:
                return False
            if status_filter != "All" and job.get("status") != status_filter:
                return False
            job_date_raw = job.get("last_event_at") or job.get("updated_at")
            if job_date_raw and (date_from or date_to):
                job_date = str(job_date_raw)[:10]
                if date_from and job_date < date_from:
                    return False
                if date_to and job_date > date_to:
                    return False
            return True

        filtered = [j for j in completed if matches(j)]
        entries = []
        for j in filtered:
            details = storage.get_latest_closing_details(conn, j["id"])
            entries.append({
                "id": j["id"], "source": j["source"], "ticket": j["ticket_number"], "status": j["status"],
                "technician_name": j.get("assigned_technician_name"),
                "completed_at": j.get("last_event_at") or j.get("updated_at"),
                "amount": details.get("amount") if details else None,
                "payment_method": details.get("payment_method") if details else None,
            })
        return {
            "entries": entries,
            "total": len(completed),
            "filter_options": {
                "sources": sorted({j["source"] for j in completed if j.get("source")}),
                "technicians": sorted({j["assigned_technician_name"] for j in completed if j.get("assigned_technician_name")}),
                "statuses": sorted({j["status"] for j in completed if j.get("status")}),
            },
        }
    finally:
        conn.close()


def route_history_detail(handler: "Handler", match: dict, body: dict) -> dict:
    job_id = int(match["id"])
    conn = get_conn()
    try:
        service = _service(conn)
        job = service.get_job_workspace(job_id)
        if job is None:
            raise ApiError(404, f"No job with id {job_id}.")
        history = service.get_job_history(job_id)
        details = storage.get_latest_closing_details(conn, job_id)
        episode = storage.get_shadow_episode_for_job(conn, job_id)
        area, description = luxor_desk_shared._service_area_and_description(job["fields"])
        return {
            "job": {
                "id": job["id"], "source": job["source"], "ticket": job["ticket_number"], "status": job["status"],
                "technician_name": job.get("assigned_technician_name"), "area": area, "description": description,
            },
            "closing": details,
            "commercial_history": _episode_summary(episode) if episode else None,
            "timeline": [_history_event_dto(e) for e in history],
        }
    finally:
        conn.close()


# ================================================================= REPORTS
def route_reports(handler: "Handler", match: dict, body: dict) -> dict:
    """Read-only. Only fields that actually exist in the database --
    never a computed percentage/profit/balance/parts/fees breakdown,
    since no such fields are persisted anywhere in this schema. Missing
    values are sent as null; the frontend renders "Not recorded", never
    a fabricated number."""
    conn = get_conn()
    try:
        entries = []
        for j in rm.get_completed_jobs(conn):
            details = storage.get_latest_closing_details(conn, j["id"])
            quote = storage.get_latest_verified_commercial_update_for_job(conn, j["id"]) if storage.shadow_schema_present(conn) else None
            entries.append({
                "job": j["ticket_number"],
                "company": j["source"],
                "date": j.get("last_event_at") or j.get("updated_at"),
                "technician": j.get("assigned_technician_name"),
                "area": j.get("service_area"),
                "quoted_amount": (quote.get("quote_amount_text") or quote.get("quote_amount")) if quote else None,
                "final_amount": details.get("amount") if details else None,
                "payment_method": details.get("payment_method") if details else None,
            })
        return {"entries": entries}
    finally:
        conn.close()


# ============================================================= PERFORMANCE
_PERFORMANCE_PERIODS = ("shift", "24h", "7d", "30d", "custom")


def route_performance(handler: "Handler", match: dict, body: dict) -> dict:
    """Read-only KPI dashboard for the Performance screen. Every figure
    comes from kpi_read_model.get_dashboard(), which only ever reads
    jobs/events/shadow_episodes -- this route never writes anything and
    never exposes phone numbers, addresses, or raw message bodies."""
    period = body.get("period") or "24h"
    if period not in _PERFORMANCE_PERIODS:
        raise ApiError(400, f"period must be one of {_PERFORMANCE_PERIODS}")
    source = body.get("source") or None
    area = body.get("area") or None
    custom_start = body.get("start") if period == "custom" else None
    custom_end = body.get("end") if period == "custom" else None
    conn = get_conn()
    try:
        try:
            return kpi_read_model.get_dashboard(
                conn, period=period, source=source, area=area,
                custom_start=custom_start, custom_end=custom_end,
            )
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
    finally:
        conn.close()


# ========================================================= COMMAND CENTER
def route_command_center(handler: "Handler", match: dict, body: dict) -> dict:
    """Read-only composed dashboard for the Command Center landing
    screen. Every figure comes from kpi_read_model.get_command_center(),
    which only calls existing kpi_read_model functions (or small new
    ones defined alongside them) -- this route never writes anything,
    never duplicates a KPI calculation, and never exposes phone numbers,
    addresses, or raw message bodies. See docs/command_center.md.

    The one thing kpi_read_model.py deliberately can't include (it only
    ever reads jobs/events/shadow_episodes SQL, per its own module
    docstring) is a real WhatsApp observation waiting for review -- that
    lives in whatsapp_bridge_adapter's in-memory queue, the same source
    route_whatsapp_pending already reads. Merged in here, capped at 5
    total attention items, never a second WhatsApp queue invented.

    chart_period (query param, GET): one of
    kpi_read_model.COMMAND_CENTER_CHART_PERIODS ("today"/"shift"/"7d"/
    "30d") -- affects only the charts section; the top KPI cards always
    stay "right now"/"today" regardless."""
    chart_period = body.get("chart_period") or "today"
    conn = get_conn()
    try:
        try:
            dashboard = kpi_read_model.get_command_center(conn, chart_period=chart_period)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        remaining = 5 - len(dashboard["attention_now"])
        if remaining > 0:
            for observation in whatsapp_bridge_adapter.list_pending()[:remaining]:
                dashboard["attention_now"].append({
                    "type": "whatsapp_observation_pending",
                    "job_id": None,
                    "ticket": None,
                    "source": "WhatsApp",
                    "age_minutes": None,
                    "primary_action": "Review in Active",
                    "message_id": observation["message_id"],
                    "group_name": observation["group_name"],
                })
        return dashboard
    finally:
        conn.close()


# =========================================================== LEARNING REVIEW
_REVIEW_STATUSES = ("VERIFIED", "CORRECTED", "EXCLUDE", "SENSITIVE", "INCOMPLETE")


def _learning_episode_dto(episode: dict) -> dict:
    commercial_update = episode.get("commercial_update") or {}
    return {
        "episode_id": episode["episode_id"],
        "job_id": episode.get("job_id"),
        "source": episode.get("source"),
        "ticket_or_pdl": episode.get("ticket_or_pdl"),
        "normalized_intent": episode.get("normalized_intent"),
        "original_observation": commercial_update.get("raw_technician_text"),
        "shadow_suggested_reply": episode.get("shadow_suggested_reply"),
        "dispatcher_actual_reply": episode.get("dispatcher_actual_reply"),
        "customer_response": episode.get("customer_response"),
        "negotiation_outcome": episode.get("negotiation_outcome"),
        "final_job_outcome": episode.get("final_job_outcome"),
        "human_review_required": bool(episode.get("human_review_required")),
        "review_status": episode.get("review_status") or "UNREVIEWED",
        "created_at": episode.get("created_at"),
        "reviewed_at": episode.get("reviewed_at"),
    }


def route_learning_review(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        episodes = storage.list_shadow_episodes(conn, limit=200)
        dtos = [_learning_episode_dto(e) for e in episodes]
        reviewed_count = sum(1 for d in dtos if d["review_status"] != "UNREVIEWED")
        return {"episodes": dtos, "reviewed_count": reviewed_count, "total_count": len(dtos)}
    finally:
        conn.close()


def route_learning_review_status(handler: "Handler", match: dict, body: dict) -> dict:
    episode_id = match["episode_id"]
    review_status = _require(body, "review_status")
    if review_status not in _REVIEW_STATUSES:
        raise ApiError(400, f"review_status must be one of {_REVIEW_STATUSES}")
    conn = get_conn()
    try:
        service = _service(conn)
        result = service.set_shadow_review(episode_id, review_status, reviewed_by=DISPATCHER_ACTOR)
        if not result.success:
            raise ApiError(400, result.user_message or "Could not update review status.")
        return _action_result_dto(result)
    finally:
        conn.close()


# =================================================== WHATSAPP SHADOW BRIDGE
# Read-only from this app's perspective: the bridge (a separate Node.js
# process) POSTs captured observations to whatsapp_bridge_adapter's own
# loopback-only listener (a different port, never proxied through the
# tunnel); this module only ever reads that in-memory queue and lets a
# human decide what happens next. Attaching an observation never calls
# DispatcherService itself -- it only hands the raw text back to the
# frontend so the EXISTING, already-audited commercial-update flow
# (route_commercial_save above, unchanged) is what actually records
# anything. No route here can send a WhatsApp message; nothing in this
# module has ever opened a real WhatsApp session (whatsapp_bridge_adapter.py
# never imports whatsapp-web.js -- it doesn't even run in this process).

def route_whatsapp_pending(handler: "Handler", match: dict, body: dict) -> dict:
    return {"pending": whatsapp_bridge_adapter.list_pending()}


def route_whatsapp_bridge_status(handler: "Handler", match: dict, body: dict) -> dict:
    """Never polls the bridge over the network (this app has no
    outbound HTTP capability -- see test_operator_api_makes_no_outbound_network_calls).
    Status is derived only from observations the bridge has actually
    sent us -- so this can never claim "connected" before a real,
    supervised pairing and incoming message have actually happened."""
    ever = whatsapp_bridge_adapter.list_pending(include_resolved=True)
    if ever:
        return {"paired": True, "status": "Incoming WhatsApp Shadow Bridge — receiving messages.", "observations_received": len(ever)}
    return {"paired": False, "status": "Experimental incoming WhatsApp Shadow Bridge — not paired yet.", "observations_received": 0}


def route_whatsapp_attach(handler: "Handler", match: dict, body: dict) -> dict:
    message_id = match["message_id"]
    job_id = _require(body, "job_id")
    observation = whatsapp_bridge_adapter.get_observation(message_id)
    if observation is None:
        raise ApiError(404, "No such pending WhatsApp observation.")
    whatsapp_bridge_adapter.resolve_observation(message_id, "attached", job_id=int(job_id))
    return {"message_id": message_id, "job_id": int(job_id), "text": observation["text"]}


def route_whatsapp_ignore(handler: "Handler", match: dict, body: dict) -> dict:
    message_id = match["message_id"]
    observation = whatsapp_bridge_adapter.resolve_observation(message_id, "ignored")
    if observation is None:
        raise ApiError(404, "No such pending WhatsApp observation.")
    return {"success": True}


# ------------------------------------------- WHATSAPP INTERPRETATION LAYER
# Read-only enrichment of whatsapp_pending_observations for CONFIRMED
# registry groups, reusing the exact same business logic every other
# screen uses (extract_job/classify_incoming for intake,
# dispatcher.technician_ack's EXACT/STRONG/AMBIGUOUS/UNMATCHED vocabulary
# for technician-group correlation). Never writes to quo_pending_messages
# -- WhatsApp stays a separate transport. Never auto-creates a job, never
# auto-attaches: every result here is a SUGGESTION surfaced for a human
# to confirm via the existing route_whatsapp_attach (technician messages)
# or the new route_whatsapp_promote_to_job (company intake) below.

def _registry_row_for_group(conn, group_id: str) -> Optional[dict]:
    return storage.get_group_registry_entry(conn, group_id)


def _resolve_registry_technician(conn, registry_row: dict) -> Optional[dict]:
    """Prefers a real technicians.id (confirmed via DB dispatch_group/
    closing_group match, e.g. Sebai/Ashraf/Kabaka) over the unconfirmed
    free-text name (e.g. Toto/Joe, not yet in the technicians table)."""
    tech_id = registry_row.get("technician_id")
    if tech_id:
        row = storage.get_technician(conn, tech_id)
        if row:
            return {"id": row["id"], "name": row["name"], "dispatch_group": row.get("dispatch_group"), "closing_group": row.get("closing_group")}
    name = registry_row.get("technician_name_unconfirmed")
    if name:
        return {"id": None, "name": name, "dispatch_group": None, "closing_group": None}
    return None


_SUGGEST_EXACT, _SUGGEST_STRONG, _SUGGEST_AMBIGUOUS, _SUGGEST_UNMATCHED = (
    technician_ack.CATEGORY_EXACT, technician_ack.CATEGORY_STRONG,
    technician_ack.CATEGORY_AMBIGUOUS, technician_ack.CATEGORY_UNMATCHED,
)


def _suggest_job_for_technician(all_jobs: list, technician: Optional[dict], extracted_ticket: Optional[str]) -> dict:
    """Generalization of technician_ack.classify_candidate's category
    vocabulary to ANY technician-group message (K, ETA, update, closing),
    not just acknowledgement-shaped text -- classify_candidate itself
    only fires for exact "k"/"ok"/decline-vocabulary text, which is too
    narrow for "which job is this ETA/closing about". Same four
    categories, same meaning: EXACT (ticket named), STRONG (exactly one
    open job for this technician), AMBIGUOUS (more than one, no ticket
    named), UNMATCHED (none, or no technician resolved)."""
    if not technician or not technician.get("name"):
        return {"job": None, "category": _SUGGEST_UNMATCHED, "reasons": ["no technician resolved for this group"]}
    open_jobs = [
        j for j in all_jobs
        if j.get("assigned_technician_name") == technician["name"] and j.get("status") in shared.ACTIVE_STATUSES
    ]
    ticket = (extracted_ticket or "").strip().upper()
    if ticket:
        exact = [j for j in open_jobs if (j.get("ticket_number") or "").strip().upper() == ticket]
        if exact:
            return {"job": exact[0], "category": _SUGGEST_EXACT, "reasons": [f"message references ticket {ticket}"]}
    if len(open_jobs) == 1:
        return {"job": open_jobs[0], "category": _SUGGEST_STRONG, "reasons": [f"{technician['name']} has exactly one open job"]}
    if len(open_jobs) > 1:
        return {"job": None, "category": _SUGGEST_AMBIGUOUS, "reasons": [f"{technician['name']} has {len(open_jobs)} open jobs, message names no ticket"]}
    return {"job": None, "category": _SUGGEST_UNMATCHED, "reasons": [f"{technician['name']} has no open job"]}


_ROLE_LABELS = {
    "COMPANY_JOB_INTAKE": "COMPANY JOB INTAKE", "COMPANY_CLOSINGS": "COMPANY CLOSINGS",
    "TECH_DISPATCH": "TECH DISPATCH", "TECH_CLOSINGS": "TECH CLOSINGS",
    "UNMAPPED_WORK": "UNMAPPED WORK", "IGNORE": "IGNORE",
}

# A dollar amount immediately followed by a known payment-method word --
# confirmed as a real, repeated closing-report pattern by checking actual
# live TECH_CLOSINGS traffic (5 of 8 real closing messages contained this
# exact adjacent shape: "$200 cash abozeid", "$720 cash", "$379.50 cc
# joy", "$415 venmo to hazem"). Deliberately narrow: requires the amount
# and method to be directly adjacent, so it does NOT match a dollar
# figure mentioned elsewhere in the message with no payment word next to
# it (e.g. a real observed false-positive risk: "Da keda additional to
# the $150", which is not a closing report at all) and does NOT attempt
# to interpret the more complex multi-line Fees/Parts/Total breakdown
# format seen in 2 other real messages -- there wasn't enough repeated,
# unambiguous evidence for that shape to build a rule from it yet.
_CLOSING_AMOUNT_RE = re.compile(
    r"\$\s*([\d,]+\.?\d*)\s+(cash|cc|card|venmo|zelle|check)\b", re.IGNORECASE,
)


# A technician reporting the CUSTOMER could not be reached. Confirmed as
# a real, repeated, cross-group pattern before being added: 6 genuine
# standalone instances across 4 different confirmed/unmapped dispatch
# groups and 4 different technicians ("No answer will try to call again",
# "Cx doesn't answer, voice mail", "Its going to voicemail left a
# voicemail and texted them", "No answer sent a text", ...). Operationally
# distinct from every existing category: it is NOT a decline (the
# technician isn't refusing the job), NOT an ETA, and NOT an ordinary
# update -- it means the job is stalling and the company needs telling.
_NO_ANSWER_RE = re.compile(
    r"(no answer|didn'?t answer|doesn'?t answer|not answering|no reply|voice ?mail)", re.IGNORECASE,
)

# All deterministic message patterns now live in one place --
# dispatcher/message_patterns.py -- because the production interpreter
# and the owner demo had each grown their own copy of the closing/ETA/
# en-route rules, which meant the demo could drift from the behaviour it
# exists to demonstrate. Each pattern's measurement and the false
# positive that shaped it are documented there.
_MENTION_ONLY_RE = message_patterns.MENTION_ONLY_RE
_ETA_TIME_RE = message_patterns.ETA_TIME_RE
_EN_ROUTE_RE = message_patterns.EN_ROUTE_RE
_QUOTE_RE = message_patterns.QUOTE_RE
_NO_ANSWER_RE = message_patterns.NO_ANSWER_RE
_PASS_PREFIX_RE = message_patterns.PASS_PREFIX_RE
_CLOSING_AMOUNT_RE = message_patterns.CLOSING_AMOUNT_RE


def _extract_closing_amount_and_method(text: str):
    """Delegates to the shared implementation so production and the demo
    can never disagree about what counts as a closing."""
    return message_patterns.extract_closing_amount(text)


def _interpret_whatsapp_observation(conn, obs: dict, all_jobs: list, vehicle_rules) -> dict:
    role = obs.get("group_role") or "UNMAPPED_WORK"
    confirmed = bool(obs.get("group_mapping_confirmed"))
    base = {
        "role": role, "role_label": _ROLE_LABELS.get(role, role),
        "mapping_status": "CONFIRMED" if confirmed else "HUMAN_REVIEW",
        "company": obs.get("group_company"), "technician": obs.get("group_technician_name"),
        "service_area": obs.get("group_service_area"),
        "interpretation": None, "proposed_job_id": None, "proposed_ticket": None,
        "confidence": None, "missing_fields": [], "next_action": "HUMAN_REVIEW_NEEDED",
    }
    if not confirmed:
        base["next_action"] = "GROUP_MAPPING_REQUIRED" if role == "UNMAPPED_WORK" else "HUMAN_REVIEW_NEEDED"
        base["interpretation"] = "Unmapped/unconfirmed group -- no interpretation attempted."
        return base

    if role == "COMPANY_JOB_INTAKE":
        # A bare acknowledgement in a company intake group is the
        # dispatcher acknowledging the company's job -- an actual step of
        # the workflow, not a job posting that failed to parse. Without
        # this it fell through to the extractor below and was reported as
        # "INSUFFICIENT DATA" with ticket/phone/address/service all
        # "missing", which reads like a broken job and buries the real
        # ones in noise. Confirmed against live traffic: every short
        # reply observed in confirmed intake groups came from the same
        # sender that also posts across 11 other groups (dispatcher-
        # shaped), and three were a bare "K".
        #
        # Reuses technician_ack.ACKNOWLEDGEMENT_TEXTS -- the same explicit,
        # never-inferred-beyond-this vocabulary the technician side uses,
        # rather than inventing a second list. Deliberately does NOT
        # assert WHO sent it: sender identity is not owner-confirmed, so
        # the label describes the message, not the person.
        if (obs.get("text") or "").strip().lower() in technician_ack.ACKNOWLEDGEMENT_TEXTS:
            base.update({
                "interpretation": "ACKNOWLEDGEMENT -- not a job posting",
                "confidence": "OK",
                "missing_fields": [],
                "next_action": "REVIEW -- ACKNOWLEDGEMENT ONLY",
            })
            return base

        job = extract_job(obs["text"], vehicle_rules)
        classify_result = storage.classify_incoming(conn, job)
        core = {"ticket": job.ticket_number.value, "phone": job.phone.value, "address": job.address.value, "service": job.service_type.value}
        missing = [k for k, v in core.items() if not v]
        needs_review = bool(missing) or bool(classify_result.conflicting_fields)
        # A message correlating to an ALREADY-existing job (correction/
        # duplicate/status update/closing text arriving in an intake group)
        # must never be labeled "NEW JOB DETECTED" -- that's misleading even
        # though the underlying promote action (confirm_whatsapp_dispatch ->
        # save_audit_entry) already correctly attaches to the existing job
        # rather than creating a second one. Label reflects what
        # classify_incoming() actually determined, not an assumption.
        if classify_result.existing_job_id is not None:
            interpretation = f"{classify_result.message_type.value} -- CORRELATES TO JOB #{classify_result.existing_job_id}"
            next_action = "REVIEW" if needs_review else f"REVIEW -- MATCHES JOB #{classify_result.existing_job_id}"
        elif job.ticket_number.value:
            interpretation = "NEW JOB DETECTED"
            next_action = "REVIEW" if needs_review else "REVIEW -- READY TO PROMOTE"
        else:
            interpretation = "MESSAGE RECEIVED -- INSUFFICIENT DATA"
            next_action = "REVIEW"
        base.update({
            "interpretation": interpretation,
            "proposed_ticket": job.ticket_number.value, "missing_fields": missing,
            "confidence": "LOW" if needs_review else "OK",
            "next_action": next_action,
            "existing_job_id": classify_result.existing_job_id,
        })
        return base

    if role == "TECH_DISPATCH":
        # A message in a technician's dispatch group is usually a short
        # reply (K / decline / ETA) -- but it can also be the dispatcher
        # forwarding the full job posting itself (same "Ticket:.../Name:.../
        # Phone:..." block shape company-intake groups use, just relayed
        # into the technician's group). Confirmed as a real, repeated
        # pattern by checking actual live traffic (17 real observations
        # across 3 different confirmed dispatch groups/companies), not
        # built from a single example. Checked first and returns early --
        # a ticket block's digits/labels must never fall through to the
        # ETA-or-update text matching below, which would misread it as a
        # technician status report instead of what it actually is: the
        # SEND_JOB_TO_TECHNICIAN step of the workflow.
        forwarded_job = extract_job(obs.get("text") or "", vehicle_rules)
        if forwarded_job.ticket_number.value and (forwarded_job.phone.value or forwarded_job.address.value):
            matched_job = next((j for j in all_jobs if j.get("ticket_number") == forwarded_job.ticket_number.value), None)
            if matched_job:
                interpretation = f"JOB FORWARDED TO TECHNICIAN -- CORRELATES TO JOB #{matched_job['id']}"
                next_action = f"REVIEW -- MATCHES JOB #{matched_job['id']}"
            else:
                interpretation = f"JOB FORWARDED TO TECHNICIAN -- TICKET {forwarded_job.ticket_number.value} (no matching job on record)"
                next_action = "REVIEW"
            base.update({
                "interpretation": interpretation,
                "proposed_job_id": matched_job["id"] if matched_job else None,
                "proposed_ticket": forwarded_job.ticket_number.value,
                "confidence": "OK",
                "next_action": next_action,
            })
            return base

    if role in ("TECH_DISPATCH", "TECH_CLOSINGS"):
        registry_row = storage.get_group_registry_entry(conn, obs["group_id"])
        technician = _resolve_registry_technician(conn, registry_row) if registry_row else None
        suggestion = _suggest_job_for_technician(all_jobs, technician, obs.get("extracted_ticket"))
        job = suggestion["job"]
        text_lower = (obs.get("text") or "").strip().lower()
        if role == "TECH_DISPATCH":
            raw_text = (obs.get("text") or "").strip()
            if text_lower in technician_ack.ACKNOWLEDGEMENT_TEXTS:
                interp = "TECHNICIAN K"
            elif text_lower in technician_ack.DECLINE_TEXTS:
                interp = "TECHNICIAN DECLINE"
            elif _NO_ANSWER_RE.search(raw_text):
                # Checked before the digit heuristic below: a real message
                # like "Pass he's too far now not worth them not answering
                # after I drive 1 hour" contains a digit and would
                # otherwise be misfiled as an ETA.
                interp = (
                    "TECHNICIAN DECLINE -- CUSTOMER NO ANSWER" if _PASS_PREFIX_RE.search(raw_text)
                    else "TECHNICIAN NO ANSWER -- CUSTOMER UNREACHABLE"
                )
            elif _MENTION_ONLY_RE.match(raw_text):
                interp = "MENTION ONLY -- no status content"
            else:
                # Replaces "any digit means ETA". See each pattern's own
                # comment for the live measurement that motivated it.
                has_time = bool(_ETA_TIME_RE.search(raw_text))
                has_quote = bool(_QUOTE_RE.search(raw_text))
                if has_time and has_quote:
                    # Real observed shape: "40 minutes away, quoted 195$".
                    # Both facts matter, so neither is dropped.
                    interp = "TECHNICIAN ETA + PRICE QUOTE"
                elif has_quote:
                    interp = "TECHNICIAN PRICE QUOTE"
                elif has_time:
                    interp = "TECHNICIAN ETA/UPDATE"
                elif _EN_ROUTE_RE.search(raw_text):
                    interp = "TECHNICIAN EN ROUTE"
                else:
                    interp = "TECHNICIAN UPDATE"
            # Distinct, unambiguous next_action per interpretation -- a decline
            # must never read like an ordinary match-confirmation prompt (a
            # dispatcher skimming this must not mistake "the technician said
            # no" for "just confirm and move on"). UPDATE_COMPANY reuses the
            # existing workflow_projection.NEXT_ACTIONS vocabulary rather than
            # inventing a parallel phrase for the same real-world step.
            if interp == "TECHNICIAN K" and job:
                next_action = "CONFIRM TECHNICIAN ACCEPTED"
            elif interp.startswith("TECHNICIAN DECLINE") and job:
                next_action = "TECHNICIAN DECLINED -- REASSIGN"
            elif interp.startswith("TECHNICIAN NO ANSWER") and job:
                next_action = "UPDATE_COMPANY"
            elif interp.startswith("MENTION ONLY"):
                # Carries no status content, so there is nothing for a
                # dispatcher to relay -- never dressed up as actionable.
                next_action = "REVIEW -- MENTION ONLY"
            elif "PRICE QUOTE" in interp and job:
                # A quoted price is a commercial fact; flag it as such
                # rather than folding it into a generic company update.
                next_action = "REVIEW -- PRICE QUOTED TO CUSTOMER"
            elif interp in ("TECHNICIAN ETA/UPDATE", "TECHNICIAN UPDATE", "TECHNICIAN EN ROUTE") and job:
                next_action = "UPDATE_COMPANY"
            else:
                next_action = "CONFIRM MATCH / REVIEW"
        else:
            closing_amount, closing_payment_method = _extract_closing_amount_and_method(obs.get("text"))
            interp = f"CLOSING RECEIVED -- ${closing_amount} {closing_payment_method}" if closing_amount else "CLOSING RECEIVED"
            next_action = "REVIEW CLOSING"
        base.update({
            "interpretation": interp,
            "technician": technician["name"] if technician else base["technician"],
            "proposed_job_id": job["id"] if job else None,
            "proposed_ticket": job["ticket_number"] if job else None,
            "confidence": suggestion["category"],
            # Only a field this message's text genuinely lacks counts as
            # "missing" -- see _extract_closing_amount_and_method's own
            # comment for exactly which real-world shape this recognizes
            # and which it deliberately doesn't (never invents a value
            # for the shapes it doesn't recognize; those still show as
            # missing, same as before this fix).
            "missing_fields": (
                ([] if closing_amount else ["amount"]) + ([] if closing_payment_method else ["payment_method"])
            ) if role == "TECH_CLOSINGS" else [],
            # _suggest_job_for_technician only ever returns a job for
            # EXACT/STRONG -- AMBIGUOUS and UNMATCHED both return job=None,
            # and both genuinely need a human to pick/find the right job,
            # so both get the same HUMAN_REVIEW_NEEDED next_action rather
            # than the misleadingly specific match-confirmation phrasing.
            "next_action": next_action if job else "HUMAN_REVIEW_NEEDED",
        })
        if role == "TECH_CLOSINGS":
            base["closing_amount"] = closing_amount
            base["closing_payment_method"] = closing_payment_method
        return base

    if role == "COMPANY_CLOSINGS":
        base.update({
            "interpretation": "COMPANY CLOSING DESTINATION",
            "next_action": "DESTINATION CONTEXT ONLY -- no job created from this group",
        })
        return base

    return base


def route_whatsapp_shadow_feed(handler: "Handler", match: dict, body: dict) -> dict:
    """The live-visibility surface Task 3 asks for: every recent pending
    WhatsApp observation, enriched with role/mapping-status/interpretation/
    proposed-job/confidence/next-action. Pure read -- computes nothing new
    into storage, mutates nothing."""
    conn = get_conn()
    try:
        observations = storage.list_whatsapp_pending_observations(conn, include_resolved=False)
        observations.sort(key=lambda o: o.get("received_at") or "", reverse=True)
        observations = observations[:100]
        all_jobs = shared.fetch_jobs(conn)
        vehicle_rules = shared.get_vehicle_rules()
        items = []
        for obs in observations:
            interp = _interpret_whatsapp_observation(conn, obs, all_jobs, vehicle_rules)
            items.append({
                "message_id": obs["message_id"], "time": obs.get("received_at"),
                "group_id": obs["group_id"], "group_name": obs["group_name"],
                "text_preview": shared.mask_sensitive(obs["text"])[:280],
                **interp,
            })
        confirmed_count = sum(1 for i in items if i["mapping_status"] == "CONFIRMED")
        human_review_count = len(items) - confirmed_count
        return {"items": items, "total": len(items), "confirmed_count": confirmed_count, "human_review_count": human_review_count}
    finally:
        conn.close()


def route_whatsapp_promote_to_job(handler: "Handler", match: dict, body: dict) -> dict:
    """Human-triggered "PROMOTE TO JOB" for a COMPANY_JOB_INTAKE candidate.
    Reuses the exact existing job-commit path
    (DispatcherService.confirm_whatsapp_dispatch -> storage.save_audit_entry)
    Quo-sourced dispatches already use -- NOT a second job-creation system.
    The WhatsApp observation's own message_id becomes the idempotency-key
    basis, so the existing compute_idempotency_key/get_event_by_idempotency_key
    guarantee (already used for every other confirm action) makes
    "same message_id never promotes twice" true for free."""
    message_id = match["message_id"]
    observation = whatsapp_bridge_adapter.get_observation(message_id)
    if observation is None:
        raise ApiError(404, "No such pending WhatsApp observation.")
    registry_row = None
    conn = get_conn()
    try:
        registry_row = storage.get_group_registry_entry(conn, observation["group_id"])
        if not registry_row or not registry_row["confirmed_by_owner"] or registry_row["role"] != "COMPANY_JOB_INTAKE":
            raise ApiError(400, "This observation's group is not a confirmed COMPANY_JOB_INTAKE group.")
        vehicle_rules = shared.get_vehicle_rules()
        job = extract_job(observation["text"], vehicle_rules)
        classify_result = storage.classify_incoming(conn, job)
        if not job.ticket_number.value:
            raise ApiError(400, "Cannot promote: no ticket/reference number could be extracted from this message.")
        preview = pipeline.process_raw_message(observation["text"], vehicle_rules, shared.get_service_areas(), conn, None)
        service = _service(conn)
        result = service.confirm_whatsapp_dispatch(
            f"whatsapp:{message_id}", job, classify_result, preview.luxor_message,
        )
        whatsapp_bridge_adapter.resolve_observation(message_id, "promoted", job_id=result.job_id)
        shared.clear_pending_caches()
        return _action_result_dto(result)
    finally:
        conn.close()


ROUTES = [
    ("POST", r"^/api/login$", route_login, {"public": True, "skip_csrf": True}),
    ("POST", r"^/api/logout$", route_logout, {}),
    ("GET", r"^/api/me$", route_me, {}),
    ("GET", r"^/api/demo_status$", route_demo_status, {"public": True}),
    ("GET", r"^/api/command_center$", route_command_center, {}),
    ("GET", r"^/api/action_center$", route_action_center, {}),
    ("GET", r"^/api/next_actionable_job$", route_next_actionable_job, {}),
    ("GET", r"^/api/search$", route_search, {}),
    ("GET", r"^/api/inbox_review$", route_inbox_review, {}),
    ("GET", r"^/api/new_jobs$", route_new_jobs, {}),
    ("POST", r"^/api/sync$", route_sync, {}),
    ("GET", r"^/api/active$", route_active, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/technician_update$", route_technician_update_record, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/company_update/prepare$", route_company_update_prepare, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/company_update/confirm_sent$", route_company_update_confirm_sent, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/technician_acceptance/confirm_accepted$", route_technician_k_confirm_accepted, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/technician_acceptance/confirm_declined$", route_technician_k_confirm_declined, {}),
    ("GET", r"^/api/technicians$", route_technicians, {}),
    ("POST", r"^/api/technicians/(?P<id>\d+)/destinations$", route_update_destinations, {}),
    ("GET", r"^/api/job/(?P<id>\d+)$", route_job_workspace, {}),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/acknowledge$", route_acknowledge, {}),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/prepare$", route_prepare_dispatch, {}),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/confirm$", route_confirm_dispatch, {}),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/handle_manually$", route_handle_manually, {}),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/pass$", route_pass, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/commercial/preview$", route_commercial_preview, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/commercial/save$", route_commercial_save, {}),
    ("POST", r"^/api/job/(?P<id>\d+)/commercial/confirm_reply_sent$", route_commercial_confirm_reply_sent, {}),
    ("GET", r"^/api/closings$", route_closings, {}),
    ("POST", r"^/api/closings/(?P<id>\d+)/record$", route_closing_record, {}),
    ("POST", r"^/api/closings/(?P<id>\d+)/prepare$", route_closing_prepare, {}),
    ("POST", r"^/api/closings/(?P<id>\d+)/confirm_sent$", route_closing_confirm_sent, {}),
    ("POST", r"^/api/closings/(?P<id>\d+)/complete$", route_closing_complete, {}),
    ("GET", r"^/api/history$", route_history, {}),
    ("GET", r"^/api/history/(?P<id>\d+)$", route_history_detail, {}),
    ("GET", r"^/api/reports$", route_reports, {}),
    ("GET", r"^/api/performance$", route_performance, {}),
    ("GET", r"^/api/learning_review$", route_learning_review, {}),
    ("POST", r"^/api/learning_review/(?P<episode_id>[^/]+)/status$", route_learning_review_status, {}),
    ("GET", r"^/api/whatsapp_pending$", route_whatsapp_pending, {}),
    ("GET", r"^/api/whatsapp_bridge_status$", route_whatsapp_bridge_status, {}),
    ("POST", r"^/api/whatsapp_pending/(?P<message_id>[^/]+)/attach$", route_whatsapp_attach, {}),
    ("POST", r"^/api/whatsapp_pending/(?P<message_id>[^/]+)/ignore$", route_whatsapp_ignore, {}),
    ("GET", r"^/api/whatsapp_shadow_feed$", route_whatsapp_shadow_feed, {}),
    ("POST", r"^/api/whatsapp_pending/(?P<message_id>[^/]+)/promote_to_job$", route_whatsapp_promote_to_job, {}),
]
_COMPILED_ROUTES = [(method, re.compile(pattern), fn, opts) for method, pattern, fn, opts in ROUTES]


SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    # Defense in depth, not a response to a found XSS -- every dynamic
    # value already goes through the frontend's esc() before reaching
    # innerHTML (verified by grep across every msg-block/detail-meta
    # render site in operator_ui_v2_connected/js/app.js). script-src
    # 'self' is safe because index.html has zero inline <script> tags
    # (both app.js and api.js are external files); style-src keeps
    # 'unsafe-inline' because the UI uses inline style="..." attributes
    # extensively and a full refactor to eliminate them is out of scope
    # here -- still blocks loading a remote stylesheet.
    ("Content-Security-Policy",
     "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
     "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
     "base-uri 'self'; form-action 'self'"),
)


class Handler(BaseHTTPRequestHandler):
    server_version = "OperatorAPI/1.0"

    def log_message(self, fmt, *args):  # quieter, never logs headers/cookies/body
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    @property
    def client_ip(self) -> str:
        return self.client_address[0]

    def _cookies(self) -> http_cookies.SimpleCookie:
        jar = http_cookies.SimpleCookie()
        raw = self.headers.get("Cookie")
        if raw:
            try:
                jar.load(raw)
            except http_cookies.CookieError:
                pass
        return jar

    def _session_token(self) -> Optional[str]:
        jar = self._cookies()
        morsel = jar.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    def _is_https(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def set_session_cookie(self, token: str) -> None:
        parts = [f"{SESSION_COOKIE_NAME}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={SESSION_TTL_SECONDS}"]
        if self._is_https():
            parts.append("Secure")
        self._pending_set_cookie = "; ".join(parts)

    def clear_session_cookie(self) -> None:
        parts = [f"{SESSION_COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if self._is_https():
            parts.append("Secure")
        self._pending_set_cookie = "; ".join(parts)

    def _require_auth(self) -> str:
        token = self._session_token()
        if not verify_session_token(token):
            raise ApiError(401, "Not authenticated.")
        return token

    def _require_csrf(self, session_token: str) -> None:
        supplied = self.headers.get("X-CSRF-Token")
        expected = csrf_token_for(session_token)
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise ApiError(403, "Missing or invalid CSRF token.")

    def _dispatch(self, method: str) -> None:
        self._pending_set_cookie = None
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self._serve_static(method)
            return
        for route_method, pattern, fn, opts in _COMPILED_ROUTES:
            if route_method != method:
                continue
            m = pattern.match(path)
            if not m:
                continue
            try:
                session_token = None
                if not opts.get("public"):
                    session_token = self._require_auth()
                if method == "POST" and not opts.get("skip_csrf"):
                    self._require_csrf(session_token)
                body = _read_json_body(self) if method == "POST" else _parse_query(self.path)
                result = fn(self, m.groupdict(), body)
                self._send_json(200, result)
            except ApiError as exc:
                self._send_json(exc.status, {"error": exc.message})
            except Exception:  # noqa: BLE001
                # Full detail goes to the server log only -- the client
                # response is deliberately generic. An unhandled
                # exception's str() can contain a file path, a raw SQL
                # fragment, or other internal detail never meant to
                # leave the process; ApiError (handled above) remains
                # the one sanctioned way to send a specific message to
                # the client.
                traceback.print_exc()
                self._send_json(500, {"error": "Internal error."})
            return
        self._send_json(404, {"error": f"No route for {method} {path}"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _send_common_headers(self) -> None:
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        if getattr(self, "_pending_set_cookie", None):
            self.send_header("Set-Cookie", self._pending_set_cookie)
            self._pending_set_cookie = None

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._send_common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, method: str) -> None:
        """Minimal, dependency-free static file server for
        operator_ui_v2_connected/ -- GET only, no directory listing, no
        traversal above STATIC_DIR. Unauthenticated by design: these
        files are pure code/CSS (the login screen itself lives here) and
        carry no customer data -- see the module docstring's "code is
        public, data is protected" split."""
        if method != "GET":
            self._send_json(405, {"error": "Method not allowed for static files."})
            return
        parsed = urlparse(self.path)
        rel = parsed.path.lstrip("/") or "index.html"
        if rel == "":
            rel = "index.html"
        candidate = (STATIC_DIR / rel).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_json(403, {"error": "Forbidden."})
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            self._send_json(404, {"error": "Not found."})
            return
        content_type = _guess_content_type(candidate)
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._send_common_headers()
        self.end_headers()
        self.wfile.write(data)


def _guess_content_type(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json",
        ".png": "image/png",
        ".svg": "image/svg+xml",
    }.get(path.suffix, "application/octet-stream")


def run(port: int = 8512, start_bridge_adapter: bool = True) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Operator API (connected) serving on http://localhost:{port}  (static: {STATIC_DIR})")

    bridge_server = None
    if start_bridge_adapter:
        whatsapp_bridge_adapter._get_or_create_adapter_secret(_env_file_path())
        bridge_server = whatsapp_bridge_adapter.run_bridge_adapter()
        bridge_thread = threading.Thread(target=bridge_server.serve_forever, daemon=True)
        bridge_thread.start()
        print(
            f"WhatsApp Shadow Bridge adapter listening on http://127.0.0.1:{bridge_server.server_address[1]} "
            "(loopback only -- never reachable through the public tunnel)."
        )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if bridge_server is not None:
            bridge_server.shutdown()
            bridge_server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8512)
    parser.add_argument("--no-bridge-adapter", action="store_true", help="Skip starting the WhatsApp bridge adapter listener.")
    args = parser.parse_args()
    run(args.port, start_bridge_adapter=not args.no_bridge_adapter)
