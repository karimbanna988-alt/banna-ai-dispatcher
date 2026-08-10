"""Phase 6 of the launch brief: the WhatsApp shadow bridge's retry queue
(.runtime/whatsapp-shadow-session/pending-delivery.json) can accumulate
observations that will NEVER succeed via the normal retry path -- e.g. a
real WhatsApp message whose body was empty (a photo/sticker/reaction
with no caption; whatsapp-web.js's msg.body is "" for those), which
fails validateObservation's "text is required" check every single retry
attempt, forever, cluttering the log and never actually delivering.

This script inspects the queue (read-only classification, no guessing):
  - an item with genuinely non-empty text/messageId/groupId/groupName and
    a valid timestamp is left in the active queue untouched -- it WILL
    deliver once the adapter is reachable, no migration needed.
  - an item that fails validation deterministically (i.e. always will,
    regardless of adapter availability -- structurally invalid, not
    transiently down) is moved to a separate quarantine file, preserving
    every field it has (minus the empty text, which carries no
    information) so a human can look it up later if needed, and removed
    from the active retry queue so it stops blocking/cluttering it.

Never invents replacement text. Never deletes anything -- quarantined
items are relocated, not discarded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

RETRY_QUEUE_FILE = (
    Path(__file__).resolve().parent.parent / ".runtime" / "whatsapp-shadow-session" / "pending-delivery.json"
)
QUARANTINE_FILE = (
    Path(__file__).resolve().parent.parent / ".runtime" / "whatsapp-shadow-session" / "legacy-quarantine.json"
)

REQUIRED_STRING_FIELDS = ("messageId", "groupId", "groupName", "text")


def classify(observation: dict) -> str:
    """Mirrors adapterClient.js's validateObservation / whatsapp_bridge_adapter.py's
    validate_observation exactly: any required string field that's
    missing or empty means this item can never deliver as-is."""
    for field in REQUIRED_STRING_FIELDS:
        value = observation.get(field)
        if not value or not isinstance(value, str):
            return "quarantine"
    timestamp = observation.get("timestamp")
    if timestamp is None or timestamp == "":
        return "quarantine"
    return "keep"


def run(retry_queue_file: Path = RETRY_QUEUE_FILE, quarantine_file: Path = QUARANTINE_FILE) -> dict:
    try:
        data = json.loads(retry_queue_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"kept": 0, "quarantined": 0}
    queued = data.get("queued", [])

    kept, quarantined = [], []
    for obs in queued:
        (kept if classify(obs) == "keep" else quarantined).append(obs)

    if quarantined:
        try:
            existing_quarantine = json.loads(quarantine_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            existing_quarantine = {"quarantined": []}
        existing_ids = {o.get("messageId") for o in existing_quarantine.get("quarantined", [])}
        for obs in quarantined:
            if obs.get("messageId") not in existing_ids:
                existing_quarantine.setdefault("quarantined", []).append({
                    **obs,
                    "quarantine_reason": "LEGACY_REVIEW_REQUIRED: empty or missing required field, "
                                          "cannot deliver deterministically (likely a media/caption-less message)",
                })
        quarantine_file.parent.mkdir(parents=True, exist_ok=True)
        quarantine_file.write_text(json.dumps(existing_quarantine, indent=2), encoding="utf-8")

    retry_queue_file.write_text(json.dumps({"queued": kept}, indent=2), encoding="utf-8")
    return {"kept": len(kept), "quarantined": len(quarantined)}


if __name__ == "__main__":
    result = run()
    print(f"Retry queue: {result['kept']} kept (will deliver normally), "
          f"{result['quarantined']} quarantined to {QUARANTINE_FILE}")
