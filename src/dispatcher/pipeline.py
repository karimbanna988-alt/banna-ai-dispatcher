"""The single extract -> classify -> profile -> decide -> format sequence.

Extracted so the real "paste a message" flow (app.py's Process tab) and the
fake-inbox simulator's "deliver a scripted message" flow call the exact same
code -- there must never be two places that reimplement this orchestration.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import storage
from .companies import CompanyProfile, get_company_profile
from .formatter import format_technician_message
from .models import ExtractedJob
from .rules import Decision, decide, force_handle_manually
from .rules.daily import DailyRules
from .storage import ClassifyResult, MessageType
from .parser import extract_job


@dataclass
class ProcessResult:
    job: ExtractedJob
    classify_result: ClassifyResult
    profile: CompanyProfile
    decision: Decision
    luxor_message: str


def process_raw_message(
    raw_text: str,
    vehicle_rules: dict,
    service_areas: list,
    conn: sqlite3.Connection,
    daily_rules: Optional[DailyRules],
) -> ProcessResult:
    job = extract_job(raw_text, vehicle_rules)
    classify_result = storage.classify_incoming(conn, job)
    profile = get_company_profile(job.source)
    decision = decide(job, profile, service_areas, daily_rules)
    if classify_result.message_type == MessageType.CLOSING_WITHOUT_DISPATCH:
        decision = force_handle_manually(decision, classify_result.reason)
    luxor_message = format_technician_message(job)
    return ProcessResult(
        job=job, classify_result=classify_result, profile=profile,
        decision=decision, luxor_message=luxor_message,
    )
