from core.models import Offer, RouteStop
from core.simulation.state import CourierStateManager


def make_offer(offer_id: str = "o1") -> Offer:
    return Offer(
        id=offer_id,
        pickup=(25.67, -100.31),
        dropoff=(25.68, -100.30),
        pay=45.0,
        time_window=(0.0, 30.0),
        received_at=0.0,
    )


def test_snapshot_is_read_only_copy():
    manager = CourierStateManager(shift_duration=480.0)
    snap = manager.snapshot()
    snap.backpack.append(make_offer())
    assert manager.snapshot().backpack == []


def test_accept_offer_increments_version_and_earnings():
    manager = CourierStateManager(shift_duration=480.0)
    offer = make_offer()
    route = [RouteStop(offer_id=offer.id, kind="pickup", eta=5.0)]

    new_version = manager.accept_offer(offer, route)

    snap = manager.snapshot()
    assert new_version == 1
    assert snap.version == 1
    assert snap.earnings == 45.0
    assert snap.backpack == [offer]


def test_apply_optimized_route_discarded_on_stale_version():
    manager = CourierStateManager(shift_duration=480.0)
    stale_version = manager.snapshot().version
    manager.accept_offer(make_offer(), [])  # version avanza a 1

    applied = manager.apply_optimized_route(stale_version, [RouteStop("x", "pickup", 1.0)])

    assert applied is False
    assert manager.snapshot().version == 1
