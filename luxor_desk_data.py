"""Fabricated data for LUXOR OPERATIONS DESK -- a workflow-first visual
prototype. Every name, ticket, phone number, and address below is
invented for this prototype only. This module never imports
dispatcher.storage, never opens the real SQLite database, and never
calls Quo -- exactly six jobs, one per required workflow example, plus a
small amount of secondary "Records" data (technicians / closings
history / activity) for the non-primary Records panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

STAGES = ("New", "Active", "Closing", "Done")


@dataclass(frozen=True)
class DeskJob:
    stage: str  # one of STAGES
    source: str  # "AGN" | "Lucky" | "Main Leads"
    ticket: str
    age_label: str
    urgency: str  # "urgent" | "warning" | "normal"
    city_state: str
    service: str
    customer: str
    phone: str
    address: str
    vehicle: Optional[str]
    notes: str
    missing: list[str]
    conflicting: list[str]
    acknowledgment_required: bool
    acknowledged: bool
    dispatched: bool
    technician: Optional[str]
    original_message: str
    luxor_message: str
    tech_status: Optional[str] = None  # "Going" | "ETA" | "No Answer" | "No Show" | "Not Available" | "Cancelled"
    company_update_message: Optional[str] = None
    closing_amount: Optional[str] = None
    payment_method: Optional[str] = None
    company_closing_message: Optional[str] = None
    update_history: list[str] = field(default_factory=list)


DESK_JOBS: list[DeskJob] = [
    # 1. AGN new job requiring K
    DeskJob(
        stage="New", source="AGN", ticket="A9K2L", age_label="42 sec", urgency="normal",
        city_state="Charlotte, NC", service="Car Lockout",
        customer="Rosa Martin", phone="(704) 555-0148 ext 12",
        address="118 Birchwood Ln, Charlotte, NC 28202", vehicle="2021 Honda Civic",
        notes="Customer locked keys in car, in a parking garage.",
        missing=[], conflicting=[], acknowledgment_required=True, acknowledged=False, dispatched=False,
        technician="Alex Rivera (eligible)",
        original_message="Ref: AGN Locksmith Services\nPDL: A9K2L\nPh: 1-704-555-0148 ext 12\nAddr: 118 Birchwood Ln, Charlotte, NC\nDesc: Car Lockout",
        luxor_message=(
            "LUXOR LOCKSMITH\nNEW SERVICE REQUEST\n\nTicket: A9K2L\nCustomer: Rosa Martin\n"
            "Phone: (704) 555-0148 ext 12\n\nService Address:\n118 Birchwood Ln, Charlotte, NC 28202\n\n"
            "Service Requested:\nCar Lockout\n\nVehicle:\n2021 Honda Civic"
        ),
    ),
    # 2. Lucky new job requiring K
    DeskJob(
        stage="New", source="Lucky", ticket="L4P81", age_label="1 min 55 sec", urgency="warning",
        city_state="Raleigh, NC", service="House Lockout",
        customer="Devon Walsh", phone="(910) 555-0122",
        address="742 Maple Crest Dr, Raleigh, NC 27601", vehicle=None,
        notes="Customer locked out, elderly parent inside, prefers fast response.",
        missing=[], conflicting=[], acknowledgment_required=True, acknowledged=False, dispatched=False,
        technician="Jordan Blake, Sam Okafor (2 eligible)",
        original_message="Co: LUCKY LOCKSMITH LLC\nPDL: L4P81\nPh: 1-910-555-0122\nAddr: 742 Maple Crest Dr, Raleigh, NC\nDesc: Home Lockout",
        luxor_message=(
            "LUXOR LOCKSMITH\nNEW SERVICE REQUEST\n\nTicket: L4P81\nCustomer: Devon Walsh\n"
            "Phone: (910) 555-0122\n\nService Address:\n742 Maple Crest Dr, Raleigh, NC 27601\n\n"
            "Service Requested:\nHouse Lockout"
        ),
    ),
    # 3. Main Leads new job requiring no K
    DeskJob(
        stage="New", source="Main Leads", ticket="ML-77QX", age_label="3 min 10 sec", urgency="urgent",
        city_state="Savannah, GA", service="New Key Fob",
        customer="Priya Chandran", phone="(843) 555-0197",
        address="30 Harbor View Ct, Savannah, GA 31401", vehicle="2019 Toyota RAV4",
        notes="Asking for a price before confirming.",
        missing=["service notes"], conflicting=[], acknowledgment_required=False, acknowledged=False, dispatched=False,
        technician="Taylor Nguyen (eligible)",
        original_message="Power By Ucallz App\n\nSavannah Locksmith Pro\nTicket: ML-77QX\nName: Priya Chandran\nPhone: (843) 555-0197\nAddress: 30 Harbor View Ct, Savannah, GA 31401\nDesc: New Key Fob",
        luxor_message=(
            "LUXOR LOCKSMITH\nNEW SERVICE REQUEST\n\nTicket: ML-77QX\nCustomer: Priya Chandran\n"
            "Phone: (843) 555-0197\n\nService Address:\n30 Harbor View Ct, Savannah, GA 31401\n\n"
            "Service Requested:\nNew Key Fob\n\nVehicle:\n2019 Toyota RAV4"
        ),
    ),
    # 4. Dispatched job awaiting technician update
    DeskJob(
        stage="Active", source="AGN", ticket="A6D19", age_label="22 min", urgency="normal",
        city_state="Charlotte, NC", service="Car Lockout",
        customer="Grace Kim", phone="(704) 555-0175",
        address="88 Larkspur Rd, Charlotte, NC 28204", vehicle="2018 Mazda CX-5",
        notes="Second vehicle in driveway is not the customer's.",
        missing=[], conflicting=[], acknowledgment_required=True, acknowledged=True, dispatched=True,
        technician="Alex Rivera",
        original_message="Ref: AGN Locksmith Services\nPDL: A6D19\nPh: 1-704-555-0175\nAddr: 88 Larkspur Rd, Charlotte, NC\nDesc: Car Lockout",
        luxor_message=(
            "LUXOR LOCKSMITH\nNEW SERVICE REQUEST\n\nTicket: A6D19\nCustomer: Grace Kim\n"
            "Phone: (704) 555-0175\n\nService Address:\n88 Larkspur Rd, Charlotte, NC 28204\n\n"
            "Service Requested:\nCar Lockout\n\nVehicle:\n2018 Mazda CX-5"
        ),
        tech_status="Not Available",
        update_history=["Dispatched to Alex Rivera -- 18 min ago"],
    ),
    # 5. Job with an ETA update ready for the company (Active stage covers
    # both "dispatched, awaiting update" and "update ready to send" -- the
    # dispatcher-facing stage navigator has only New / Active / Closing).
    DeskJob(
        stage="Active", source="Lucky", ticket="L2Q56", age_label="15 min", urgency="normal",
        city_state="Durham, NC", service="Car Ignition Key",
        customer="Nina Torres", phone="(910) 555-0133",
        address="18 Cobblestone Way, Durham, NC 27701", vehicle="2016 Ford Focus",
        notes="Customer asked for a callback with an ETA.",
        missing=[], conflicting=[], acknowledgment_required=True, acknowledged=True, dispatched=True,
        technician="Jordan Blake",
        original_message="Co: LUCKY LOCKSMITH LLC\nPDL: L2Q56\nPh: 1-910-555-0133\nAddr: 18 Cobblestone Way, Durham, NC\nDesc: Car Ignition Key",
        luxor_message=(
            "LUXOR LOCKSMITH\nNEW SERVICE REQUEST\n\nTicket: L2Q56\nCustomer: Nina Torres\n"
            "Phone: (910) 555-0133\n\nService Address:\n18 Cobblestone Way, Durham, NC 27701\n\n"
            "Service Requested:\nCar Ignition Key\n\nVehicle:\n2016 Ford Focus"
        ),
        tech_status="ETA",
        company_update_message="LUXOR LOCKSMITH\nTECHNICIAN UPDATE\n\nTicket: L2Q56\nStatus: ETA 15 minutes",
        update_history=["Dispatched to Jordan Blake -- 12 min ago", "Technician reported ETA 15 min -- 1 min ago"],
    ),
    # 6. Closing ready with amount and payment method
    DeskJob(
        stage="Closing", source="Main Leads", ticket="ML-52JD", age_label="1 hr 5 min", urgency="normal",
        city_state="Savannah, GA", service="Home Lockout",
        customer="Owen Fitzgerald", phone="(843) 555-0161",
        address="9 Riverside Pl, Savannah, GA 31401", vehicle=None,
        notes="Job completed without issue.",
        missing=[], conflicting=[], acknowledgment_required=False, acknowledged=False, dispatched=True,
        technician="Taylor Nguyen",
        original_message="Power By Ucallz App\n\nSavannah Locksmith Pro\nTicket: ML-52JD\nName: Owen Fitzgerald\nPhone: (843) 555-0161\nAddress: 9 Riverside Pl, Savannah, GA 31401\nDesc: Home Lockout",
        luxor_message=(
            "LUXOR LOCKSMITH\nNEW SERVICE REQUEST\n\nTicket: ML-52JD\nCustomer: Owen Fitzgerald\n\n"
            "Service Address:\n9 Riverside Pl, Savannah, GA 31401\n\nService Requested:\nHome Lockout"
        ),
        closing_amount="$95.00", payment_method="Card",
        company_closing_message="LUXOR LOCKSMITH\nCLOSING\n\nTicket: ML-52JD\nAmount: $95.00\nPayment: Card",
        update_history=["Dispatched to Taylor Nguyen -- 58 min ago", "Job completed -- 14 min ago"],
    ),
]

TECH_STATUS_OPTIONS = ("Going", "ETA", "No Answer", "No Show", "Not Available", "Cancelled")


@dataclass(frozen=True)
class RecordsTechnician:
    name: str
    areas: list[str]
    skills: list[str]
    dispatch_group: str
    active: bool


RECORDS_TECHNICIANS: list[RecordsTechnician] = [
    RecordsTechnician("Alex Rivera", ["Charlotte, NC"], ["Car Lockout", "House Lockout", "Rekey"], "AGN - Charlotte Dispatch", True),
    RecordsTechnician("Jordan Blake", ["Raleigh, NC", "Durham, NC"], ["House Lockout", "Rekey", "Ignition Key"], "Lucky - Raleigh Group", True),
    RecordsTechnician("Sam Okafor", ["Raleigh, NC"], ["House Lockout", "Car Lockout"], "Lucky - Raleigh Group", True),
    RecordsTechnician("Taylor Nguyen", ["Savannah, GA"], ["New Key Fob", "Home Lockout"], "Main Leads - Savannah", True),
]


@dataclass(frozen=True)
class RecordsClosing:
    ticket: str
    source: str
    amount: str
    payment_method: str
    technician: str
    closed_label: str


RECORDS_CLOSINGS: list[RecordsClosing] = [
    RecordsClosing("ML-52JD", "Main Leads", "$95.00", "Card", "Taylor Nguyen", "14 min ago"),
    RecordsClosing("L9V44", "Lucky", "$140.00", "Cash", "Jordan Blake", "1 hr 40 min ago"),
]


@dataclass(frozen=True)
class RecordsActivity:
    label: str
    detail: str
    at_label: str
    source: str


RECORDS_ACTIVITY: list[RecordsActivity] = [
    RecordsActivity("Job closed", "ML-52JD -- $95 collected (card)", "14 min ago", "Main Leads"),
    RecordsActivity("ETA reported", "L2Q56 -- 15 minutes", "1 min ago", "Lucky"),
    RecordsActivity("Dispatched", "A6D19 -- Alex Rivera", "18 min ago", "AGN"),
]
