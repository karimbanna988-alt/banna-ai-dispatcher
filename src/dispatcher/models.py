"""Shared data classes for the Luxor Locksmith dispatcher parser.

See docs/phase1_scope.md for how these are used (confidence scoring,
Handle Manually triggers, audit log).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Source(str, Enum):
    MAIN_LEADS = "Main Leads"
    AGN = "AGN"
    LUCKY = "Lucky"
    UNKNOWN = "Unknown"


class ExtractionStatus(str, Enum):
    OK = "OK"
    HANDLE_MANUALLY = "HANDLE_MANUALLY"


@dataclass
class FieldValue:
    """A single extracted field with its confidence and reasoning.

    `candidates` is only populated when more than one conflicting value
    was found for the same field — never invented, only observed.
    """

    value: Optional[str]
    confidence: int
    reason: str
    candidates: List[str] = field(default_factory=list)

    @property
    def is_missing(self) -> bool:
        return self.value is None

    @property
    def is_conflicting(self) -> bool:
        return len(self.candidates) > 1


@dataclass
class ExtractedJob:
    """Full extraction result for one raw job message."""

    raw_text: str

    source: Source
    source_confidence: int
    source_reason: str

    ticket_number: FieldValue
    customer_name: FieldValue
    phone: FieldValue
    second_phone: FieldValue
    extension: FieldValue
    address: FieldValue
    city: FieldValue
    state: FieldValue
    zip_code: FieldValue
    service_type: FieldValue
    vehicle: FieldValue
    notes: FieldValue

    vehicle_restriction_status: str = "not_applicable"
    vehicle_restriction_reason: str = ""

    overall_confidence: int = 0
    status: ExtractionStatus = ExtractionStatus.OK
    status_reasons: List[str] = field(default_factory=list)

    def all_fields(self) -> Dict[str, FieldValue]:
        return {
            "ticket_number": self.ticket_number,
            "customer_name": self.customer_name,
            "phone": self.phone,
            "second_phone": self.second_phone,
            "extension": self.extension,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
            "service_type": self.service_type,
            "vehicle": self.vehicle,
            "notes": self.notes,
        }
