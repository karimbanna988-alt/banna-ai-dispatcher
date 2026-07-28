"""Standalone background Quo poller.

Not a Windows service. Not a Scheduled Task. Not added to startup. Does
not run automatically after a reboot. Installs nothing anywhere -- it is
a plain Python script that does something only for as long as a human
keeps its process running in a terminal they started themselves. Closing
that terminal, Ctrl+C, or restarting the machine all stop it the same
way: the process simply isn't running anymore, and nothing brings it back
without a human running this file again.

Reuses dispatcher.quo_ingest.poll_for_new_messages() exactly -- no second
poller, no second dedup/checkpoint mechanism. Queues incoming messages
only (direction=="incoming"); outgoing CLOSINGS replies are filtered by
that same, unchanged function. This script never imports
dispatcher.outbound and cannot send, acknowledge, or reply to anything --
QUO_OUTBOUND_MODE is irrelevant to this script because it never even
looks at it.

Manual usage (never automatic):
    python scripts/background_poller.py                # poll once, then exit
    python scripts/background_poller.py --loop                     # poll every 300s until Ctrl+C
    python scripts/background_poller.py --loop --interval 600      # poll every 10 min until Ctrl+C

QUO_POLL_MIN_INTERVAL_SECONDS (default 60) additionally refuses to poll
again before that many seconds have passed since the last *successful*
poll, regardless of how often this script or --interval asks it to --
protects the real Quo API and this deployment from being polled harder
than intended even if the human running this script sets --interval too
low.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from dispatcher import quo_client, quo_ingest, storage
from dispatcher.quo_client import API_KEY_ENV_VAR, QuoAPIError, QuoConfigError

DEFAULT_MIN_INTERVAL_SECONDS = 60
MIN_INTERVAL_ENV_VAR = "QUO_POLL_MIN_INTERVAL_SECONDS"


def seconds_since_last_success(conn) -> Optional[float]:
    checkpoint = storage.get_quo_checkpoint(conn)
    if not checkpoint:
        return None
    try:
        dt = datetime.fromisoformat(checkpoint.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def poll_once(conn, client_factory: Optional[Callable[..., object]] = None) -> dict:
    """Exactly one poll attempt, gated by the minimum-interval guard.
    Never raises -- every outcome (skipped/config_error/api_error/polled)
    is returned as a status dict so a caller (CLI, test, future wrapper)
    always gets a uniform result. Every non-"skipped" outcome that isn't
    a full success durably records the failure via
    storage.record_poll_error() before returning."""
    min_interval = int(os.environ.get(MIN_INTERVAL_ENV_VAR, DEFAULT_MIN_INTERVAL_SECONDS))
    elapsed = seconds_since_last_success(conn)
    if elapsed is not None and elapsed < min_interval:
        return {
            "outcome": "skipped",
            "reason": f"last success was {elapsed:.0f}s ago, minimum interval is {min_interval}s",
        }

    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        storage.record_poll_error(conn, "QUO_API_KEY is not set")
        return {"outcome": "config_error", "reason": "QUO_API_KEY is not set"}

    try:
        client = (client_factory or quo_client.QuoClient)(api_key=api_key)
        outcome = quo_ingest.poll_for_new_messages(conn, client)
    except QuoConfigError as exc:
        storage.record_poll_error(conn, str(exc))
        return {"outcome": "config_error", "reason": str(exc)}
    except QuoAPIError as exc:
        storage.record_poll_error(conn, str(exc))
        return {"outcome": "api_error", "reason": str(exc)}

    return {"outcome": "polled", "queued": len(outcome.queued), "failed": outcome.failed_count}


def _format_status(status: dict) -> str:
    ts = datetime.now(timezone.utc).isoformat()
    if status["outcome"] == "polled":
        return f"[{ts}] Polled -- {status['queued']} new message(s) queued, {status['failed']} failed to persist."
    if status["outcome"] == "skipped":
        return f"[{ts}] Skipped -- {status['reason']}"
    return f"[{ts}] {status['outcome']} -- {status['reason']}"


def run_loop(
    conn, interval_seconds: int, client_factory: Optional[Callable[..., object]] = None,
    max_iterations: Optional[int] = None, sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """A human-started loop, not a scheduler -- the process this runs in
    is the only thing keeping it alive; there is no daemonization, no
    service registration, nothing that survives this process exiting.
    `max_iterations`/`sleep_fn` exist only so tests can run this
    deterministically without an actual infinite loop or real sleeping."""
    iterations = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            status = poll_once(conn, client_factory)
            print(_format_status(status))
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                sleep_fn(interval_seconds)
    except KeyboardInterrupt:
        print("Stopped (Ctrl+C) -- clean shutdown, no partial state left behind.")
        return 0
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone, manually-started Quo background poller.")
    parser.add_argument("--loop", action="store_true", help="Poll repeatedly until Ctrl+C, instead of once and exit.")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between polls in --loop mode (default 300).")
    args = parser.parse_args(argv)

    conn = storage.connect()
    try:
        if args.loop:
            return run_loop(conn, args.interval)
        status = poll_once(conn)
        print(_format_status(status))
        return 0 if status["outcome"] in ("polled", "skipped") else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
