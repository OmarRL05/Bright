"""Tests de Abraham: P0.1, P0.2, P0.5, P0.6, P1.1.

Cubre:
- ZoneMap y DistanceMatrix (P0.2)
- VehicleProfile (P0.5)
- CourierStateManager.tick() con posicion, retiro de paradas y sim_time (P0.1)
- shift_ends_before_route() (P0.1)
- validate_windows() tras recalcular ETAs — Hallazgo 1 (P0.6)
- EventLog JSONL: los 8 tipos de evento (P1.1)
"""

from __future__ import annotations

import io
import json

import pytest

from core.models import (
    DEFAULT_ZONE_MAP,
    DistanceMatrix,
    EventType,
    Offer,
    RouteStop,
    VehicleProfile,
    VehicleType,
    VEHICLE_PROFILES,
    Zone,
    ZoneMap,
)
from core.simulation.engine import SimulationEngine
from core.simulation.state import CourierStateManager, StaleWindowOffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_offer(
    offer_id: str = "o1",
    pickup: tuple[float, float] = (25.651, -100.289),   # Tec
    dropoff: tuple[float, float] = (25.680, -100.310),  # Centro
    pay: float = 80.0,
    time_window: tuple[float, float] = (0.0, 60.0),
    received_at: float = 0.0,
) -> Offer:
    return Offer(
        id=offer_id,
        pickup=pickup,
        dropoff=dropoff,
        pay=pay,
        time_window=time_window,
        received_at=received_at,
    )


def make_manager(
    shift_duration: float = 480.0,
    vehicle_type: VehicleType = VehicleType.MOTO,
    start_position: tuple[float, float] = (25.680, -100.310),
) -> CourierStateManager:
    return CourierStateManager(
        shift_duration=shift_duration,
        start_position=start_position,
        vehicle_type=vehicle_type,
    )


# ===========================================================================
# P0.2 — ZoneMap
# ===========================================================================

class TestZoneMap:
    def test_default_zone_map_has_four_zones(self):
        assert len(DEFAULT_ZONE_MAP.zones) == 4

    def test_nearest_zone_returns_exact_match(self):
        tec_coord = (25.651, -100.289)
        zone = DEFAULT_ZONE_MAP.nearest_zone(tec_coord)
        assert zone.name == "Tec"

    def test_nearest_zone_snaps_to_closest(self):
        # Coordenada entre Tec y San Pedro, más cerca de Tec
        coord = (25.652, -100.300)
        zone = DEFAULT_ZONE_MAP.nearest_zone(coord)
        assert zone.name == "Tec"

    def test_zone_by_name_found(self):
        zone = DEFAULT_ZONE_MAP.zone_by_name("Centro")
        assert zone.demand_score == 0.9

    def test_zone_by_name_raises_on_unknown(self):
        with pytest.raises(KeyError):
            DEFAULT_ZONE_MAP.zone_by_name("ZonaInexistente")

    def test_coords_property_length(self):
        assert len(DEFAULT_ZONE_MAP.coords) == len(DEFAULT_ZONE_MAP.zones)

    def test_custom_zones(self):
        custom = ZoneMap([Zone("A", (1.0, 2.0), 0.8), Zone("B", (3.0, 4.0), 0.2)])
        assert custom.nearest_zone((1.1, 2.1)).name == "A"
        assert custom.nearest_zone((2.9, 3.9)).name == "B"


# ===========================================================================
# P0.2 — DistanceMatrix
# ===========================================================================

class _FakeDistanceProvider:
    """Provider de distancias sintético para tests."""
    def travel_time(self, a, b):
        dx = abs(a[0] - b[0])
        dy = abs(a[1] - b[1])
        return (dx + dy) * 100.0

    def travel_distance(self, a, b):
        dx = abs(a[0] - b[0])
        dy = abs(a[1] - b[1])
        return (dx + dy) * 10.0


class TestDistanceMatrix:
    def test_build_symmetric(self):
        coords = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        provider = _FakeDistanceProvider()
        matrix = DistanceMatrix.build(coords, provider)
        # Diagonal debe ser 0
        for i, c in enumerate(coords):
            assert matrix.travel_time(c, c) == 0.0

    def test_lookup_known_pair(self):
        coords = [(25.651, -100.289), (25.680, -100.310)]
        provider = _FakeDistanceProvider()
        matrix = DistanceMatrix.build(coords, provider)
        expected = provider.travel_time(coords[0], coords[1])
        assert matrix.travel_time(coords[0], coords[1]) == pytest.approx(expected)

    def test_snap_to_nearest_coord(self):
        """Una coordenada no registrada se resuelve al índice más cercano."""
        coords = [(25.651, -100.289), (25.680, -100.310)]
        provider = _FakeDistanceProvider()
        matrix = DistanceMatrix.build(coords, provider)
        # Coord muy cerca de coords[0] — debe snappear ahí
        near_0 = (25.6511, -100.2891)
        assert matrix.travel_time(near_0, coords[1]) == pytest.approx(
            matrix.travel_time(coords[0], coords[1])
        )


# ===========================================================================
# P0.5 — VehicleProfile
# ===========================================================================

class TestVehicleProfile:
    def test_all_vehicle_types_defined(self):
        for vt in VehicleType:
            assert vt in VEHICLE_PROFILES

    def test_moto_faster_than_car(self):
        assert VEHICLE_PROFILES[VehicleType.MOTO].avg_speed_kmh > \
               VEHICLE_PROFILES[VehicleType.CAR].avg_speed_kmh

    def test_bike_cheapest_per_km(self):
        assert VEHICLE_PROFILES[VehicleType.BIKE].cost_per_km < \
               VEHICLE_PROFILES[VehicleType.MOTO].cost_per_km < \
               VEHICLE_PROFILES[VehicleType.CAR].cost_per_km

    def test_car_max_backpack_greater_than_moto(self):
        assert VEHICLE_PROFILES[VehicleType.CAR].max_backpack > \
               VEHICLE_PROFILES[VehicleType.MOTO].max_backpack

    def test_min_per_km(self):
        moto = VEHICLE_PROFILES[VehicleType.MOTO]
        assert moto.min_per_km == pytest.approx(60.0 / moto.avg_speed_kmh)

    def test_vehicle_profile_frozen(self):
        """VehicleProfile es inmutable (frozen=True)."""
        moto = VEHICLE_PROFILES[VehicleType.MOTO]
        with pytest.raises(Exception):
            moto.avg_speed_kmh = 999.0  # type: ignore[misc]

    def test_snapshot_includes_vehicle(self):
        manager = make_manager(vehicle_type=VehicleType.CAR)
        snap = manager.snapshot()
        assert snap.vehicle.type == VehicleType.CAR


# ===========================================================================
# P0.1 — tick(): sim_time, position, retiro de paradas
# ===========================================================================

class TestTick:
    def test_tick_decrements_time_remaining(self):
        manager = make_manager(shift_duration=480.0)
        manager.tick(10.0)
        assert manager.snapshot().time_remaining == pytest.approx(470.0)

    def test_tick_advances_sim_time(self):
        manager = make_manager(shift_duration=480.0)
        manager.tick(5.0)
        manager.tick(5.0)
        assert manager.snapshot().sim_time == pytest.approx(10.0)

    def test_tick_does_not_go_below_zero(self):
        manager = make_manager(shift_duration=10.0)
        manager.tick(20.0)
        assert manager.snapshot().time_remaining == 0.0

    def test_tick_removes_completed_stops(self):
        """Paradas con ETA <= sim_time se retiran automáticamente en tick()."""
        manager = make_manager(shift_duration=480.0)
        offer = make_offer()
        route = [
            RouteStop(offer_id="o1", kind="pickup", eta=5.0),    # se completará en t=6
            RouteStop(offer_id="o1", kind="dropoff", eta=20.0),
        ]
        manager.accept_offer(offer, route)

        manager.tick(6.0)  # avanza a sim_time=6; la parada de eta=5 queda atrás

        snap = manager.snapshot()
        assert len(snap.route) == 1
        assert snap.route[0].kind == "dropoff"

    def test_tick_updates_position_to_origin_when_no_route(self):
        """Sin ruta, la posición no cambia."""
        start = (25.680, -100.310)
        manager = make_manager(start_position=start)
        manager.tick(10.0)
        assert manager.snapshot().position == start

    def test_tick_interpolates_position(self):
        """Con ruta activa, la posición se mueve hacia la siguiente parada."""
        start = (25.651, -100.289)   # Tec
        pickup = (25.680, -100.310)  # Centro
        manager = make_manager(start_position=start)

        offer = make_offer(pickup=pickup, dropoff=pickup)
        # ETA del pickup: 10 minutos desde el inicio
        route = [RouteStop(offer_id="o1", kind="pickup", eta=10.0)]
        manager.accept_offer(offer, route)

        # Avanzar la mitad del camino (5 minutos de 10)
        manager.tick(5.0)
        pos = manager.snapshot().position

        # La posición debe estar entre start y pickup
        assert start[0] < pos[0] < pickup[0] or pos[0] == pytest.approx(
            start[0] + 0.5 * (pickup[0] - start[0]), rel=0.01
        )


# ===========================================================================
# P0.1 — shift_ends_before_route()
# ===========================================================================

class TestShiftEndsBeforeRoute:
    def test_no_route_returns_false(self):
        manager = make_manager(shift_duration=480.0)
        assert not manager.shift_ends_before_route()

    def test_returns_false_when_route_fits(self):
        manager = make_manager(shift_duration=480.0)
        offer = make_offer()
        route = [RouteStop(offer_id="o1", kind="dropoff", eta=100.0)]
        manager.accept_offer(offer, route)
        # time_remaining=480, shift_end_at=0+480=480 >= eta=100 → cabe
        assert not manager.shift_ends_before_route()

    def test_returns_true_when_route_doesnt_fit(self):
        manager = make_manager(shift_duration=30.0)
        offer = make_offer()
        route = [RouteStop(offer_id="o1", kind="dropoff", eta=50.0)]
        manager.accept_offer(offer, route)
        # time_remaining=30, shift_end_at=0+30=30 < eta=50 → no cabe
        assert manager.shift_ends_before_route()

    def test_accounts_for_elapsed_time(self):
        manager = make_manager(shift_duration=60.0)
        offer = make_offer()
        route = [RouteStop(offer_id="o1", kind="dropoff", eta=55.0)]
        manager.accept_offer(offer, route)

        manager.tick(30.0)  # sim_time=30, time_remaining=30, shift_end_at=60

        # eta=55 < shift_end_at=60 → cabe justo
        assert not manager.shift_ends_before_route()

        manager.tick(10.0)  # sim_time=40, time_remaining=20, shift_end_at=60 — sigue cabiendo
        assert not manager.shift_ends_before_route()

        manager.tick(10.0)  # sim_time=50, time_remaining=10, shift_end_at=60 — eta=55 < 60 → cabe
        assert not manager.shift_ends_before_route()

        # Añadir parada fuera del turno
        offer2 = make_offer("o2")
        route2 = [RouteStop(offer_id="o2", kind="dropoff", eta=75.0)]
        manager.accept_offer(offer2, route2)
        assert manager.shift_ends_before_route()


# ===========================================================================
# P0.6 — validate_windows() — Hallazgo 1
# ===========================================================================

class TestValidateWindows:
    def test_no_stale_when_windows_ok(self):
        manager = make_manager(shift_duration=480.0)
        offer = make_offer(time_window=(0.0, 60.0))
        route = [RouteStop(offer_id="o1", kind="dropoff", eta=30.0)]
        manager.accept_offer(offer, route)
        stale = manager.validate_windows()
        assert stale == []

    def test_detects_stale_dropoff(self):
        """Una parada con eta > window_end se detecta como infeasible."""
        manager = make_manager(shift_duration=480.0)
        offer = make_offer(time_window=(0.0, 20.0))  # ventana cierra en t=20
        route = [RouteStop(offer_id="o1", kind="dropoff", eta=25.0)]  # eta=25 > 20
        manager.accept_offer(offer, route)

        stale = manager.validate_windows()

        assert len(stale) == 1
        assert stale[0].offer.id == "o1"
        assert stale[0].stop_kind == "dropoff"
        assert stale[0].actual_eta == 25.0
        assert stale[0].window_end == 20.0

    def test_tick_triggers_window_validation(self):
        """tick() valida ventanas automáticamente y devuelve las infeasible."""
        manager = make_manager(shift_duration=480.0)
        offer = make_offer(time_window=(0.0, 10.0))
        # La parada tiene ETA=15 — ya es infeasible desde el inicio,
        # pero tick() debe detectarlo y devolverlo.
        route = [RouteStop(offer_id="o1", kind="dropoff", eta=15.0)]
        manager.accept_offer(offer, route)

        stale = manager.tick(1.0)

        assert any(s.offer.id == "o1" for s in stale)

    def test_multiple_stale_offers(self):
        manager = make_manager(shift_duration=480.0)

        offer1 = make_offer("o1", time_window=(0.0, 5.0))
        offer2 = make_offer("o2", time_window=(0.0, 30.0))
        route = [
            RouteStop("o1", "dropoff", eta=10.0),   # infeasible
            RouteStop("o2", "dropoff", eta=20.0),   # ok
        ]
        manager.accept_offer(offer1, [])
        manager.accept_offer(offer2, route)

        stale = manager.validate_windows()
        stale_ids = {s.offer.id for s in stale}
        assert "o1" in stale_ids
        assert "o2" not in stale_ids


# ===========================================================================
# P1.1 — Event log JSONL (8 tipos de evento)
# ===========================================================================

class TestEventLog:
    def _run_engine_with_log(self, seed: int = 42, shift_duration: float = 30.0):
        log_buf = io.StringIO()
        engine = SimulationEngine(
            seed=seed,
            shift_duration=shift_duration,
            log_file=log_buf,
        )
        events = list(engine.event_stream())
        return log_buf, events

    def test_log_is_valid_jsonl(self):
        log_buf, _ = self._run_engine_with_log()
        lines = [l for l in log_buf.getvalue().splitlines() if l.strip()]
        for line in lines:
            obj = json.loads(line)  # no debe lanzar
            assert "event_type" in obj
            assert "sim_time" in obj
            assert "payload" in obj
            assert "agent_id" in obj

    def test_tick_events_present(self):
        log_buf, _ = self._run_engine_with_log(shift_duration=5.0)
        types = {json.loads(l)["event_type"] for l in log_buf.getvalue().splitlines() if l.strip()}
        assert EventType.TICK.value in types

    def test_shift_end_event_last(self):
        log_buf, _ = self._run_engine_with_log(shift_duration=5.0)
        lines = [l for l in log_buf.getvalue().splitlines() if l.strip()]
        last = json.loads(lines[-1])
        assert last["event_type"] == EventType.SHIFT_END.value

    def test_offer_received_events_present(self):
        """Con seed 42 y 60 min hay al menos una oferta generada."""
        log_buf, events = self._run_engine_with_log(shift_duration=60.0)
        types = [json.loads(l)["event_type"] for l in log_buf.getvalue().splitlines() if l.strip()]
        assert EventType.OFFER_RECEIVED.value in types

    def test_offer_received_payload_fields(self):
        log_buf, _ = self._run_engine_with_log(shift_duration=60.0)
        for line in log_buf.getvalue().splitlines():
            obj = json.loads(line)
            if obj["event_type"] == EventType.OFFER_RECEIVED.value:
                assert "offer_id" in obj["payload"]
                assert "pay" in obj["payload"]
                assert "pickup" in obj["payload"]
                assert "demand_percentile" in obj["payload"]
                break  # suficiente con el primero

    def test_log_offer_decision_accepted(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=1, shift_duration=1.0, log_file=log_buf)
        offer = make_offer()
        engine.log_offer_decision(offer, accepted=True, reason="test reason")
        lines = [l for l in log_buf.getvalue().splitlines() if l.strip()]
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["event_type"] == EventType.OFFER_ACCEPTED.value
        assert obj["payload"]["reason"] == "test reason"

    def test_log_offer_decision_rejected(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=1, shift_duration=1.0, log_file=log_buf)
        offer = make_offer()
        engine.log_offer_decision(offer, accepted=False, reason="zona fria")
        obj = json.loads(log_buf.getvalue().strip())
        assert obj["event_type"] == EventType.OFFER_REJECTED.value

    def test_log_stop_completed(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=1, shift_duration=1.0, log_file=log_buf)
        engine.log_stop_completed("o1", "pickup")
        obj = json.loads(log_buf.getvalue().strip())
        assert obj["event_type"] == EventType.STOP_COMPLETED.value
        assert obj["payload"]["kind"] == "pickup"

    def test_log_route_optimized(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=1, shift_duration=1.0, log_file=log_buf)
        engine.log_route_optimized(route_length=4)
        obj = json.loads(log_buf.getvalue().strip())
        assert obj["event_type"] == EventType.ROUTE_OPTIMIZED.value
        assert obj["payload"]["route_stops"] == 4

    def test_no_log_when_no_file(self):
        """Sin log_file configurado no debe lanzar ninguna excepción."""
        engine = SimulationEngine(seed=42, shift_duration=5.0, log_file=None)
        events = list(engine.event_stream())
        # El hecho de completar sin error es el assert

    def test_agent_id_propagated(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=42, shift_duration=5.0, log_file=log_buf, agent_id="baseline")
        list(engine.event_stream())
        for line in log_buf.getvalue().splitlines():
            if line.strip():
                obj = json.loads(line)
                assert obj["agent_id"] == "baseline"


# ===========================================================================
# Retrocompatibilidad — tests existentes no deben romperse
# ===========================================================================

class TestRetrocompat:
    """Verifica que los cambios de v2 no rompen los tests originales."""

    def test_same_seed_generates_same_events(self):
        e1 = SimulationEngine(seed=42, shift_duration=60)
        e2 = SimulationEngine(seed=42, shift_duration=60)
        assert list(e1.event_stream()) == list(e2.event_stream())

    def test_snapshot_is_read_only_copy(self):
        from core.models import Offer
        manager = CourierStateManager(shift_duration=480.0)
        snap = manager.snapshot()
        snap.backpack.append(make_offer())
        assert manager.snapshot().backpack == []

    def test_accept_offer_increments_version_and_earnings(self):
        manager = CourierStateManager(shift_duration=480.0)
        offer = make_offer()
        route = [RouteStop(offer_id=offer.id, kind="pickup", eta=5.0)]
        new_version = manager.accept_offer(offer, route)
        snap = manager.snapshot()
        assert new_version == 1
        assert snap.earnings == 80.0
        assert snap.backpack == [offer]

    def test_apply_optimized_route_discarded_on_stale_version(self):
        manager = CourierStateManager(shift_duration=480.0)
        stale_version = manager.snapshot().version
        manager.accept_offer(make_offer(), [])
        applied = manager.apply_optimized_route(stale_version, [RouteStop("x", "pickup", 1.0)])
        assert applied is False
