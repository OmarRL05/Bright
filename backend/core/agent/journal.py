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
dos veces podria contestar distinto. Por eso `record()` guarda los valores ya
calculados (incluida la estimacion de tiempos que uso el gate) y `explain()`
solo les da forma.

Reparto del trabajo entre las dos operaciones
---------------------------------------------
`record()` corre DENTRO de la ventana de 50 ms: guarda referencias a objetos
inmutables que ya existen y no formatea nada. `explain()` corre cuando un juez
pregunta, sin presion de tiempo: ahi si se arma el dict del contrato. Invertir
ese reparto (formatear al registrar) cargaria trabajo al fast path para un
dict que casi nunca se lee.

Sin reloj de pared, igual que el resto del fast path: el tiempo de cada
registro es el `sim_time` de la oferta.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from core.agent.contracts import BindingConstraint, CourierRuntimeState, Decision, OrderRequest
from core.agent.safety import SafetyVerdict

#: Tope de decisiones guardadas. Un turno de 8 h con ofertas cada pocos
#: segundos no llega ni de lejos; el limite existe para que una corrida larga
#: o un bucle de replay no crezcan sin techo. Al llegar, se tira la mas vieja.
MAX_RECORDS = 20_000


@dataclass(frozen=True)
class DecisionRecord:
    """Lo que se supo en el momento de decidir. Inmutable a proposito."""

    order: OrderRequest
    state: CourierRuntimeState
    verdict: SafetyVerdict
    decision: Decision
    reason: str
    binding_constraint: BindingConstraint | None
    latency_ms: float
    tier: str = "tier1"
    degraded: bool = False
    #: Aritmetica de la capa economica, si existe. Hoy llega en None: nadie es
    #: dueño de esa capa bajo el contrato oficial todavia. El dia que exista,
    #: se pasa aqui y `explain()` la publica sin cambios en este archivo.
    economics: dict[str, Any] | None = None
    #: Parametros de estrategia vigentes (tier2). Los llena strategy.py.
    strategy: dict[str, Any] | None = None

    @property
    def order_id(self) -> str:
        return self.order.order_id

    @property
    def sim_time(self) -> datetime | None:
        return self.order.sim_time


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
        "order_id": order.order_id,
        "platform": order.platform,
        "sim_time": _iso(order.sim_time),
        "zone_pickup": order.zone_pickup,
        "zone_dropoff": order.zone_dropoff,
        "distance_pickup_km": order.distance_pickup_km,
        "distance_delivery_km": order.distance_delivery_km,
        "base_pay_mxn": order.base_pay_mxn,
        "est_tip_mxn": order.est_tip_mxn,
        "surge_multiplier": order.surge_multiplier,
        "restaurant_prep_min": order.restaurant_prep_min,
        "weight_kg": order.weight_kg,
        "volume_liters": order.volume_liters,
        "vehicle": order.vehicle,
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

    order, state, verdict = record.order, record.state, record.verdict
    estimate = verdict.estimate

    time_remaining_min: float | None = None
    if order.sim_time is not None and state.shift_end_time is not None:
        time_remaining_min = (state.shift_end_time - order.sim_time).total_seconds() / 60.0

    return {
        "sim_time": _iso(order.sim_time),
        "position_zone": state.current_zone,
        "vehicle": order.vehicle,
        "vehicle_profile": {
            "speed_kmh": order.profile.speed_kmh,
            "max_weight_kg": order.profile.max_weight_kg,
            "max_volume_liters": order.profile.max_volume_liters,
        },
        "offer": {
            "zone_pickup": order.zone_pickup,
            "zone_dropoff": order.zone_dropoff,
            "distance_pickup_km": order.distance_pickup_km,
            "distance_delivery_km": order.distance_delivery_km,
            "base_pay_mxn": order.base_pay_mxn,
            "surge_multiplier": order.surge_multiplier,
            "est_tip_mxn": order.est_tip_mxn,
            "gross_pay_mxn": round(order.gross_pay_mxn, 2),
            "weight_kg": order.weight_kg,
            "volume_liters": order.volume_liters,
            "restaurant_prep_min": order.restaurant_prep_min,
        },
        "courier_state": {
            "continuous_riding_min": state.continuous_riding_min,
            "effective_continuous_riding_min": safety.effective_continuous_riding_min(
                state, order.sim_time
            ),
            "shift_elapsed_hours": state.shift_elapsed_hours,
            "last_break_end_time": _iso(state.last_break_end_time),
            "shift_end_time": _iso(state.shift_end_time),
            "time_remaining_min": time_remaining_min,
            "in_flight_weight_kg": state.in_flight_weight_kg,
            "in_flight_volume_liters": state.in_flight_volume_liters,
        },
        "in_flight_orders": [
            {
                "order_id": o.order_id,
                "weight_kg": o.weight_kg,
                "volume_liters": o.volume_liters,
                "zone_dropoff": o.zone_dropoff,
                "eta_dropoff": _iso(o.eta_dropoff),
            }
            for o in state.in_flight_orders
        ],
        "time_to_completion_min": estimate.minutes_to_dropoff if estimate else None,
        "completion_breakdown": (
            {
                "queue_offset_min": estimate.queue_offset_minutes,
                "riding_min": estimate.riding_minutes,
                "waiting_min": estimate.waiting_minutes,
            }
            if estimate
            else None
        ),
        "active_limits": {
            "flagged_zones": sorted(safety.FLAGGED_ZONES),
            "curfew_start_hour": safety.CURFEW_START_HOUR,
            "max_continuous_riding_min": safety.MAX_CONTINUOUS_RIDING_MIN,
            "mandatory_break_min": safety.MANDATORY_BREAK_MIN,
            "heat_window": f"{safety.HEAT_WINDOW_START_HOUR:02d}:00-{safety.HEAT_WINDOW_END_HOUR:02d}:00",
            "heat_max_continuous_riding_min": safety.HEAT_MAX_CONTINUOUS_RIDING_MIN,
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
    3. **La aritmetica economica**, cuando esa capa exista.
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

    if record.economics:
        rate = record.economics.get("adjusted_rate_mxn_hr")
        floor = record.economics.get("reservation_wage_mxn_hr")
        if rate is not None and floor is not None:
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
