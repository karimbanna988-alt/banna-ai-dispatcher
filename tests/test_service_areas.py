from dispatcher.rules.service_areas import find_service_area, load_service_areas


def test_loads_real_service_areas_file():
    areas = load_service_areas()
    assert any(a.city == "Charlotte" and a.state == "NC" for a in areas)
    assert any(a.city is None and a.state == "DE" for a in areas)


def test_confirmed_status_requires_both_groups():
    # Every current entry has no dispatch/closing group confirmed yet.
    areas = load_service_areas()
    lookup = find_service_area("Charlotte", "NC", areas)
    assert lookup.status == "unconfirmed"
    assert lookup.dispatch_group is None


def test_unknown_city_is_not_found():
    areas = load_service_areas()
    lookup = find_service_area("Nowhereville", "ZZ", areas)
    assert lookup.status == "not_found"


def test_whole_state_entry_matches_on_state_alone():
    areas = load_service_areas()
    lookup = find_service_area(None, "DE", areas)
    assert lookup.status == "unconfirmed"
    assert lookup.record is not None
    assert lookup.record.state == "DE"


def test_inactive_area_is_reported_as_inactive():
    from dispatcher.rules.service_areas import ServiceAreaRecord

    areas = [ServiceAreaRecord(city="Testville", state="TS", dispatch_group="TestGroup", closing_group="TestClose", active=False)]
    lookup = find_service_area("Testville", "TS", areas)
    assert lookup.status == "inactive"


def test_confirmed_area_with_both_groups_set():
    from dispatcher.rules.service_areas import ServiceAreaRecord

    areas = [ServiceAreaRecord(city="Testville", state="TS", dispatch_group="TestGroup", closing_group="TestClose")]
    lookup = find_service_area("Testville", "TS", areas)
    assert lookup.status == "confirmed"
    assert lookup.dispatch_group == "TestGroup"
    assert lookup.closing_group == "TestClose"
