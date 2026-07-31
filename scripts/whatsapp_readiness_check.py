"""Safe WhatsApp Cloud API readiness check.

Reports only whether the standard Meta/WhatsApp Cloud API configuration
environment variables are *present* in the current environment. Never
reads, prints, or logs any variable's value -- presence is checked via
`"NAME" in os.environ`, and the report never touches `os.environ["NAME"]`.

This script makes no network calls and does not require any of the
variables to exist. See docs/whatsapp_groups_readiness.md for what these
variables are for and why none of them are configured yet.

Usage:
    python scripts/whatsapp_readiness_check.py

Exit code is always 0 -- this is a report, not a gate.
"""

from __future__ import annotations

import os

REQUIRED_NAMES = [
    "WHATSAPP_META_APP_ID",
    "WHATSAPP_BUSINESS_ACCOUNT_ID",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_CALLBACK_URL",
]


def main() -> None:
    print("WhatsApp Cloud API readiness check (names only -- no values read or printed)\n")
    present = [name for name in REQUIRED_NAMES if name in os.environ]
    missing = [name for name in REQUIRED_NAMES if name not in os.environ]

    for name in REQUIRED_NAMES:
        status = "present" if name in present else "missing"
        print(f"  [{status:7}] {name}")

    print(f"\n{len(present)}/{len(REQUIRED_NAMES)} configuration variable names present.")
    if missing:
        print("Missing names listed above. This is expected until a WhatsApp")
        print("Cloud API integration phase is explicitly approved and started --")
        print("see docs/whatsapp_groups_readiness.md.")
    else:
        print("All expected names are present in the environment (values not")
        print("verified by this script -- presence only).")


if __name__ == "__main__":
    main()
