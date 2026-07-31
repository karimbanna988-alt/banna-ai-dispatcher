"""The narrow, localhost-only adapter between the Node.js WhatsApp
Shadow Bridge and the Python dispatcher. Runs as a SEPARATE bound
socket from operator_api.py's tunneled port -- 127.0.0.1 only, never
0.0.0.0, so it is structurally unreachable through the public
Cloudflare tunnel regardless of anything else.

This module owns no SQL and calls no DispatcherService write method
itself. It only ever holds captured WhatsApp observations in memory
(never persisted to data/dispatcher.db) until a human, in the
authenticated connected UI, explicitly attaches one to a real job --
at which point the EXISTING, already-audited Active-screen commercial-
update flow (operator_api.route_commercial_save, unchanged by this
module) is what actually calls DispatcherService and enriches Shadow
Mode. Attaching only pre-fills that existing flow; it never bypasses it.

Every write path from Node.js stops here: the bridge can only ever
call POST /observation, and this handler's only response to that is to
append a plain dict to an in-memory list -- there is no code path in
this file capable of writing to the real database.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

ADAPTER_SECRET_ENV_VAR = "WHATSAPP_BRIDGE_ADAPTER_SECRET"
ADAPTER_PORT_ENV_VAR = "WHATSAPP_BRIDGE_ADAPTER_PORT"
DEFAULT_ADAPTER_PORT = 8513

ALLOWED_EVENT_TYPES = {"incoming_message"}
MAX_BODY_BYTES = 20_000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120  # generous for a single low-volume bridge; still bounded

_lock = threading.Lock()
_pending: "list[dict]" = []  # in-memory only, never written to data/dispatcher.db
_seen_message_ids: "set[str]" = set()
_request_timestamps: "list[float]" = []


class AdapterError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _check_rate_limit() -> None:
    now = time.time()
    global _request_timestamps
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
    required = ("message_id", "group_id", "group_name", "text", "timestamp")
    for field in required:
        value = payload.get(field)
        if not value or not isinstance(value, str):
            raise AdapterError(400, f"{field!r} is required and must be a non-empty string.")
    if len(payload.get("text", "")) > 4000:
        raise AdapterError(400, "text exceeds maximum length.")
    return {
        "message_id": payload["message_id"],
        "group_id": payload["group_id"],
        "group_name": payload["group_name"],
        "sender": payload.get("sender"),
        "timestamp": payload["timestamp"],
        "text": payload["text"],
        "extracted_ticket": payload.get("extracted_ticket"),
    }


def add_observation(payload: dict) -> dict:
    """Deduplicates by message_id, stamps a received_at/status, and
    appends to the in-memory pending queue. Never touches the main
    database; never matches or attaches to a job automatically."""
    observation = validate_observation(payload)
    with _lock:
        if observation["message_id"] in _seen_message_ids:
            return {"accepted": True, "duplicate": True}
        _seen_message_ids.add(observation["message_id"])
        observation["received_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        observation["status"] = "pending"
        _pending.append(observation)
    return {"accepted": True, "duplicate": False}


def list_pending(include_resolved: bool = False) -> "list[dict]":
    with _lock:
        if include_resolved:
            return list(_pending)
        return [o for o in _pending if o["status"] == "pending"]


def resolve_observation(message_id: str, status: str) -> Optional[dict]:
    with _lock:
        for o in _pending:
            if o["message_id"] == message_id:
                o["status"] = status
                return dict(o)
    return None


def get_observation(message_id: str) -> Optional[dict]:
    with _lock:
        for o in _pending:
            if o["message_id"] == message_id:
                return dict(o)
    return None


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
            result = add_observation(payload)
            self._send_json(200, result)
        except AdapterError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": "Internal error."})


def _reset_state_for_tests() -> None:
    """Test-only: clears the in-memory pending queue, seen-id set, and
    rate-limit window. Never called from production code paths."""
    with _lock:
        _pending.clear()
        _seen_message_ids.clear()
        _request_timestamps.clear()


def run_bridge_adapter(port: Optional[int] = None) -> ThreadingHTTPServer:
    """Starts the loopback-only adapter server. Caller is responsible
    for running serve_forever() on a background thread -- see
    operator_api.py's startup, which does exactly that."""
    bind_port = port or int(os.environ.get(ADAPTER_PORT_ENV_VAR, DEFAULT_ADAPTER_PORT))
    server = ThreadingHTTPServer(("127.0.0.1", bind_port), BridgeAdapterHandler)
    return server
