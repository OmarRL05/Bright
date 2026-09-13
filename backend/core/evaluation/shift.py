"""Corredor de turnos: el loop completo que hasta ahora solo existia en un test.

Toma el stream reproducible de `SimulationEngine`, se lo presenta a una
politica **con la forma exacta del contrato oficial** (`DecideRequest`, la
misma clase que recibe `POST /decide`), simula al repartidor haciendo el
trabajo que acepto, y devuelve las metricas del turno.

Por que la politica ve un `DecideRequest` y no un `Offer`
---------------------------------------------------------
Porque si el arnes midiera algo distinto de lo que el endpoint decide,
mediriamos un sistema que no es el que los jueces van a probar. Con esta
forma, `OurAgent` corre **exactamente** el mismo `evaluate_safety_full` +
`evaluate_economics` + `combine` que `api/decide.py`, y no una reimplementacion
parecida.

El modelo del repartidor, explicito
------------------------------------
Un repartidor, secuencial: hace un pedido tras otro. `busy_until` es cuando
termina todo lo aceptado y `position_zone` es donde queda al terminarlo. Una
oferta que llega mientras esta ocupado se evalua con esa posicion futura y con
`queue_offset_min = busy_until - ahora` -- que es precisamente lo que la
constraint de fin de turno necesita para ver la ruta combinada y no el pedido
suelto.

Tres decisiones de modelado que cambian los numeros, y por que
---------------------------------------------------------------

**1. Las ganancias se cuentan al ENTREGAR, no al aceptar.** Es el hallazgo 4
de la auditoria. Contar al aceptar mete en el total pedidos que nunca se
entregaron o que llegaron tarde, y cualquier comparacion contra un baseline
construida sobre ese numero es invalida.

**2. El pago es neto de combustible**, con el `cost_per_km` del perfil, sobre
el kilometraje completo (traslado en vacio + entrega). Sin eso el deadhead
sale gratis y las tres politicas de vehiculo se vuelven indistinguibles.

**3. La pausa obligatoria se TOMA.** Cuando el repartidor acumula 4 horas
continuas y esta libre, para 20 minutos y el contador se reinicia. Si esta a
media entrega no puede parar, asi que la constraint rechaza ofertas hasta que
termine -- que es la unica lectura realista, y ademas es lo que hace que la
constraint se dispare de verdad en un turno completo en vez de quedar como
codigo que nadie ejecuta.

Las violaciones de seguridad se miden aparte de lo que decide la politica: a
cada pedido ACEPTADO se le corre el gate con el estado que habia al decidir. Un
baseline que ignora la seguridad acumula violaciones; el agente tiene cero por
construccion. Esa columna es el punto entero de la comparacion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import IO, Any

from api.schemas import CourierStateOverrides, DecideRequest
from core.agent.safety import evaluate_safety_full
from core.models import (
    DEFAULT_ZONE_MAP,
    VEHICLE_PROFILES,
    DistanceMatrix,
    EventType,
    Offer,
    VehicleProfile,
    VehicleType,
    ZoneMap,
)
from core.routing.euclidean import EuclideanDistanceProvider
from core.simulation.engine import DEFAULT_SHIFT_START_TIME, SimulationEngine

#: Duracion de la pausa que el repartidor toma cuando se dispara la constraint.
#: Debe coincidir con `safety.MANDATORY_BREAK_DURATION_MIN`; se importa de ahi
#: para que no puedan desincronizarse.
from core.agent.safety import (  # noqa: E402  (import agrupado a proposito)
    HEAT_RULE_END_HOUR,
    HEAT_RULE_MAX_CONTINUOUS_MIN,
    HEAT_RULE_START_HOUR,
    MANDATORY_BREAK_AFTER_MIN,
    MANDATORY_BREAK_DURATION_MIN,
)


@dataclass(frozen=True)
class ShiftConfig:
    """La configuracion de turno que el protocolo define (seccion 1)."""

    seed: int
    shift_hours: float = 8.0
    vehicle: str = "moto"
    start_location_zone: int = 0
    shift_start_time: datetime = DEFAULT_SHIFT_START_TIME

    @property
    def shift_minutes(self) -> float:
        return self.shift_hours * 60.0

    @property
    def shift_end_time(self) -> datetime:
        return self.shift_start_time + timedelta(minutes=self.shift_minutes)

    @property
    def profile(self) -> VehicleProfile:
        return VEHICLE_PROFILES[VehicleType(self.vehicle)]


@dataclass
class ShiftState:
    """Estado del repartidor durante el turno.

    Espeja `courier_state_overrides` del contrato: lo que el arnes simula es
    exactamente lo que un juez puede fijar a mano en un ping.
    """

    config: ShiftConfig
    position_zone: int
    #: Cuando termina todo el trabajo aceptado. Igual a `sim_time` si esta libre.
    busy_until: datetime
    continuous_riding_min: float = 0.0
    last_break_end_time: datetime | None = None
    #: Pedidos aceptados y todavia no entregados, en la forma que espera
    #: `courier_state_overrides.in_flight_orders`.
    in_flight: list[dict[str, Any]] = field(default_factory=list)

    earnings_mxn: float = 0.0        # realizadas: se suman al ENTREGAR
    orders_completed: int = 0
    orders_accepted: int = 0
    orders_offered: int = 0
    #: Aceptados que no alcanzan a entregarse antes del cierre del turno. No
    #: pagan: el repartidor gasto el tiempo y no cobro. Es la consecuencia
    #: concreta de ignorar la constraint `shift_end_infeasible`.
    orders_abandoned: int = 0
    km_total: float = 0.0
    km_deadhead: float = 0.0
    deadline_misses: int = 0
    safety_violations: int = 0
    violations_by_constraint: dict[str, int] = field(default_factory=dict)
    breaks_taken: int = 0

    def overrides(self, sim_time: datetime) -> CourierStateOverrides:
        """El estado tal como viajaria en un `POST /decide`."""
        elapsed = (sim_time - self.config.shift_start_time).total_seconds() / 3600.0
        return CourierStateOverrides(
            continuous_riding_min=self.continuous_riding_min,
            shift_elapsed_hours=max(0.0, elapsed),
            last_break_end_time=self.last_break_end_time,
            shift_end_time=self.config.shift_end_time,
            in_flight_orders=list(self.in_flight),
        )


class ShiftRunner:
    """Corre un turno completo de una politica y devuelve sus metricas."""

    def __init__(
        self,
        config: ShiftConfig,
        zone_map: ZoneMap | None = None,
        log_file: IO[str] | None = None,
    ) -> None:
        self.config = config
        self.zone_map = zone_map or DEFAULT_ZONE_MAP
        self.log_file = log_file
        self._matrix = DistanceMatrix.build(self.zone_map.coords, EuclideanDistanceProvider())

    # -- geometria ---------------------------------------------------------

    def _coord(self, zone_id: int) -> tuple[float, float]:
        return self.zone_map.by_id(zone_id).coord

    def distance_km(self, from_zone: int, to_zone: int) -> float:
        return self._matrix.travel_distance(self._coord(from_zone), self._coord(to_zone))

    def travel_min(self, from_zone: int, to_zone: int, profile: VehicleProfile) -> float:
        """Minutos entre dos zonas al perfil del vehiculo.

        Se deriva de la distancia y la velocidad del perfil en vez de usar el
        tiempo precalculado de la matriz: esa matriz se construyo con la
        velocidad del proveedor euclidiano por defecto, y aqui la velocidad
        tiene que ser la del vehiculo del turno -- si no, moto, car y bike
        tardarian lo mismo.
        """
        return self.distance_km(from_zone, to_zone) / profile.avg_speed_kmh * 60.0

    # -- construccion del request ------------------------------------------

    def build_request(self, offer: Offer, state: ShiftState, sim_time: datetime) -> DecideRequest:
        """Traduce una oferta del stream al contrato oficial.

        El deadhead se calcula aqui y no en el generador porque depende de
        DONDE ESTA el repartidor, cosa que el generador no sabe. Es la version
        honesta del `distance_pickup_km = 0.0` que el engine deja como default.
        """
        zone_pickup = offer.zone_pickup if offer.zone_pickup is not None else state.position_zone
        zone_dropoff = offer.zone_dropoff if offer.zone_dropoff is not None else zone_pickup

        return DecideRequest(
            order_id=offer.id,
            platform=offer.platform,
            sim_time=sim_time,
            zone_pickup=zone_pickup,
            zone_dropoff=zone_dropoff,
            distance_pickup_km=self.distance_km(state.position_zone, zone_pickup),
            distance_delivery_km=self.distance_km(zone_pickup, zone_dropoff),
            base_pay_mxn=offer.pay,
            est_tip_mxn=offer.est_tip_mxn,
            surge_multiplier=offer.surge_multiplier,
            restaurant_prep_min=offer.restaurant_prep_min,
            weight_kg=offer.weight_kg,
            volume_liters=offer.volume_liters,
            vehicle=self.config.vehicle,
            courier_state_overrides=state.overrides(sim_time),
        )

    # -- simulacion del trabajo --------------------------------------------

    def order_timing(
        self, request: DecideRequest, state: ShiftState, sim_time: datetime
    ) -> tuple[float, float, float]:
        """(minutos_totales, minutos_de_manejo, km_totales) de hacer este pedido.

        Arranca cuando el repartidor queda libre, no cuando llega la oferta.
        """
        profile = self.config.profile
        to_pickup = request.distance_pickup_km / profile.avg_speed_kmh * 60.0
        to_dropoff = request.distance_delivery_km / profile.avg_speed_kmh * 60.0
        # El prep del restaurante corre en paralelo mientras vas en camino.
        waiting = max(0.0, request.restaurant_prep_min - to_pickup)
        riding = to_pickup + to_dropoff
        km = request.distance_pickup_km + request.distance_delivery_km
        return riding + waiting, riding, km

    def queue_offset_min(self, state: ShiftState, sim_time: datetime) -> float:
        return max(0.0, (state.busy_until - sim_time).total_seconds() / 60.0)

    def riding_limit(self, sim_time: datetime) -> float:
        """Tope de manejo continuo vigente a esta hora.

        Dentro de la franja de calor baja de 240 a 90. Sale de las mismas
        constantes que usa el gate, no de copias.
        """
        if HEAT_RULE_START_HOUR <= sim_time.hour < HEAT_RULE_END_HOUR:
            return HEAT_RULE_MAX_CONTINUOUS_MIN
        return MANDATORY_BREAK_AFTER_MIN

    def take_break_if_due(self, state: ShiftState, sim_time: datetime) -> None:
        """El repartidor descansa cuando alcanza el tope de manejo continuo.

        Dos cosas que la primera version hacia mal, y que costaban la mitad del
        turno:

        **La pausa se toma AL TERMINAR lo que trae, no solo si esta libre.**
        Exigir que estuviera libre justo en el instante en que llega una oferta
        significaba que un repartidor con trabajo encolado no descansaba nunca,
        y por lo tanto rechazaba todo por `mandatory_break` durante el resto del
        turno. Un repartidor real termina la entrega en curso y entonces para.

        **El tope de calor tambien se descansa.** La regla de las 12:00-16:00
        baja el tope a 90 minutos, pero si nada reinicia el contador, el
        repartidor queda bloqueado entre los 90 y los 240 minutos: horas
        rechazandolo todo sin descansar. Una constraint que encierra al
        repartidor no es una regla de seguridad, es un defecto -- y era el 29%
        de nuestros rechazos.

        El contador se pone en cero al programar la pausa, no al terminarla: la
        pausa ya esta comprometida en `busy_until`, asi que el repartidor no
        puede manejar durante ella de todos modos.
        """
        if state.continuous_riding_min < self.riding_limit(sim_time):
            return
        inicio = max(sim_time, state.busy_until)
        state.busy_until = inicio + timedelta(minutes=MANDATORY_BREAK_DURATION_MIN)
        state.last_break_end_time = state.busy_until
        state.continuous_riding_min = 0.0
        state.breaks_taken += 1

    def accept(
        self,
        request: DecideRequest,
        offer: Offer,
        state: ShiftState,
        sim_time: datetime,
    ) -> None:
        """Aplica al estado el trabajo de aceptar este pedido."""
        profile = self.config.profile
        total_min, riding_min, km = self.order_timing(request, state, sim_time)

        start = max(sim_time, state.busy_until)
        completion = start + timedelta(minutes=total_min)

        state.orders_accepted += 1
        state.busy_until = completion
        state.position_zone = request.zone_dropoff
        state.continuous_riding_min += riding_min
        state.km_total += km
        state.km_deadhead += request.distance_pickup_km

        # La ventana de entrega de la oferta es relativa al inicio del turno.
        deadline = self.config.shift_start_time + timedelta(minutes=offer.time_window[1])
        if completion > deadline:
            state.deadline_misses += 1

        # Ganancias al ENTREGAR (hallazgo 4), netas de combustible.
        #
        # Y solo si la entrega CABE en el turno. Un pedido que terminaria
        # despues del cierre no se cobra: el repartidor gasto el tiempo y se
        # quedo sin pagar. Sin esta linea, una politica que acepta todo
        # "completa" doscientos pedidos en ocho horas y reporta ganancias
        # imposibles -- el numero contra el que compararia nuestro agente
        # seria ficcion, y toda la tabla dejaria de significar nada.
        if completion <= self.config.shift_end_time:
            gross = offer.pay * offer.surge_multiplier + offer.est_tip_mxn
            state.earnings_mxn += gross - km * profile.cost_per_km
            state.orders_completed += 1
        else:
            state.orders_abandoned += 1

        state.in_flight.append(
            {
                "order_id": offer.id,
                "weight_kg": offer.weight_kg,
                "volume_liters": offer.volume_liters,
                "zone_dropoff": request.zone_dropoff,
                "eta_dropoff": completion.isoformat(),
            }
        )

    def drain_delivered(self, state: ShiftState, sim_time: datetime) -> None:
        """Saca de la mochila lo que ya se entrego.

        Sin esto la mochila crece de forma monotona y la constraint de
        capacidad termina rechazandolo todo -- que es el hallazgo 7 de la
        auditoria ("nada completa ni retira paradas de la ruta").
        """
        state.in_flight = [
            o
            for o in state.in_flight
            if datetime.fromisoformat(o["eta_dropoff"]) > sim_time
        ]

    # -- el turno ----------------------------------------------------------

    def run(self, policy) -> ShiftState:
        """Corre el turno completo con `policy` y devuelve el estado final."""
        engine = SimulationEngine(
            seed=self.config.seed,
            shift_duration=self.config.shift_minutes,
            zone_map=self.zone_map,
            vehicle_type=VehicleType(self.config.vehicle),
            log_file=self.log_file,
            shift_start_time=self.config.shift_start_time,
            start_location_zone=self.config.start_location_zone,
        )

        state = ShiftState(
            config=self.config,
            position_zone=self.config.start_location_zone,
            busy_until=self.config.shift_start_time,
        )

        # El stream se consume PEREZOSAMENTE, oferta por oferta. Materializarlo
        # primero haria que el generador escribiera `shift_start`, todos los
        # `order_offered` y el `shift_end` ANTES de la primera decision, y el
        # event log tiene que ir en orden cronologico: un log con las decisiones
        # despues del fin del turno no sirve para replay ni para el dashboard.
        self._log_strategy(engine, policy)

        for event in engine.event_stream():
            if not isinstance(event, Offer):
                continue
            offer = event
            sim_time = self.config.shift_start_time + timedelta(minutes=offer.received_at)
            state.orders_offered += 1

            self.drain_delivered(state, sim_time)
            self.take_break_if_due(state, sim_time)

            request = self.build_request(offer, state, sim_time)
            resultado = policy.decide(request, self, state, sim_time)
            accepted = bool(resultado)

            self._log_decision(engine, offer, resultado, sim_time)

            if accepted:
                # La violacion se mide sobre lo que la politica ACEPTO, con el
                # estado que habia al decidir -- independientemente de si la
                # politica se molesto en consultar el gate.
                self._count_violations(request, state, sim_time)
                self.accept(request, offer, state, sim_time)
                self._log_progress(engine, request, state, sim_time)

            self._refresh_shift_end_stats(engine, state)

        self._refresh_shift_end_stats(engine, state)
        return state

    # -- event log ---------------------------------------------------------

    def _log_strategy(self, engine, policy) -> None:
        """`strategy_update` con los parametros vigentes al arrancar el turno.

        El arnes no corre tier2 (el modelo no puede opinar dentro de una
        corrida que tiene que ser reproducible), asi que aqui hay exactamente
        una revision: la que se uso. Emitirla igual importa por el replay --
        `StrategyLayer.apply_recorded()` reinyecta los parametros desde este
        evento en vez de volver a llamar al modelo, que es lo que garantiza
        que ninguna decision del fast path cambie al reproducir el turno.
        """
        if self.log_file is None:
            return
        wage = getattr(policy, "reservation_wage_mxn_hr", None)
        engine.log_event(
            EventType.STRATEGY_UPDATE,
            0.0,
            {
                "reservation_wage_mxn_hr": wage if wage is not None else 0.0,
                "reasoning": f"parametros fijos del arnes para la politica {getattr(policy, 'name', '?')}",
                "confidence": "high",
                "degraded": False,
            },
        )

    def _minute(self, sim_time: datetime) -> float:
        return (sim_time - self.config.shift_start_time).total_seconds() / 60.0

    def _log_decision(self, engine, offer: Offer, resultado, sim_time: datetime) -> None:
        if self.log_file is None:
            return
        engine.log_event(
            EventType.DECISION,
            self._minute(sim_time),
            {
                "order_id": offer.id,
                "decision": "ACCEPT" if bool(resultado) else "SKIP",
                "reason": getattr(resultado, "reason", "sin motivo registrado"),
                "binding_constraint": getattr(resultado, "binding_constraint", None),
                "latency_ms": 0.0,  # el arnes no mide latencia: eso lo hace /decide
                "tier": "tier1",
            },
        )

    def _log_progress(
        self, engine, request: DecideRequest, state: ShiftState, sim_time: datetime
    ) -> None:
        """`position_update` y `earnings_update`.

        El engine declaraba que estos dos eventos "siguen sin productor". Quien
        corre el turno es su productor natural: es el unico que sabe donde
        quedo el repartidor y cuanto lleva cobrado. Los necesita el dashboard
        (P2.3) y el replay (P2.2).
        """
        if self.log_file is None:
            return
        minuto = self._minute(sim_time)
        engine.log_event(
            EventType.POSITION_UPDATE,
            minuto,
            {"zone": state.position_zone, "status": "to_dropoff"},
        )
        horas = max(minuto / 60.0, 1e-9)
        engine.log_event(
            EventType.EARNINGS_UPDATE,
            minuto,
            {
                "earnings_mxn": round(state.earnings_mxn, 2),
                "orders_completed": state.orders_completed,
                "mxn_per_hr": round(state.earnings_mxn / horas, 1),
            },
        )

    def _refresh_shift_end_stats(self, engine, state: ShiftState) -> None:
        engine.shift_end_stats = {
            "orders_completed": state.orders_completed,
            "earnings_mxn": round(state.earnings_mxn, 2),
            "safety_violations": state.safety_violations,
        }

    def _count_violations(
        self, request: DecideRequest, state: ShiftState, sim_time: datetime
    ) -> None:
        total_min, _, _ = self.order_timing(request, state, sim_time)
        verdict = evaluate_safety_full(
            vehicle=request.vehicle,
            weight_kg=request.weight_kg,
            volume_liters=request.volume_liters,
            sim_time=sim_time,
            zone_dropoff=request.zone_dropoff,
            continuous_riding_min=state.continuous_riding_min,
            order_total_time_min=total_min,
            shift_end_time=self.config.shift_end_time,
            in_flight_weight_kg=sum(o["weight_kg"] for o in state.in_flight),
            in_flight_volume_liters=sum(o["volume_liters"] for o in state.in_flight),
            last_break_end_time=state.last_break_end_time,
            queue_offset_min=self.queue_offset_min(state, sim_time),
        )
        for violation in verdict.violations:
            state.safety_violations += 1
            state.violations_by_constraint[violation.constraint] = (
                state.violations_by_constraint.get(violation.constraint, 0) + 1
            )
