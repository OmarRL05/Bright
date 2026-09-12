from core.agent.decision import DecisionEngine
from core.models import Offer
from core.routing.euclidean import EuclideanDistanceProvider
from core.simulation.state import CourierStateManager

PROVIDER = EuclideanDistanceProvider()

TEC = (25.651, -100.289)
CENTRO = (25.680, -100.310)
APODACA = (25.780, -100.180)


class FakeDemandSignal:
    """DemandSignal de prueba: score fijo, sin depender de zonas reales."""

    def __init__(self, score: float) -> None:
        self.score = score

    def zone_score(self, location: tuple[float, float]) -> float:
        return self.score


def make_offer(offer_id, pickup, dropoff, pay, received_at=0.0, window=(0.0, 10_000.0)):
    return Offer(
        id=offer_id,
        pickup=pickup,
        dropoff=dropoff,
        pay=pay,
        time_window=window,
        received_at=received_at,
    )


def make_engine(shift_duration=480.0, demand_score=0.0):
    manager = CourierStateManager(shift_duration=shift_duration)
    engine = DecisionEngine(
        state_manager=manager,
        distance_provider=PROVIDER,
        demand_signal=FakeDemandSignal(demand_score),
    )
    return manager, engine


def test_accepts_clearly_profitable_offer():
    manager, engine = make_engine()
    offer = make_offer("o1", TEC, CENTRO, pay=200.0)

    decision = engine.evaluate(offer)

    assert decision.accepted is True
    assert "Aceptado" in decision.log
    snapshot = manager.snapshot()
    assert snapshot.version == 1
    assert snapshot.earnings == 200.0
    assert snapshot.backpack == [offer]


def test_rejects_when_pay_per_km_is_below_minimum():
    manager, engine = make_engine()
    offer = make_offer("o1", TEC, APODACA, pay=10.0)  # ~18 km, muy mal pagado

    decision = engine.evaluate(offer)

    assert decision.accepted is False
    assert "Rechazado" in decision.log
    snapshot = manager.snapshot()
    assert snapshot.version == 0
    assert snapshot.earnings == 0.0
    assert snapshot.backpack == []


def test_rejects_when_not_enough_shift_time_remaining():
    manager, engine = make_engine(shift_duration=5.0)
    offer = make_offer("o1", TEC, APODACA, pay=1000.0)  # muy rentable, pero ~54 min de viaje

    decision = engine.evaluate(offer)

    assert decision.accepted is False
    assert "quedan" in decision.reason
    assert manager.snapshot().version == 0


def test_accepts_by_marginal_zero_exception_even_with_low_pay():
    manager, engine = make_engine()

    first = make_offer("o1", TEC, CENTRO, pay=200.0)
    assert engine.evaluate(first).accepted is True

    # o2 recoge y entrega exactamente donde o1 ya termina: desvio ~0.
    second = make_offer("o2", CENTRO, CENTRO, pay=1.0, received_at=100.0)
    decision = engine.evaluate(second)

    assert decision.accepted is True
    assert "desvio ~0" in decision.reason
    snapshot = manager.snapshot()
    assert snapshot.earnings == 201.0
    assert snapshot.version == 2


def test_demand_signal_relaxes_the_threshold():
    distance = PROVIDER.travel_distance(TEC, CENTRO)
    pay = 6.0 * distance  # $/km ~6.0: por debajo del minimo base (8.0)

    cold_manager, cold_engine = make_engine(demand_score=0.0)
    cold_offer = make_offer("o1", TEC, CENTRO, pay=pay)
    assert cold_engine.evaluate(cold_offer).accepted is False

    hot_manager, hot_engine = make_engine(demand_score=1.0)
    hot_offer = make_offer("o1", TEC, CENTRO, pay=pay)
    decision = hot_engine.evaluate(hot_offer)

    assert decision.accepted is True
    assert hot_manager.snapshot().earnings == pay
