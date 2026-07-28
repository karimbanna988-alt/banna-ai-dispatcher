"""Polling/dedup logic that turns real Quo activity into queued raw
messages ready for pipeline.process_raw_message() -- nothing in this module
writes anything back to Quo; it only reads via QuoClient's GET-only methods
and writes to our own local storage (checkpoint + dedup tables).

Confirmed 2026-07-27: generic discovery via GET /v1/conversations lags real
message activity (new messages from known dispatch groups did not appear in
the "most recent" conversations list even when only minutes old). Instead,
this polls a small, known, configured list of dispatch-group contacts
directly (rules/quo_dispatch_groups.yaml) via GET /v1/messages -- no
conversation discovery involved.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import streamlit as st
import yaml

from . import storage
from .quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoClient, QuoConfigError, QuoMessage

DISPATCH_GROUPS_PATH = Path(__file__).resolve().parents[2] / "rules" / "quo_dispatch_groups.yaml"

DEFAULT_LOOKBACK_MINUTES = 15
LOOKBACK_ENV_VAR = "QUO_POLL_INITIAL_LOOKBACK_MINUTES"

# Audit-log channel tag for a message delivered via this module -- shadow
# mode: read-only, nothing was ever sent, replied to, or acknowledged.
SHADOW_MODE_CHANNEL = "shadow_mode_quo_live"

# Confirmed 2026-07-27 against real Quo messages: the literal values are
# "incoming" and "outgoing".
INBOUND_DIRECTION_VALUES = {"incoming"}

# Small overlap so poll-window edges (clock skew between when we poll and
# when Quo stamps createdAt/updatedAt) don't cause a missed message -- the
# dedup table absorbs any resulting re-fetches for free, at no risk of a
# message being queued twice.
_OVERLAP_BUFFER_SECONDS = 30


@dataclass(frozen=True)
class DispatchGroupConfig:
    label: str
    quo_contact_id: str


@dataclass(frozen=True)
class QuoPollConfig:
    our_phone_number_id: str
    dispatch_groups: List[DispatchGroupConfig] = field(default_factory=list)


@dataclass(frozen=True)
class QueuedQuoMessage:
    message_id: str
    conversation_id: Optional[str]
    raw_text: str
    received_at: Optional[str]
    from_number: Optional[str]
    group_label: str


@dataclass(frozen=True)
class PollOutcome:
    queued: List[QueuedQuoMessage]
    failed_count: int


def load_dispatch_groups(path: Optional[Path] = None) -> QuoPollConfig:
    with open(path or DISPATCH_GROUPS_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    groups = [
        DispatchGroupConfig(label=g["label"], quo_contact_id=g["quo_contact_id"])
        for g in raw.get("dispatch_groups", [])
    ]
    return QuoPollConfig(our_phone_number_id=raw.get("our_phone_number_id"), dispatch_groups=groups)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _initial_checkpoint() -> str:
    """First-ever poll: look back a bounded window rather than importing
    the org's entire message history."""
    lookback_minutes = int(os.environ.get(LOOKBACK_ENV_VAR, DEFAULT_LOOKBACK_MINUTES))
    return _iso(_now() - timedelta(minutes=lookback_minutes))


def _is_inbound(msg: QuoMessage) -> bool:
    return (msg.direction or "").strip().lower() in INBOUND_DIRECTION_VALUES


def poll_for_new_messages(
    conn: sqlite3.Connection, client: QuoClient, config: Optional[QuoPollConfig] = None,
) -> PollOutcome:
    """Fetches new inbound messages and durably stores each one's full
    content *before* marking its Quo id processed (storage.
    save_pending_and_mark_processed does both in one transaction) -- a
    database failure can never result in a message being marked processed
    without its content being safely recoverable.

    If any message in this poll fails to persist, the poll checkpoint is
    NOT advanced (the whole poll's checkpoint update is skipped), so the
    next poll re-scans the same window: messages that already succeeded are
    skipped again via the untouched quo_processed_messages dedup ledger
    (unaffected by this poll's rollback), while the message(s) that failed
    -- never marked processed -- are naturally retried."""
    config = config or load_dispatch_groups()
    poll_started_at = _now()
    checkpoint = storage.get_quo_checkpoint(conn) or _initial_checkpoint()

    queued: List[QueuedQuoMessage] = []
    failed_count = 0
    for group in config.dispatch_groups:
        contact = client.get_contact(group.quo_contact_id)
        if not contact.phone_numbers:
            continue
        for phone in contact.phone_numbers:
            for msg in client.iter_messages(
                phone_number_id=config.our_phone_number_id, participants=[phone],
                created_after=checkpoint, max_results=100,
            ):
                if not _is_inbound(msg):
                    continue
                if not msg.id or storage.is_quo_message_processed(conn, msg.id):
                    continue
                if not msg.text:
                    # Nothing to lose -- metadata-only processed marker is
                    # sufficient, matches the pre-existing documented
                    # behavior (see test_message_with_no_text_is_marked_
                    # processed_but_not_queued).
                    storage.mark_quo_message_processed(conn, msg.id, msg.conversation_id)
                    continue
                try:
                    storage.save_pending_and_mark_processed(
                        conn, message_id=msg.id, conversation_id=msg.conversation_id,
                        group_label=group.label, raw_text=msg.text,
                        from_number=msg.from_number, received_at=msg.created_at,
                    )
                except Exception:
                    failed_count += 1
                    continue
                queued.append(QueuedQuoMessage(
                    message_id=msg.id, conversation_id=msg.conversation_id, raw_text=msg.text,
                    received_at=msg.created_at, from_number=msg.from_number, group_label=group.label,
                ))

    if failed_count == 0:
        new_checkpoint = poll_started_at - timedelta(seconds=_OVERLAP_BUFFER_SECONDS)
        storage.set_quo_checkpoint(conn, _iso(new_checkpoint))
    return PollOutcome(queued=queued, failed_count=failed_count)


def render_quo_live_tab(conn: sqlite3.Connection) -> None:
    """Streamlit UI for the "Quo Inbox (Live)" tab. Receive-only: polls via
    QuoClient's GET-only methods and, when the dispatcher chooses to load a
    queued message, hands it to the review screen through the exact same
    `sim_pending_delivery` mechanism the fake-inbox simulator uses --
    `simulator.apply_pending_delivery` doesn't know or care whether the text
    came from a scenario fixture or a real Quo message.

    The queue itself is backed entirely by storage.quo_pending_messages, not
    session state -- every render re-reads it fresh, so the displayed queue
    is correct immediately after an app or laptop restart, not just within
    one browser session."""
    st.subheader("Quo inbox (live, read-only)")
    st.caption(
        "Polls the known dispatch-group contacts (rules/quo_dispatch_groups.yaml) for new inbound "
        "messages and feeds them into the same review screen as every other message. This only ever "
        "makes GET requests -- nothing is ever sent, replied to, or modified on Quo from this tab. "
        "The queue is stored durably and survives an app or laptop restart."
    )

    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        st.error(f"{API_KEY_ENV_VAR} is not set in this environment -- see .env.example.")
        return

    if st.button("Poll Quo now", type="primary"):
        try:
            client = QuoClient(api_key=api_key)
            outcome = poll_for_new_messages(conn, client)
            st.success(f"Found {len(outcome.queued)} new message(s).")
            if outcome.failed_count:
                st.error(
                    f"{outcome.failed_count} message(s) could not be saved durably and were "
                    "NOT marked as processed -- they will be retried on the next poll."
                )
        except QuoConfigError as exc:
            st.error(str(exc))
        except QuoAPIError as exc:
            st.error(f"Quo API error: {exc}")

    pending = storage.get_pending_quo_messages(conn)
    total_pending = storage.count_pending_quo_messages(conn)
    st.write(f"**{total_pending} message(s) queued for review.**")
    for item in pending:
        with st.expander(f"[{item['group_label']}] {item['received_at'] or '(unknown time)'} -- {item['status']}"):
            st.code(item["raw_text"])

    if st.button("Load next Quo message into review", disabled=total_pending == 0):
        next_item = storage.get_next_pending_quo_message(conn)
        if next_item is not None:
            storage.mark_quo_pending_message_loaded(conn, next_item["message_id"])
            st.session_state.sim_pending_delivery = {
                "kind": "message", "content": next_item["raw_text"], "channel": SHADOW_MODE_CHANNEL,
                "quo_message_id": next_item["message_id"],
            }
            st.rerun()
