"""Read-only Quo (formerly OpenPhone) API client.

Deliberately contains no method capable of POST/PUT/PATCH/DELETE to Quo --
only GET. This is not a convention to be careful about; the capability to
send a message or modify anything on the Quo account simply does not exist
in this module. tests/test_quo_client.py enforces this structurally by
grepping this file's own source for those verbs.

Base URL, auth header shape, and field names below are taken from Quo's
published API docs (https://www.quo.com/docs, fetched 2026-07-27). Two
things the docs don't state -- the exact `direction` value for an inbound
message, and the precise JSON envelope shape -- are isolated into the
_unwrap_list() helper below so a one-time confirmation against a real
response (see scripts/quo_probe_readonly.py) only requires editing one
spot, not the whole module.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

import requests

BASE_URL = "https://api.quo.com/v1"
API_KEY_ENV_VAR = "QUO_API_KEY"

# Client-side throttle: Quo's documented limit is 10 requests/second/key.
_MIN_SECONDS_BETWEEN_REQUESTS = 0.11
_MAX_429_RETRIES = 5
_INITIAL_BACKOFF_SECONDS = 1.0


class QuoConfigError(RuntimeError):
    """QUO_API_KEY is missing or empty."""


class QuoAPIError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class QuoRateLimitError(QuoAPIError):
    """Exhausted retries against repeated 429 responses."""


@dataclass(frozen=True)
class Conversation:
    id: str
    phone_number_id: Optional[str]
    participants: List[str]
    last_activity_at: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    name: Optional[str]


@dataclass(frozen=True)
class QuoMessage:
    id: str
    to: Optional[str]
    from_number: Optional[str]
    text: Optional[str]
    phone_number_id: Optional[str]
    conversation_id: Optional[str]
    direction: Optional[str]
    status: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


@dataclass(frozen=True)
class Contact:
    id: str
    first_name: Optional[str]
    last_name: Optional[str]
    company: Optional[str]
    phone_numbers: List[str]

    @property
    def label(self) -> str:
        parts = [self.first_name, self.last_name, self.company]
        return " ".join(p for p in parts if p)


def _unwrap_list(body: Any) -> (List[dict], Optional[str]):
    """Isolates the "what does the envelope actually look like" question to
    one place. Confirmed 2026-07-27 against real responses:
    {"data": [...], "nextPageToken": ..., "totalItems": ...}."""
    if isinstance(body, list):
        return body, None
    if isinstance(body, dict):
        items = body.get("data", [])
        next_token = body.get("nextPageToken")
        return items, next_token
    raise QuoAPIError(f"Unrecognized response envelope shape: {type(body)!r}")


def _conversation_from_dict(d: dict) -> Conversation:
    return Conversation(
        id=d.get("id"),
        phone_number_id=d.get("phoneNumberId"),
        participants=d.get("participants") or [],
        last_activity_at=d.get("lastActivityAt"),
        created_at=d.get("createdAt"),
        updated_at=d.get("updatedAt"),
        name=d.get("name"),
    )


def _message_from_dict(d: dict) -> QuoMessage:
    return QuoMessage(
        id=d.get("id"),
        to=d.get("to"),
        from_number=d.get("from"),
        text=d.get("text"),
        phone_number_id=d.get("phoneNumberId"),
        conversation_id=d.get("conversationId"),
        direction=d.get("direction"),
        status=d.get("status"),
        created_at=d.get("createdAt"),
        updated_at=d.get("updatedAt"),
    )


def _contact_from_dict(d: dict) -> Contact:
    default_fields = d.get("defaultFields") or {}
    phone_numbers = [
        p.get("value") for p in (default_fields.get("phoneNumbers") or []) if p.get("value")
    ]
    return Contact(
        id=d.get("id"),
        first_name=default_fields.get("firstName"),
        last_name=default_fields.get("lastName"),
        company=default_fields.get("company"),
        phone_numbers=phone_numbers,
    )


class QuoClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
        base_url: str = BASE_URL,
    ):
        self._api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if not self._api_key:
            raise QuoConfigError(f"{API_KEY_ENV_VAR} is not set")
        self._session = session or requests.Session()
        self._sleep = sleep_fn
        self._base_url = base_url
        self._last_request_at: Optional[float] = None

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = _MIN_SECONDS_BETWEEN_REQUESTS - elapsed
        if remaining > 0:
            self._sleep(remaining)

    def _get(self, path: str, params: Dict[str, Any]) -> dict:
        clean_params = {k: v for k, v in params.items() if v is not None}
        url = f"{self._base_url}{path}"
        headers = {"Authorization": self._api_key}

        backoff = _INITIAL_BACKOFF_SECONDS
        for attempt in range(_MAX_429_RETRIES + 1):
            self._throttle()
            self._last_request_at = time.monotonic()
            resp = self._session.get(url, headers=headers, params=clean_params, timeout=15)

            if resp.status_code == 429:
                if attempt == _MAX_429_RETRIES:
                    raise QuoRateLimitError(
                        "Quo API rate limit exceeded after retries", status_code=429, body=resp.text,
                    )
                self._sleep(backoff)
                backoff *= 2
                continue

            if not (200 <= resp.status_code < 300):
                raise QuoAPIError(
                    f"Quo API request failed: {resp.status_code}", status_code=resp.status_code, body=resp.text,
                )

            try:
                return resp.json()
            except ValueError as exc:
                raise QuoAPIError(f"Quo API returned non-JSON response: {exc}", status_code=resp.status_code, body=resp.text)

        raise QuoRateLimitError("Quo API rate limit exceeded after retries")

    def list_conversations_page(
        self, *, max_results: int = 10, phone_numbers: Optional[List[str]] = None,
        user_id: Optional[str] = None, created_after: Optional[str] = None,
        created_before: Optional[str] = None, updated_after: Optional[str] = None,
        updated_before: Optional[str] = None, exclude_inactive: Optional[bool] = None,
        page_token: Optional[str] = None,
    ) -> (List[Conversation], Optional[str]):
        body = self._get(
            "/conversations",
            {
                "maxResults": max_results, "phoneNumbers": phone_numbers, "userId": user_id,
                "createdAfter": created_after, "createdBefore": created_before,
                "updatedAfter": updated_after, "updatedBefore": updated_before,
                "excludeInactive": exclude_inactive, "pageToken": page_token,
            },
        )
        items, next_token = _unwrap_list(body)
        return [_conversation_from_dict(item) for item in items], next_token

    def iter_conversations(self, **filters) -> Iterator[Conversation]:
        page_token = None
        while True:
            conversations, next_token = self.list_conversations_page(page_token=page_token, **filters)
            for conv in conversations:
                yield conv
            if not next_token:
                return
            page_token = next_token

    def list_messages_page(
        self, *, phone_number_id: str, participants: List[str], max_results: int = 10,
        user_id: Optional[str] = None, created_after: Optional[str] = None,
        created_before: Optional[str] = None, page_token: Optional[str] = None,
    ) -> (List[QuoMessage], Optional[str]):
        body = self._get(
            "/messages",
            {
                "phoneNumberId": phone_number_id, "participants": participants, "maxResults": max_results,
                "userId": user_id, "createdAfter": created_after, "createdBefore": created_before,
                "pageToken": page_token,
            },
        )
        items, next_token = _unwrap_list(body)
        return [_message_from_dict(item) for item in items], next_token

    def iter_messages(self, *, phone_number_id: str, participants: List[str], **filters) -> Iterator[QuoMessage]:
        page_token = None
        while True:
            messages, next_token = self.list_messages_page(
                phone_number_id=phone_number_id, participants=participants, page_token=page_token, **filters,
            )
            for msg in messages:
                yield msg
            if not next_token:
                return
            page_token = next_token

    def get_contact(self, contact_id: str) -> Contact:
        body = self._get(f"/contacts/{contact_id}", {})
        data = body.get("data", body) if isinstance(body, dict) else body
        return _contact_from_dict(data)
