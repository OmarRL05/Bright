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
from pathlib import Path

import pytest

from core.models import (
    DEFAULT_ZONE_MAP,
    DistanceMatrix,
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
    def test_default_zone_map_has_sixteen_zones(self):
        """Ampliado de 4 a 16 (ver core.models.ZoneMap docstring): los
        ejemplos ilustrativos del material oficial usan zone_pickup/
        zone_dropoff hasta 11 -- con solo 4 zonas (0-3), esos ids caian
        fuera de nuestro propio universo de zonas."""
        assert len(DEFAULT_ZONE_MAP.zones) == 16

    def test_zone_ids_from_official_examples_are_known(self):
        """decision_response_schema.json / event_log_schema.json usan estos
        ids en sus ejemplos ilustrativos (5, 7, 11)."""
        for zone_id in (5, 7, 11):
            assert DEFAULT_ZONE_MAP.by_id(zone_id) is not None

    def test_by_id_or_none_returns_none_for_unknown_zone(self):
        """Un juez puede mandar cualquier entero de zona -- no debe lanzar."""
        assert DEFAULT_ZONE_MAP.by_id_or_none(9999) is None

    def test_by_id_or_none_returns_zone_when_known(self):
        zone = DEFAULT_ZONE_MAP.by_id_or_none(2)
        assert zone is not None
        assert zone.name == "Centro"

    def test_by_id_raises_on_unknown_zone(self):
        with pytest.raises(KeyError):
            DEFAULT_ZONE_MAP.by_id(9999)

    def test_zone_ids_are_unique(self):
        ids = [z.zone_id for z in DEFAULT_ZONE_MAP.zones]
        assert len(ids) == len(set(ids))

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
        custom = ZoneMap([Zone(0, "A", (1.0, 2.0), 0.8), Zone(1, "B", (3.0, 4.0), 0.2)])
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
# P1.1 — Event log JSONL (8 tipos de evento, contrato oficial)
#
# Corregido tras el merge a B5: el vocabulario interno original (tick,
# offer_received, offer_accepted/offer_rejected, stop_completed, road_event,
# route_optimized) no era ninguno de los 8 tipos de
# student-materials/courier/event_log_schema.json y hacia fallar
# validate_format.py --event-log en cada linea. Estos tests verifican el
# contrato oficial directamente -- test_log_passes_official_validator corre
# el validador real del reto, no una copia.
# ===========================================================================

OFFICIAL_EVENT_TYPES = {
    "shift_start", "order_offered", "decision", "position_update",
    "earnings_update", "shock", "strategy_update", "shift_end",
}


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

    def _lines(self, log_buf) -> list[dict]:
        return [json.loads(l) for l in log_buf.getvalue().splitlines() if l.strip()]

    def test_log_is_valid_jsonl(self):
        log_buf, _ = self._run_engine_with_log()
        for obj in self._lines(log_buf):
            assert "event" in obj
            assert "sim_time" in obj
            assert obj["event"] in OFFICIAL_EVENT_TYPES

    def test_sim_time_is_iso8601(self):
        """El contrato oficial exige datetime ISO 8601, no minutos float."""
        import datetime as dt
        log_buf, _ = self._run_engine_with_log(shift_duration=5.0)
        for obj in self._lines(log_buf):
            dt.datetime.fromisoformat(obj["sim_time"])  # no debe lanzar

    def test_shift_start_is_first_event(self):
        log_buf, _ = self._run_engine_with_log(shift_duration=5.0)
        first = self._lines(log_buf)[0]
        assert first["event"] == "shift_start"
        for key in ("seed", "shift_hours", "vehicle", "start_location_zone", "shift_end_time"):
            assert key in first

    def test_shift_end_event_last(self):
        log_buf, _ = self._run_engine_with_log(shift_duration=5.0)
        last = self._lines(log_buf)[-1]
        assert last["event"] == "shift_end"

    def test_order_offered_events_present_with_required_fields(self):
        """Con seed 42 y 60 min hay al menos una oferta generada."""
        log_buf, _ = self._run_engine_with_log(shift_duration=60.0)
        order_offered = [obj for obj in self._lines(log_buf) if obj["event"] == "order_offered"]
        assert order_offered
        first = order_offered[0]
        for key in ("order_id", "zone_pickup", "zone_dropoff", "distance_pickup_km",
                    "distance_delivery_km", "base_pay_mxn", "surge_multiplier", "vehicle"):
            assert key in first
        assert isinstance(first["zone_pickup"], int)
        assert isinstance(first["zone_dropoff"], int)

    def test_shock_events_have_shock_type(self):
        """Con seed 3 y 200 min se generan varios road events (shock)."""
        log_buf, _ = self._run_engine_with_log(seed=3, shift_duration=200.0)
        shocks = [obj for obj in self._lines(log_buf) if obj["event"] == "shock"]
        assert shocks
        for shock in shocks:
            assert "shock_type" in shock
            assert shock["shock_type"] in {"surge", "closure", "rain", "delay"}

    def test_log_offer_decision_emits_decision_event(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=1, shift_duration=1.0, log_file=log_buf)
        offer = make_offer()
        engine.log_offer_decision(offer, accepted=True, reason="test reason", latency_ms=2.5)
        objs = self._lines(log_buf)
        assert len(objs) == 1  # log_offer_decision no pasa por event_stream(), no hay shift_start
        decision = objs[-1]
        assert decision["event"] == "decision"
        assert decision["order_id"] == offer.id
        assert decision["decision"] == "ACCEPT"
        assert decision["reason"] == "test reason"
        assert decision["latency_ms"] == 2.5

    def test_log_offer_decision_rejected_uses_skip(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=1, shift_duration=1.0, log_file=log_buf)
        offer = make_offer()
        engine.log_offer_decision(offer, accepted=False, reason="zona fria")
        decision = self._lines(log_buf)[-1]
        assert decision["decision"] == "SKIP"

    def test_no_log_when_no_file(self):
        """Sin log_file configurado no debe lanzar ninguna excepción."""
        engine = SimulationEngine(seed=42, shift_duration=5.0, log_file=None)
        list(engine.event_stream())
        # El hecho de completar sin error es el assert

    def test_agent_id_propagated(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=42, shift_duration=5.0, log_file=log_buf, agent_id="baseline")
        list(engine.event_stream())
        for obj in self._lines(log_buf):
            assert obj["agent_id"] == "baseline"

    def test_log_passes_official_validator(self, tmp_path):
        """Corre el validador REAL del reto (no una copia) contra un log generado.

        Es la garantia fuerte de que el formato es correcto -- no solo que
        "parece" correcto segun nuestra propia lectura del schema.
        """
        import subprocess
        import sys

        log_buf, _ = self._run_engine_with_log(seed=7, shift_duration=120.0)
        log_path = tmp_path / "shift.jsonl"
        log_path.write_text(log_buf.getvalue())

        validator = (
            Path(__file__).resolve().parents[2] / "student-materials" / "courier" / "validate_format.py"
        )
        assert validator.exists(), f"no se encontro el validador oficial en {validator}"

        result = subprocess.run(
            [sys.executable, str(validator), "--event-log", str(log_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr


# ===========================================================================
# Shocks con efecto economico — un surge debe subir el pago de las ofertas
# siguientes en su zona, no solo aparecer en el log sin consecuencia.
# ===========================================================================

def _offer_with_surges(seed: int, surges: dict[int, tuple[float, float]] | None = None):
    """Primera oferta de un turno con `seed`, opcionalmente bajo surge.

    Comparar contra una corrida de CONTROL con la misma seed es lo que hace
    estos tests robustos: el RNG real produce la misma secuencia en las dos,
    asi que cualquier diferencia entre las dos ofertas viene del surge y de
    nada mas. La alternativa -- un RNG de mentira que devuelva valores fijos --
    tiene que replicar a mano el orden exacto de extracciones del generador, y
    se rompe en silencio en cuanto alguien agrega un campo.
    """
    engine = SimulationEngine(seed=seed, shift_duration=60.0)
    if surges:
        engine._active_surges.update(surges)
    return engine, engine._generate_offer()


class TestShockEconomics:
    """Un `shock` de surge tiene que mover dinero, no solo escribir una linea.

    El brief exige al menos un shock en vivo durante la demo; si el evento se
    emite y las ofertas siguientes salen igual, no hay nada que enseñar.
    """

    def test_surge_sube_el_multiplicador_de_su_zona(self):
        control_engine, control = _offer_with_surges(seed=1)
        zona = control.zone_pickup

        _, surgida = _offer_with_surges(seed=1, surges={zona: (2.2, 30.0)})

        assert surgida.zone_pickup == zona          # misma secuencia de RNG
        assert surgida.surge_multiplier == 2.2
        assert surgida.surge_multiplier > control.surge_multiplier

    def test_el_surge_no_se_cuenta_dos_veces(self):
        """La tarifa base NO cambia: el surge vive en `surge_multiplier`.

        Toda la economia aguas abajo calcula `base_pay * surge + propina`. Si
        el generador multiplicara tambien `pay`, el surge entraria dos veces y
        el evento `order_offered` mentiria -- el contrato oficial pide los dos
        campos por separado, no premultiplicados.
        """
        _, control = _offer_with_surges(seed=1)
        _, surgida = _offer_with_surges(seed=1, surges={control.zone_pickup: (2.2, 30.0)})

        assert surgida.pay == control.pay

    def test_surge_no_toca_otras_zonas(self):
        _, control = _offer_with_surges(seed=1)
        otra = next(z.zone_id for z in DEFAULT_ZONE_MAP.zones if z.zone_id != control.zone_pickup)

        _, ajena = _offer_with_surges(seed=1, surges={otra: (2.2, 30.0)})

        assert ajena.surge_multiplier == control.surge_multiplier
        assert ajena.pay == control.pay

    def test_surge_de_zona_y_de_oferta_no_se_apilan(self):
        """Son la misma senal por dos vias. Multiplicarlas daria 2.2 x 2.0 =
        4.4x, que no es un turno sino un premio: se toma el mayor."""
        _, control = _offer_with_surges(seed=1)
        _, surgida = _offer_with_surges(seed=1, surges={control.zone_pickup: (1.5, 30.0)})

        assert surgida.surge_multiplier == max(control.surge_multiplier, 1.5)

    def test_surge_expira_por_tiempo_de_simulacion(self):
        engine = SimulationEngine(seed=1, shift_duration=60.0)
        zona = DEFAULT_ZONE_MAP.zones[0]
        engine._active_surges[zona.zone_id] = (1.5, 5.0)

        engine.current_time = 4.0
        assert engine._active_surge_multiplier(zona.zone_id) == 1.5

        engine.current_time = 5.0      # ventana semiabierta: a los 5 ya no
        assert engine._active_surge_multiplier(zona.zone_id) == 1.0
        # Y la entrada caducada se borra al consultarla, sin barrido aparte.
        assert zona.zone_id not in engine._active_surges

    def test_el_log_separa_tarifa_y_multiplicador(self):
        log_buf = io.StringIO()
        engine = SimulationEngine(seed=1, shift_duration=60.0, log_file=log_buf)
        control = SimulationEngine(seed=1, shift_duration=60.0)._generate_offer()
        engine._active_surges[control.zone_pickup] = (1.5, 30.0)

        offer = engine._generate_offer()
        engine._log_order_offered(offer)

        obj = json.loads(log_buf.getvalue().strip())
        assert obj["surge_multiplier"] == 1.5
        assert obj["base_pay_mxn"] == pytest.approx(control.pay)  # sin premultiplicar


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
