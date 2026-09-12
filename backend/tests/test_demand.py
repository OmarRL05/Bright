from core.agent.demand import StaticDemandSignal

TEC = (25.651, -100.289)
SAN_PEDRO = (25.657, -100.402)
CENTRO = (25.680, -100.310)
APODACA = (25.780, -100.180)


def test_known_zones_return_score_in_valid_range():
    signal = StaticDemandSignal()
    for zone in (TEC, SAN_PEDRO, CENTRO, APODACA):
        score = signal.zone_score(zone)
        assert 0.0 <= score <= 1.0


def test_centro_is_hotter_than_apodaca():
    signal = StaticDemandSignal()
    assert signal.zone_score(CENTRO) > signal.zone_score(APODACA)


def test_nearby_point_snaps_to_nearest_known_zone():
    signal = StaticDemandSignal()
    near_centro = (25.681, -100.311)  # ~150m de Centro, lejos de las demas zonas
    assert signal.zone_score(near_centro) == signal.zone_score(CENTRO)


def test_score_is_deterministic():
    signal = StaticDemandSignal()
    assert signal.zone_score(TEC) == signal.zone_score(TEC)
