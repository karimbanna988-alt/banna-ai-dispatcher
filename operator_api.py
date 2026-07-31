"""Connected Operator UI V2 -- local-only JSON API + static file server.

Zero-subscription by construction: stdlib http.server only (no Flask, no
new pip dependency), no outbound network call anywhere in this module,
no WhatsApp library, no browser automation. Every state-changing route
is a thin wrapper around an existing, already-audited
dispatcher.application.DispatcherService method or a pure read -- this
file owns no SQL and no business logic of its own; see
dispatcher/application.py and luxor_desk_read_model.py, which it reuses
exactly as luxor_desk.py already does.

WhatsApp itself is never touched programmatically: this server never
opens a socket to web.whatsapp.com, never drives a browser, never reads
a session cookie. "Open WhatsApp" is a purely client-side (JS) action;
this server only records that a human later confirmed a message was
sent -- the same manual-confirmation contract
adapters.ManualWhatsAppHandoffAdapter already implements.

Run: python operator_api.py [--port 8512] [--db path/to.db]
The default DB is the real one (dispatcher.storage.connect()'s default)
-- set OPERATOR_API_DB_PATH to point at a different file (tests always
do this, and always use a temporary path, never the real database).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

import luxor_desk_read_model as rm  # noqa: E402
import luxor_ops_shared as shared  # noqa: E402
from dispatcher import commercial, storage  # noqa: E402
from dispatcher.application import DispatcherService  # noqa: E402
from dispatcher.policy import get_policy  # noqa: E402

STATIC_DIR = REPO_ROOT / "operator_ui_v2_connected"
DISPATCHER_ACTOR = "dispatcher"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _db_path() -> Optional[Path]:
    override = os.environ.get("OPERATOR_API_DB_PATH")
    return Path(override) if override else None


def get_conn():
    return storage.connect(_db_path())


def _service(conn) -> DispatcherService:
    return DispatcherService(conn, queue_reader=rm.LuxorDeskQueueReader())


# --------------------------------------------------------------- Helpers --
def _age_minutes(age_seconds: float) -> int:
    return max(0, int(age_seconds // 60))


def _find_new_job_entry(conn, message_id: str):
    """Re-derives (job, classify_result, result, tracking) for one
    pending message by re-running the exact same live-queue computation
    the Jobs list itself used -- never a second parse/cache of the raw
    text. Raises ApiError(404) if the message is no longer live
    (already dispatched, or aged past the boundary in a way that
    removed it -- callers should refresh their list)."""
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

    return {
        "message_id": item["message_id"],
        "source": job.source.value,
        "ticket": job.ticket_number.value,
        "service": job.service_type.value,
        "city": job.city.value,
        "state": job.state.value,
        "age_minutes": _age_minutes(entry.age_seconds),
        "urgency": entry.urgency,
        "is_closings_stream": is_closings_stream,
        "needs_review": needs_review,
        "review_problems": review_problems,
        "k_required": k_required,
        "k_sent": tracking["acknowledged_at"] is not None,
        "copied": tracking["copied_at"] is not None,
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
    d = dataclasses.asdict(result)
    return d


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


def _require(body: dict, field: str) -> str:
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiError(400, f"{field!r} is required.")
    return value


# --------------------------------------------------------- Route handlers -
def route_new_jobs(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        live, _backlog, _other = rm.get_live_dispatch_queue(conn)
        return {"entries": [_new_job_dto(conn, e) for e in live]}
    finally:
        conn.close()


def route_active(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        jobs = rm.get_active_jobs_detailed(conn)
        for j in jobs:
            episode = storage.get_shadow_episode_for_job(conn, j["id"])
            j["shadow_episode"] = _episode_summary(episode) if episode else None
        return {"entries": jobs}
    finally:
        conn.close()


def route_technicians(handler: "Handler", match: dict, body: dict) -> dict:
    conn = get_conn()
    try:
        technicians = storage.list_technicians(conn, active_only=False)
        return {"technicians": [_technician_dto(t) for t in technicians]}
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


ROUTES = [
    ("GET", r"^/api/new_jobs$", route_new_jobs),
    ("GET", r"^/api/active$", route_active),
    ("GET", r"^/api/technicians$", route_technicians),
    ("POST", r"^/api/technicians/(?P<id>\d+)/destinations$", route_update_destinations),
    ("GET", r"^/api/job/(?P<id>\d+)$", route_job_workspace),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/acknowledge$", route_acknowledge),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/prepare$", route_prepare_dispatch),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/confirm$", route_confirm_dispatch),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/handle_manually$", route_handle_manually),
    ("POST", r"^/api/new_jobs/(?P<message_id>[^/]+)/pass$", route_pass),
    ("POST", r"^/api/job/(?P<id>\d+)/commercial/preview$", route_commercial_preview),
    ("POST", r"^/api/job/(?P<id>\d+)/commercial/save$", route_commercial_save),
    ("POST", r"^/api/job/(?P<id>\d+)/commercial/confirm_reply_sent$", route_commercial_confirm_reply_sent),
]

import re  # noqa: E402
_COMPILED_ROUTES = [(method, re.compile(pattern), fn) for method, pattern, fn in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "OperatorAPI/1.0"

    def log_message(self, fmt, *args):  # quieter, no request logging of raw bodies
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self._serve_static(method)
            return
        for route_method, pattern, fn in _COMPILED_ROUTES:
            if route_method != method:
                continue
            m = pattern.match(path)
            if not m:
                continue
            try:
                body = _read_json_body(self) if method == "POST" else {}
                result = fn(self, m.groupdict(), body)
                self._send_json(200, result)
            except ApiError as exc:
                self._send_json(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._send_json(500, {"error": f"Internal error: {exc}"})
            return
        self._send_json(404, {"error": f"No route for {method} {path}"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, method: str) -> None:
        """Minimal, dependency-free static file server for
        operator_ui_v2_connected/ -- GET only, no directory listing,
        no traversal above STATIC_DIR."""
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


def run(port: int = 8512) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Operator API (connected) serving on http://localhost:{port}  (static: {STATIC_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8512)
    args = parser.parse_args()
    run(args.port)
