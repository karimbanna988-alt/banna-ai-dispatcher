"""Sanitized JSONL export of reviewed Shadow Mode episodes -- see
docs/SHADOW_MODE.md. Only VERIFIED/CORRECTED episodes are ever included
(storage.list_shadow_episodes_for_export() already enforces the review
gate); this module's own job is redaction of what's already been
review-gated, never the gating itself.

Never call this output "training data" unless a separate training
process actually exists and consumes it -- see docs/SHADOW_MODE.md
section 16 (no automatic/online learning in this build).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import List, Optional

_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_ADDRESS_HINT_RE = re.compile(
    r"\d{1,6}\s+[A-Za-z0-9.\s]+?\b(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place)\b\.?",
    re.IGNORECASE,
)


def _fingerprint(text: Optional[str]) -> Optional[str]:
    """Stable one-way fingerprint for correlation without exposing the
    original value -- e.g. a ticket/PDL, which is otherwise a
    guessable, low-entropy identifier."""
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sanitize_text(text: Optional[str]) -> Optional[str]:
    """Best-effort mechanical redaction of phone-number-shaped and
    street-address-shaped substrings. This is NOT a substitute for
    human review -- it cannot reliably detect an arbitrary customer
    name in free text (no NER model is used; this phase stays fully
    deterministic, no LLM). That is exactly why only VERIFIED/CORRECTED
    episodes -- ones a human has already looked at -- are ever eligible
    for export; see storage.list_shadow_episodes_for_export()."""
    if not text:
        return text
    text = _PHONE_RE.sub("[phone redacted]", text)
    text = _ADDRESS_HINT_RE.sub("[address redacted]", text)
    return text


def _sanitize_commercial_update(cu: Optional[dict]) -> Optional[dict]:
    if not cu:
        return cu
    sanitized = dict(cu)
    if "raw_technician_text" in sanitized:
        sanitized["raw_technician_text"] = sanitize_text(sanitized["raw_technician_text"])
    return sanitized


# Fields never included in an exported record -- actor/technician
# identity and internal message ids (a ticket/PDL is fingerprinted
# instead, for correlation without exposing the raw value).
_EXCLUDED_EPISODE_FIELDS = frozenset({
    "episode_id", "dispatcher_actor", "technician_id",
    "source_message_id", "pending_message_id", "job_id",
    "created_at", "updated_at", "reviewed_at", "eligible_for_export",
})


def sanitize_episode_for_export(episode: dict) -> dict:
    """One sanitized JSONL record for one already-review-gated episode.
    Never includes customer names, phone numbers, full addresses,
    credentials, tunnel URLs, or local database paths -- none of those
    are fields this model ever stores in the first place (see
    shadow.ShadowEpisode); the only free-text fields that could contain
    incidental PII (technician/company reply text) are passed through
    sanitize_text()."""
    return {
        "source": episode.get("source"),
        "channel": episode.get("channel"),
        "ticket_or_pdl_fingerprint": _fingerprint(episode.get("ticket_or_pdl")),
        "previous_job_status": episode.get("previous_job_status"),
        "incoming_message_type": episode.get("incoming_message_type"),
        "normalized_intent": episode.get("normalized_intent"),
        "shadow_suggested_action": episode.get("shadow_suggested_action"),
        "shadow_suggested_reply": sanitize_text(episode.get("shadow_suggested_reply")),
        "shadow_confidence": episode.get("shadow_confidence"),
        "shadow_reasons": episode.get("shadow_reasons") or [],
        "validation_issues": episode.get("validation_issues") or [],
        "human_review_required": episode.get("human_review_required"),
        "dispatcher_actual_action": episode.get("dispatcher_actual_action"),
        "dispatcher_actual_reply": sanitize_text(episode.get("dispatcher_actual_reply")),
        "actual_send_channel": episode.get("actual_send_channel"),
        "commercial_update": _sanitize_commercial_update(episode.get("commercial_update")),
        "customer_response": episode.get("customer_response"),
        "negotiation_outcome": episode.get("negotiation_outcome"),
        "final_job_outcome": episode.get("final_job_outcome"),
        "review_status": episode.get("review_status"),
        "schema_version": episode.get("schema_version"),
    }


def export_shadow_dataset_jsonl(episodes: List[dict]) -> str:
    """Pure function over an already-fetched, already-review-gated list
    of episodes -- never queries a database itself, so it can never
    accidentally include an episode a caller forgot to filter through
    storage.list_shadow_episodes_for_export(). One JSON object per line,
    keys sorted for stable diffs."""
    lines = [json.dumps(sanitize_episode_for_export(ep), sort_keys=True) for ep in episodes]
    return "\n".join(lines) + ("\n" if lines else "")
