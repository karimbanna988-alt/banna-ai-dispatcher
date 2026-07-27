"""One-off, read-only manual verification script -- NOT part of the app,
NOT collected by pytest (lives outside tests/, filename doesn't match
test_*.py).

Makes exactly two GET requests to the real Quo API using your own
QUO_API_KEY, prints the raw JSON to stdout, and does nothing else: no
writes to storage, no calls into the parser/pipeline, nothing sent
anywhere. Purpose: confirm the real response shape (envelope, the exact
`direction` value for an inbound message, and whether `message.text`
contains our parser's expected header lines verbatim) before quo_client.py's
parsing code is finalized against documentation alone.

Usage: create a .env file (copy .env.example) with your real QUO_API_KEY
in it, then just run:
    python scripts/quo_probe_readonly.py

(.env is loaded automatically below and is already gitignored.)

The key itself is never printed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests

BASE_URL = "https://api.quo.com/v1"
API_KEY_ENV_VAR = "QUO_API_KEY"


def main() -> int:
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        print(f"ERROR: {API_KEY_ENV_VAR} is not set in this shell.", file=sys.stderr)
        print('PowerShell: $env:QUO_API_KEY = "your-key-here"', file=sys.stderr)
        return 1

    headers = {"Authorization": api_key}

    print(f"=== GET {BASE_URL}/conversations?maxResults=1 ===")
    resp = requests.get(f"{BASE_URL}/conversations", headers=headers, params={"maxResults": 1}, timeout=15)
    print(f"HTTP {resp.status_code}")
    try:
        conv_body = resp.json()
    except ValueError:
        print("Response was not JSON:")
        print(resp.text[:2000])
        return 1
    print(json.dumps(conv_body, indent=2))

    if resp.status_code != 200:
        print("\nStopping here -- conversations call did not return 200.", file=sys.stderr)
        return 1

    # Try to find a conversation in the response regardless of envelope shape.
    conversations = conv_body.get("data") if isinstance(conv_body, dict) else conv_body
    if not conversations:
        print("\nNo conversations returned -- nothing to probe for /messages. Done.")
        return 0

    first = conversations[0]
    phone_number_id = first.get("phoneNumberId")
    participants = first.get("participants") or []
    if not phone_number_id or not participants:
        print("\nFirst conversation is missing phoneNumberId/participants -- can't probe /messages.")
        print("Conversation object was:", json.dumps(first, indent=2))
        return 0

    print(f"\n=== GET {BASE_URL}/messages?phoneNumberId={phone_number_id}&participants={participants[0]}&maxResults=1 ===")
    resp2 = requests.get(
        f"{BASE_URL}/messages", headers=headers,
        params={"phoneNumberId": phone_number_id, "participants": participants[0], "maxResults": 1},
        timeout=15,
    )
    print(f"HTTP {resp2.status_code}")
    try:
        msg_body = resp2.json()
    except ValueError:
        print("Response was not JSON:")
        print(resp2.text[:2000])
        return 1
    print(json.dumps(msg_body, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
