"""The narrow, localhost-only adapter between the Node.js WhatsApp
Shadow Bridge and the Python dispatcher. Runs as a SEPARATE bound
socket from operator_api.py's tunneled port -- 127.0.0.1 only, never
0.0.0.0, so it is structurally unreachable through the public
Cloudflare tunnel regardless of anything else.

Accepted observations are persisted to their own dedicated table
(whatsapp_pending_observations, storage.py) so a CRM restart never
loses one -- previously an in-memory list, lost on every restart. This
module still calls no DispatcherService write method and still never
writes to the jobs/events tables itself: attaching an observation to a
real job remains the existing, already-audited, human-driven
operator_api.route_whatsapp_attach flow (unchanged).

Every write path from Node.js stops here: the bridge can only ever
call POST /observation, and this handler's only response to that is to
durably store the observation -- there is no code path in this file
capable of sending anything back to WhatsApp or writing to jobs/events.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from dispatcher import storage
from dispatcher import db_safety

ADAPTER_SECRET_ENV_VAR = "WHATSAPP_BRIDGE_ADAPTER_SECRET"
ADAPTER_PORT_ENV_VAR = "WHATSAPP_BRIDGE_ADAPTER_PORT"
DEFAULT_ADAPTER_PORT = 8513

ALLOWED_EVENT_TYPES = {"incoming_message"}
MAX_BODY_BYTES = 20_000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120  # generous for a single low-volume bridge; still bounded

# Replies that carry no information the deterministic parser can act on
# -- never inferred as a real status, always routed to a human instead
# of silently dropped. Kept intentionally narrow (exact match only,
# case/whitespace-insensitive) so a real informative reply is never
# mistaken for a bare acknowledgement.
_ACKNOWLEDGEMENT_ONLY_TEXTS = {"k", "kk", "ok", "okay", "yes", "y", "👍"}

_lock = threading.Lock()
_request_timestamps: "list[float]" = []


class AdapterError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _db_path() -> Optional[Path]:
    return db_safety.resolve_db_path_from_env()


def _conn():
    return storage.connect(_db_path())


def _check_rate_limit() -> None:
    now = time.time()
    global _request_timestamps
    with _lock:
        _request_timestamps = [t for t in _request_timestamps if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(_request_timestamps) >= RATE_LIMIT_MAX_REQUESTS:
            raise AdapterError(429, "Too many requests.")
        _request_timestamps.append(now)


def validate_observation(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise AdapterError(400, "Body must be a JSON object.")
    event_type = payload.get("event_type")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise AdapterError(400, f"event_type must be one of {sorted(ALLOWED_EVENT_TYPES)}.")
    required = ("message_id", "group_id", "group_name", "text")
    for field in required:
        value = payload.get(field)
        if not value or not isinstance(value, str):
            raise AdapterError(400, f"{field!r} is required and must be a non-empty string.")
    # whatsapp-web.js's own Message.timestamp is a numeric Unix epoch
    # (seconds), not a string -- the real bridge has always sent a raw
    # JSON number here. The original implementation demanded a string,
    # which rejected every real message with a 400 while passing every
    # test (whose fixtures used a string timestamp that never matched
    # what the real bridge actually sends). Accept either representation
    # and normalize to a string for storage.
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (str, int, float)) or timestamp == "":
        raise AdapterError(400, "'timestamp' is required and must be a string or number.")
    if len(payload.get("text", "")) > 4000:
        raise AdapterError(400, "text exceeds maximum length.")
    quoted_message_id = payload.get("quoted_message_id")
    if quoted_message_id is not None and not isinstance(quoted_message_id, str):
        raise AdapterError(400, "'quoted_message_id' must be a string when present.")
    return {
        "message_id": payload["message_id"],
        "group_id": payload["group_id"],
        "group_name": payload["group_name"],
        "sender": payload.get("sender"),
        "timestamp": str(timestamp),
        "text": payload["text"],
        "extracted_ticket": payload.get("extracted_ticket"),
        "quoted_message_id": quoted_message_id,
    }


def _acknowledgement_only(text: str) -> bool:
    return text.strip().lower() in _ACKNOWLEDGEMENT_ONLY_TEXTS


def add_observation(payload: dict, conn=None) -> dict:
    """Durably stores one captured observation -- deduplicated by
    message_id at the storage layer (UNIQUE constraint), so a retried
    delivery of the same message_id is a safe no-op, never a second
    row. Never touches jobs/events; never matches or attaches to a job
    automatically. A bare acknowledgement (e.g. "k") is stored as-is and
    flagged human_review_needed rather than silently treated as a
    meaningful technician status -- no status is invented for it."""
    observation = validate_observation(payload)
    direction = "incoming"  # this endpoint's only legitimate caller only ever forwards incoming messages
    human_review_needed = False
    human_review_reason = None
    if _acknowledgement_only(observation["text"]):
        human_review_needed = True
        human_review_reason = "acknowledgement_only_reply"
    owns_conn = conn is None
    conn = conn or _conn()
    try:
        result = storage.add_whatsapp_observation(conn, {
            **observation,
            "direction": direction,
            "human_review_needed": human_review_needed,
            "human_review_reason": human_review_reason,
        })
        return result
    finally:
        if owns_conn:
            conn.close()


def list_pending(include_resolved: bool = False, conn=None) -> "list[dict]":
    owns_conn = conn is None
    conn = conn or _conn()
    try:
        return storage.list_whatsapp_pending_observations(conn, include_resolved=include_resolved)
    finally:
        if owns_conn:
            conn.close()


def resolve_observation(message_id: str, status: str, job_id: Optional[int] = None, conn=None) -> Optional[dict]:
    owns_conn = conn is None
    conn = conn or _conn()
    try:
        return storage.resolve_whatsapp_observation(conn, message_id, status, job_id=job_id)
    finally:
        if owns_conn:
            conn.close()


def get_observation(message_id: str, conn=None) -> Optional[dict]:
    owns_conn = conn is None
    conn = conn or _conn()
    try:
        return storage.get_whatsapp_observation(conn, message_id)
    finally:
        if owns_conn:
            conn.close()


def _get_or_create_adapter_secret(env_path) -> str:
    secret = os.environ.get(ADAPTER_SECRET_ENV_VAR)
    if secret:
        return secret
    import secrets as _secrets

    generated = _secrets.token_hex(32)
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\n{ADAPTER_SECRET_ENV_VAR}={generated}\n")
    os.environ[ADAPTER_SECRET_ENV_VAR] = generated
    print(
        f"Generated a new {ADAPTER_SECRET_ENV_VAR} and appended it to {env_path} (value not printed). "
        f"Copy the same value into whatsapp_shadow_bridge/.env before enabling the bridge."
    )
    return generated


class BridgeAdapterHandler(BaseHTTPRequestHandler):
    server_version = "WhatsAppBridgeAdapter/1.0"

    def log_message(self, fmt, *args):  # never logs headers/body/customer data
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/observation":
            self._send_json(404, {"error": "Not found"})
            return
        try:
            _check_rate_limit()
            secret = os.environ.get(ADAPTER_SECRET_ENV_VAR, "")
            supplied = self.headers.get("X-Bridge-Secret", "")
            if not secret or not hmac.compare_digest(supplied, secret):
                raise AdapterError(401, "Invalid or missing bridge secret.")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                raise AdapterError(400, "Invalid request body size.")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise AdapterError(400, f"Invalid JSON: {exc}") from exc
            # Durably stored (or a safe duplicate no-op) before a 2xx is
            # returned -- the bridge only marks a message as delivered
            # once this call returns success, so a DB-level failure here
            # surfaces as a non-2xx to the bridge, which then does not
            # mark the message as seen and will retry it.
            result = add_observation(payload)
            self._send_json(200, result)
        except AdapterError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": "Internal error."})


def _reset_state_for_tests() -> None:
    """Test-only: clears the in-memory rate-limit window. The pending
    queue itself now lives in SQLite -- tests that need a clean queue
    use a fresh throwaway database (see tests/test_whatsapp_bridge_adapter.py),
    same convention as every other durable table in this project. Never
    called from production code paths."""
    with _lock:
        _request_timestamps.clear()


def run_bridge_adapter(port: Optional[int] = None) -> ThreadingHTTPServer:
    """Starts the loopback-only adapter server. Caller is responsible
    for running serve_forever() on a background thread -- see
    operator_api.py's startup, which does exactly that."""
    # `port or default` would silently treat port=0 (the ephemeral/OS-
    # assigned port tests rely on) the same as "not provided", binding
    # the real production port instead -- 0 is falsy but a meaningful,
    # distinct value here, so it must be checked with `is not None`.
    bind_port = port if port is not None else int(os.environ.get(ADAPTER_PORT_ENV_VAR, DEFAULT_ADAPTER_PORT))
    server = ThreadingHTTPServer(("127.0.0.1", bind_port), BridgeAdapterHandler)
    return server
