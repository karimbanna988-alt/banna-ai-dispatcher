"""An owner-posted job in a confirmed intake group is a job.

A message the paired account sends itself is audit-only everywhere
else, and must stay that way: in a technician group it is the
dispatcher forwarding a job that already exists, and counting that as
an arrival would invent duplicate work out of our own outbound
traffic. The single exception is a group the owner has explicitly
confirmed as COMPANY_JOB_INTAKE -- that group is, by the owner's own
definition, where jobs arrive.
"""

import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dispatcher import storage  # noqa: E402

INTAKE_GROUP = "intake@g.us"
TECH_GROUP = "tech@g.us"

JOB_TEXT = (
    "Ref: Example Locksmith\n"
    "PDL: ZZ9QQ\n"
    "N: Test Customer\n"
    "Ph: 1-919-555-0101\n"
    "Addr: 1 Test St , Raleigh, NC, 27610\n"
    "Desc: Car Lockout\n"
)


def _conn(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    storage.upsert_group_registry_entry(conn, {
        "group_id": INTAKE_GROUP, "exact_name": "Intake", "role": "COMPANY_JOB_INTAKE",
        "company": "Example Co", "service_area": None, "technician_id": None,
        "technician_name_unconfirmed": None, "is_active": 1, "confirmed_by_owner": 1,
        "source": "test", "notes": None,
    })
    storage.upsert_group_registry_entry(conn, {
        "group_id": TECH_GROUP, "exact_name": "Tech", "role": "TECH_DISPATCH",
        "company": "Example Co", "service_area": None, "technician_id": None,
        "technician_name_unconfirmed": None, "is_active": 1, "confirmed_by_owner": 1,
        "source": "test", "notes": None,
    })
    return conn


def _audit(conn, group_id, group_name, message_id, text):
    conn.execute(
        "INSERT INTO whatsapp_outgoing_audit "
        "(message_id, group_id, group_name, whatsapp_timestamp, text, received_at, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (message_id, group_id, group_name, "1794215005", text,
         "2026-11-09T09:03:26+00:00", "2026-11-09T09:03:26+00:00"),
    )
    conn.commit()


def test_owner_posted_job_in_confirmed_intake_group_is_returned(tmp_path):
    conn = _conn(tmp_path)
    _audit(conn, INTAKE_GROUP, "Intake", "m1", JOB_TEXT)

    rows = storage.list_owner_posted_intake_observations(conn)

    assert len(rows) == 1
    assert rows[0]["text"] == JOB_TEXT
    assert rows[0]["group_role"] == "COMPANY_JOB_INTAKE"
    assert rows[0]["group_company"] == "Example Co"
    assert rows[0]["group_mapping_confirmed"] is True


def test_owner_posted_message_in_a_technician_group_stays_audit_only(tmp_path):
    """The load-bearing half: our own forward must never become intake."""
    conn = _conn(tmp_path)
    _audit(conn, TECH_GROUP, "Tech", "m2", JOB_TEXT)

    assert storage.list_owner_posted_intake_observations(conn) == []


def test_an_unconfirmed_intake_group_is_not_trusted(tmp_path):
    conn = _conn(tmp_path)
    storage.upsert_group_registry_entry(conn, {
        "group_id": "unconfirmed@g.us", "exact_name": "Maybe", "role": "COMPANY_JOB_INTAKE",
        "company": "Example Co", "service_area": None, "technician_id": None,
        "technician_name_unconfirmed": None, "is_active": 1, "confirmed_by_owner": 0,
        "source": "test", "notes": None,
    })
    _audit(conn, "unconfirmed@g.us", "Maybe", "m3", JOB_TEXT)

    assert storage.list_owner_posted_intake_observations(conn) == []


def test_a_group_with_no_registry_row_is_not_trusted(tmp_path):
    conn = _conn(tmp_path)
    _audit(conn, "stranger@g.us", "Stranger", "m4", JOB_TEXT)

    assert storage.list_owner_posted_intake_observations(conn) == []


def test_the_row_is_honest_about_having_been_outgoing(tmp_path):
    """Reclassifying it as intake must not falsify how it arrived."""
    conn = _conn(tmp_path)
    _audit(conn, INTAKE_GROUP, "Intake", "m5", JOB_TEXT)

    row = storage.list_owner_posted_intake_observations(conn)[0]

    assert row["direction"] == "outgoing"
    assert row["owner_posted"] is True
