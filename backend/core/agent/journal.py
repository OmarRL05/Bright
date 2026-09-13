"""Bitacora de decisiones — respaldo de `explain_decision` y del event log.

    "Looked up by order_id after the fact. Judges ask 'why did you skip that
     order' and expect an answer in under ten seconds from a log, not from
     re-running your system."
    -- decision_response_schema.json

    "Answering from a decision log in under ten seconds is itself scored.
     Re-deriving the answer live is the wrong answer even when it turns out
     to be right."
    -- student-materials/courier/README.md

Eso es una restriccion de diseño, no un detalle de implementacion: **explicar
no puede volver a decidir**. Si `explain_decision` recalculara, respondería con
los numeros de *ahora* y no con los de *entonces* — y ante la misma pregunta
dos veces podria contestar distinto. Por eso `record()` guarda lo que ya se
calculo (incluido `minutes_to_completion`, el numero exacto que usaron las
constraints de horario) y `explain()` solo le da forma.

Reparto del trabajo entre las dos operaciones
---------------------------------------------
`record()` corre DENTRO de la ventana de 50 ms: guarda referencias a objetos
que ya existen y no formatea nada. `explain()` corre cuando un juez pregunta,
sin presion de tiempo: ahi si se arma el dict del contrato. Invertir ese
reparto (formatear al registrar) cargaria trabajo al fast path para un dict que
casi nunca se lee.

Sobre el acoplamiento con la capa HTTP
---------------------------------------
`DecisionRecord` guarda el `DecideRequest` y los `courier_state_overrides` de
pydantic **tal cual**, leidos con `getattr`, en vez de importarlos desde
`api.schemas`. Dos razones: `core/` no debe depender de `api/`, y no copiar los
campos evita que este archivo tenga que cambiar cada vez que el contrato HTTP
gane uno. Los atributos que se esperan estan listados en `OrderLike` /
`OverridesLike` abajo, y todo acceso tolera que falten.

Sin reloj de pared, igual que el resto del fast path: el tiempo de cada
registro es el `sim_time` de la oferta.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Protocol

from core.agent.safety import SafetyVerdict

#: Tope de decisiones guardadas. Un turno de 8 h con ofertas cada pocos
#: segundos no llega ni de lejos; el limite existe para que una corrida larga
#: o un bucle de replay no crezcan sin techo. Al llegar, se tira la mas vieja.
MAX_RECORDS = 20_000


class OrderLike(Protocol):
    """Lo que la bitacora espera de una oferta (lo cumple `DecideRequest`)."""

    order_id: str
    sim_time: datetime
    zone_pickup: int
    zone_dropoff: int
    distance_pickup_km: float
    distance_delivery_km: float
    base_pay_mxn: float
    surge_multiplier: float
    vehicle: str


class OverridesLike(Protocol):
    """Lo que espera del estado (lo cumple `CourierStateOverrides`)."""

    continuous_riding_min: float
    shift_elapsed_hours: float
    last_break_end_time: datetime | None
    shift_end_time: datetime | None
    in_flight_orders: list[dict[str, Any]]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Lectura tolerante: un campo que no vino no puede tumbar el explain."""
    return getattr(obj, name, default) if obj is not None else default


# ==========================================================================
# Utilidades de mochila
#
# Viven aqui y no en safety.py porque son parseo del payload, no politica de
# seguridad: `in_flight_orders` es `list[dict]` sin forma fijada por el
# material oficial ("el ejemplo solo muestra []"), asi que hay que ser
# tolerante con lo que manden los jueces.
# ==========================================================================


def in_flight_totals(in_flight_orders: Any) -> tuple[float, float]:
    """(peso_kg, volumen_L) acumulados de los pedidos ya aceptados.

    Tolera dicts con claves faltantes, valores no numericos y entradas que no
    son dicts. Lo que no se puede leer cuenta como 0: es la direccion segura
    para el formato y la honesta para el juicio -- no inventamos carga que no
    podemos sostener con un numero.
    """
    weight = volume = 0.0
    for item in in_flight_orders or ():
        if not isinstance(item, dict):
            continue
        for key, add in (("weight_kg", "w"), ("volume_liters", "v")):
            raw = item.get(key)
            if isinstance(raw, bool) or raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if add == "w":
                weight += value
            else:
                volume += value
    return weight, volume


def latest_in_flight_eta(in_flight_orders: Any) -> datetime | None:
    """ETA de dropoff mas tardia entre los pedidos en vuelo, si viene."""
    etas: list[datetime] = []
    for item in in_flight_orders or ():
        if not isinstance(item, dict):
            continue
        raw = item.get("eta_dropoff")
        if isinstance(raw, datetime):
            etas.append(raw.replace(tzinfo=None))
        elif isinstance(raw, str) and raw.strip():
            text = raw.strip().rstrip("Zz")
            try:
                etas.append(datetime.fromisoformat(text).replace(tzinfo=None))
            except ValueError:
                continue
    return max(etas) if etas else None


def queue_offset_min(in_flight_orders: Any, sim_time: datetime | None) -> float:
    """Minutos que falta para terminar lo ya aceptado.

    Es lo que hace que la constraint de fin de turno vea la RUTA COMBINADA y no
    solo el pedido suelto -- la categoria de sondeo "Stacking and route
    feasibility" del protocolo.
    """
    if sim_time is None:
        return 0.0
    latest = latest_in_flight_eta(in_flight_orders)
    if latest is None:
        return 0.0
    return max(0.0, (latest - sim_time).total_seconds() / 60.0)


# ==========================================================================
# Registro
# ==========================================================================


@dataclass(frozen=True)
class DecisionRecord:
    """Lo que se supo en el momento de decidir. Inmutable a proposito."""

    order: Any            # DecideRequest (ver OrderLike)
    overrides: Any        # CourierStateOverrides (ver OverridesLike)
    verdict: SafetyVerdict
    decision: str
    reason: str
    binding_constraint: str | None
    latency_ms: float
    tier: str = "tier1"
    degraded: bool = False
    #: Aritmetica de la capa economica (EconomicsResult.__dict__ o equivalente).
    economics: dict[str, Any] | None = None
    #: Parametros de estrategia vigentes (tier2). Los llena strategy.py.
    strategy: dict[str, Any] | None = None

    @property
    def order_id(self) -> str:
        return str(_get(self.order, "order_id", ""))

    @property
    def sim_time(self) -> datetime | None:
        return _get(self.order, "sim_time")


class DecisionJournal:
    """Bitacora append-only, indexada por `order_id`.

    Segura entre hilos: el fast path escribe desde el hilo del request y la
    capa de estrategia (tier2) o el dashboard pueden leer desde otro.
    """

    def __init__(self, max_records: int = MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._records: OrderedDict[str, DecisionRecord] = OrderedDict()
        self._max_records = max_records

    # -- escritura (dentro de la ventana de decision) ----------------------

    def record(self, record: DecisionRecord) -> DecisionRecord:
        """Guarda una decision. O(1), sin formateo, sin I/O.

        Si el mismo `order_id` se decide dos veces (un replay sobre la misma
        bitacora), gana la ultima y conserva su lugar al final del orden
        cronologico.
        """
        with self._lock:
            self._records.pop(record.order_id, None)
            self._records[record.order_id] = record
            while len(self._records) > self._max_records:
                self._records.popitem(last=False)
        return record

    # -- lectura (cuando un juez pregunta) ---------------------------------

    def get(self, order_id: str) -> DecisionRecord | None:
        with self._lock:
            return self._records.get(order_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def recent(self, limit: int = 20) -> list[DecisionRecord]:
        """Las ultimas `limit` decisiones, de la mas nueva a la mas vieja.

        Es lo que alimenta el feed del dashboard.
        """
        with self._lock:
            return list(reversed(list(self._records.values())))[:limit]

    def all_records(self) -> list[DecisionRecord]:
        with self._lock:
            return list(self._records.values())

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def explain(self, order_id: str) -> dict[str, Any] | None:
        """`explain_decision` del contrato oficial. None si no existe.

        Devuelve exactamente las cinco claves requeridas por el schema:
        `order_id`, `decision`, `reason`, `inputs`, `alternatives_considered`.
        """
        record = self.get(order_id)
        return explain_payload(record) if record else None

    # -- salida al event log JSONL (Bloque 1, P1.1) -------------------------

    def decision_events(self) -> Iterator[dict[str, Any]]:
        """Un evento `decision` por registro, en orden cronologico."""
        for record in self.all_records():
            yield to_decision_event(record)


# ==========================================================================
# Renderizado. Funciones libres: se pueden usar sin instanciar el journal, y
# se testean sin construir uno.
# ==========================================================================


def to_decision_event(record: DecisionRecord) -> dict[str, Any]:
    """Evento `decision` del event log (event_log_schema.json).

    Claves requeridas por validate_format.py: event, order_id, sim_time,
    decision, reason, latency_ms. Se agregan binding_constraint, tier y
    degraded porque son justo los campos que hacen la refusal de seguridad
    distinguible por maquina de la matematica de pago.
    """
    return {
        "event": "decision",
        "order_id": record.order_id,
        "sim_time": _iso(record.sim_time),
        "decision": record.decision,
        "reason": record.reason,
        "binding_constraint": record.binding_constraint,
        "latency_ms": round(record.latency_ms, 3),
        "tier": record.tier,
        "degraded": record.degraded,
    }


def to_order_offered_event(record: DecisionRecord) -> dict[str, Any]:
    """Evento `order_offered` reconstruido desde la oferta registrada.

    Cortesia para Bloque 1 (P1.1): permite que el journal, por si solo,
    produzca un fragmento de event log que pasa el validador oficial. Si el
    simulador ya emite este evento, este helper sobra — no es la fuente de
    verdad del stream, solo un eco de lo que el endpoint recibio.
    """
    order = record.order
    return {
        "event": "order_offered",
        "order_id": record.order_id,
        "platform": _get(order, "platform"),
        "sim_time": _iso(record.sim_time),
        "zone_pickup": _get(order, "zone_pickup"),
        "zone_dropoff": _get(order, "zone_dropoff"),
        "distance_pickup_km": _get(order, "distance_pickup_km"),
        "distance_delivery_km": _get(order, "distance_delivery_km"),
        "base_pay_mxn": _get(order, "base_pay_mxn"),
        "est_tip_mxn": _get(order, "est_tip_mxn"),
        "surge_multiplier": _get(order, "surge_multiplier"),
        "restaurant_prep_min": _get(order, "restaurant_prep_min"),
        "weight_kg": _get(order, "weight_kg"),
        "volume_liters": _get(order, "volume_liters"),
        "vehicle": _get(order, "vehicle"),
    }


def explain_payload(record: DecisionRecord) -> dict[str, Any]:
    """Arma la respuesta de `explain_decision` a partir de un registro."""
    return {
        "order_id": record.order_id,
        "decision": record.decision,
        "reason": record.reason,
        "inputs": _inputs(record),
        "alternatives_considered": _alternatives(record),
    }


def _inputs(record: DecisionRecord) -> dict[str, Any]:
    """Estado numerico completo en el momento de decidir.

    El schema lo enumera: "position, time_remaining, time_to_completion,
    active strategy parameters, in-flight orders". Se agregan los limites
    vigentes porque la pregunta que sigue siempre es "¿contra que limite?", y
    tenerlo en el mismo dict evita ir a buscarlo al codigo en vivo.
    """
    from core.agent import safety  # import local: evita un ciclo en import-time

    order, overrides = record.order, record.overrides
    sim_time = record.sim_time
    shift_end = _get(overrides, "shift_end_time")
    in_flight = _get(overrides, "in_flight_orders", []) or []
    profile = safety.profile_for(_get(order, "vehicle", ""))

    time_remaining_min: float | None = None
    if sim_time is not None and shift_end is not None:
        time_remaining_min = (shift_end - sim_time).total_seconds() / 60.0

    weight, volume = in_flight_totals(in_flight)
    continuous = float(_get(overrides, "continuous_riding_min", 0.0) or 0.0)

    from core.agent.economics import dropoff_demand_score

    zone_pickup = _get(order, "zone_pickup")
    zone_dropoff = _get(order, "zone_dropoff")

    return {
        "sim_time": _iso(sim_time),
        "position_zone": _get(overrides, "current_zone", zone_pickup),
        # Un juez puede mandar cualquier entero de zona: "you build your own
        # data" no significa que conozcamos su universo de zonas. Cuando no la
        # conocemos, la demanda se asume neutral -- y eso queda escrito aqui en
        # vez de ser un supuesto invisible dentro de la aritmetica.
        #
        # Las tres banderas, y no una: `zone_known` a secas es ambiguo (¿la de
        # recogida o la de entrega?) y esa ambiguedad ya costo un test. Aqui
        # `zone_known` significa "conocemos LAS DOS"; las otras dos dicen cual
        # falla. La demanda se toma de la zona de DROPOFF, que es la que
        # importa para "donde te deja el pedido".
        "zone_known": (
            safety.zone_is_known(zone_pickup) and safety.zone_is_known(zone_dropoff)
        ),
        "zone_pickup_known": safety.zone_is_known(zone_pickup),
        "zone_dropoff_known": safety.zone_is_known(zone_dropoff),
        # Dos scores con nombre, no un `demand_score` a secas: la economia usa
        # el de DROPOFF (donde te deja el pedido, que es la categoria de sondeo
        # "Dropoff location value"), y el de pickup se publica porque es el que
        # explica de donde salio la oferta. Confundirlos cambia el numero.
        "dropoff_demand_score": dropoff_demand_score(zone_dropoff),
        "pickup_demand_score": dropoff_demand_score(zone_pickup),
        "vehicle": _get(order, "vehicle"),
        "vehicle_profile": {
            "avg_speed_kmh": profile.avg_speed_kmh,
            "weight_limit_kg": profile.weight_limit_kg,
            "volume_limit_liters": profile.volume_limit_liters,
            "max_backpack": profile.max_backpack,
            "cost_per_km": profile.cost_per_km,
        },
        "offer": {
            "zone_pickup": _get(order, "zone_pickup"),
            "zone_dropoff": _get(order, "zone_dropoff"),
            "distance_pickup_km": _get(order, "distance_pickup_km"),
            "distance_delivery_km": _get(order, "distance_delivery_km"),
            "base_pay_mxn": _get(order, "base_pay_mxn"),
            "surge_multiplier": _get(order, "surge_multiplier"),
            "est_tip_mxn": _get(order, "est_tip_mxn"),
            "weight_kg": _get(order, "weight_kg"),
            "volume_liters": _get(order, "volume_liters"),
            "restaurant_prep_min": _get(order, "restaurant_prep_min"),
        },
        "courier_state": {
            "continuous_riding_min": continuous,
            "effective_continuous_riding_min": safety.effective_continuous_riding_min(
                continuous, _get(overrides, "last_break_end_time"), sim_time
            ),
            "shift_elapsed_hours": _get(overrides, "shift_elapsed_hours"),
            "last_break_end_time": _iso(_get(overrides, "last_break_end_time")),
            "shift_end_time": _iso(shift_end),
            "time_remaining_min": time_remaining_min,
            "in_flight_weight_kg": weight,
            "in_flight_volume_liters": volume,
        },
        "in_flight_orders": list(in_flight),
        "time_to_completion_min": record.verdict.minutes_to_completion,
        "queue_offset_min": queue_offset_min(in_flight, sim_time),
        "active_limits": {
            "flagged_zones": sorted(safety.FLAGGED_ZONES),
            "night_curfew_hour": safety.NIGHT_CURFEW_HOUR,
            "mandatory_break_after_min": safety.MANDATORY_BREAK_AFTER_MIN,
            "mandatory_break_duration_min": safety.MANDATORY_BREAK_DURATION_MIN,
            "heat_window": f"{safety.HEAT_RULE_START_HOUR:02d}:00-{safety.HEAT_RULE_END_HOUR:02d}:00",
            "heat_rule_max_continuous_min": safety.HEAT_RULE_MAX_CONTINUOUS_MIN,
            "shift_end_safety_margin_min": safety.SHIFT_END_SAFETY_MARGIN_MIN,
        },
        "strategy": record.strategy,
        "economics": record.economics,
        "latency_ms": round(record.latency_ms, 3),
        "tier": record.tier,
        "degraded": record.degraded,
    }


def _alternatives(record: DecisionRecord) -> list[dict[str, str]]:
    """"What else was on the table and why it lost."

    Tres fuentes, en este orden:

    1. **El camino no tomado.** Si se rechazo, la alternativa era aceptar (y
       perdio por la constraint que mordio); si se acepto, era rechazar (y
       perdio porque nada la bloqueaba).
    2. **Las constraints que tambien violaban pero no mandaron.** Esta es la
       parte con valor real: contesta "¿y si arreglo esa?" sin re-correr nada
       — "aunque resolvieras el descanso, el toque de queda igual la bloquea".
    3. **La aritmetica economica**, que es lo que contesta "¿por que SI?"
       cuando ninguna constraint bloqueo.
    """
    alternatives: list[dict[str, str]] = []
    binding = record.verdict.binding

    if record.decision == "SKIP":
        alternatives.append({"option": "ACCEPT", "rejected_because": record.reason})
    else:
        alternatives.append(
            {
                "option": "SKIP",
                "rejected_because": "ninguna constraint de seguridad violo y el criterio de pago acepto",
            }
        )

    if binding is not None:
        for violation in record.verdict.violations[1:]:
            alternatives.append(
                {
                    "option": f"ACCEPT tras resolver {binding.constraint}",
                    "rejected_because": violation.reason,
                }
            )

    economics = record.economics or {}
    rate = economics.get("adjusted_rate_mxn_hr")
    floor = economics.get("reservation_wage_mxn_hr")
    if rate is not None and floor is not None:
        if record.decision == "ACCEPT":
            alternatives.append(
                {
                    "option": "SKIP por paga insuficiente",
                    "rejected_because": (
                        f"${rate:.0f}/hr efectivos superan el minimo de ${floor:.0f}/hr"
                    ),
                }
            )
        else:
            alternatives.append(
                {
                    "option": "decidir solo por paga",
                    "rejected_because": f"${rate:.0f}/hr efectivos contra un minimo de ${floor:.0f}/hr",
                }
            )

    return alternatives


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


# ==========================================================================
# Instancia compartida del proceso.
#
# El endpoint, el dashboard y el escritor del event log tienen que ver la
# MISMA bitacora; si cada uno construye la suya, `explain_decision` contesta
# "no encontrado" para decisiones que si se tomaron. Se expone una sola y se
# permite inyectar otra en los tests.
# ==========================================================================

JOURNAL = DecisionJournal()
