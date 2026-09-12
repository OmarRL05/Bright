from core.routing.euclidean import EuclideanDistanceProvider

TEC = (25.651, -100.289)
SAN_PEDRO = (25.657, -100.402)


def test_distance_is_zero_for_same_point():
    provider = EuclideanDistanceProvider()
    assert provider.travel_distance(TEC, TEC) == 0.0
    assert provider.travel_time(TEC, TEC) == 0.0


def test_distance_is_symmetric():
    provider = EuclideanDistanceProvider()
    assert provider.travel_distance(TEC, SAN_PEDRO) == provider.travel_distance(SAN_PEDRO, TEC)


def test_distance_is_reasonable_for_monterrey_zones():
    provider = EuclideanDistanceProvider()
    distance_km = provider.travel_distance(TEC, SAN_PEDRO)
    # Tec y San Pedro estan a ~10-15 km en linea recta, no al otro lado del pais.
    assert 5.0 < distance_km < 25.0


def test_travel_time_scales_with_speed():
    slow = EuclideanDistanceProvider(avg_speed_kmh=10.0)
    fast = EuclideanDistanceProvider(avg_speed_kmh=20.0)

    assert slow.travel_time(TEC, SAN_PEDRO) == fast.travel_time(TEC, SAN_PEDRO) * 2
