from dispatcher.rules.sla_config import ACKNOWLEDGMENT_WARNING_SECONDS, DISPATCH_URGENT_SECONDS, format_age, urgency_level


def test_fresh_job_is_attention():
    assert urgency_level(0, acknowledged=False, dispatched=False) == "attention"


def test_unacknowledged_past_warning_threshold_is_warning():
    assert urgency_level(ACKNOWLEDGMENT_WARNING_SECONDS + 1, acknowledged=False, dispatched=False) == "warning"


def test_acknowledged_before_urgent_threshold_stays_attention():
    assert urgency_level(ACKNOWLEDGMENT_WARNING_SECONDS + 1, acknowledged=True, dispatched=False) == "attention"


def test_past_urgent_threshold_is_urgent_regardless_of_ack():
    assert urgency_level(DISPATCH_URGENT_SECONDS + 1, acknowledged=True, dispatched=False) == "urgent"
    assert urgency_level(DISPATCH_URGENT_SECONDS + 1, acknowledged=False, dispatched=False) == "urgent"


def test_dispatched_always_wins():
    assert urgency_level(9999, acknowledged=False, dispatched=True) == "dispatched"


def test_format_age_seconds():
    assert format_age(18) == "18 sec"


def test_format_age_minutes_and_seconds():
    assert format_age(72) == "1 min 12 sec"


def test_format_age_exact_minutes():
    assert format_age(120) == "2 min"


def test_format_age_never_negative():
    assert format_age(-5) == "0 sec"
