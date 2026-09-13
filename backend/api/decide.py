"""Bloque 6 - Endpoint de decision (contrato oficial, fast path).

Contrato: student-materials/courier/decision_response_schema.json
Protocolo: student-materials/courier/evaluation_protocol.md

Deliberadamente NO se monta bajo el prefijo /api de api/routes.py: el README
del reto y validate_format.py --endpoint golpean http://host:puerto/decide
directo, sin prefijo.

Este endpoint es autocontenido (no depende de que el loop de simulacion este
corriendo): cada request trae su propio `sim_time` y, opcionalmente,
`courier_state_overrides` para fijar el estado del repartidor antes del
ping -- asi es como el protocolo de evaluacion prueba condiciones de
frontera sin tener que orquestar un turno completo. Por eso puede
implementarse ya, aunque el reloj real del turno (P0.1, Abraham) y el motor
VRPTW de coordenadas (Bloque 3, decision.py/greedy.py) todavia no hablan el
mismo modelo de datos (zonas enteras vs. lat/lon) -- ver P0.2 en el roadmap
de la auditoria del 12 sep.
"""

import time

from fastapi import APIRouter, HTTPException

from api.schemas import (
    AlternativeConsidered,
    DecideRequest,
    DecideResponse,
    EconomicsBreakdown,
    ExplainDecisionResponse,
)
from core.agent.economics import RESERVATION_WAGE_MXN_HR, evaluate_economics
from core.agent.safety import evaluate_safety
from core.agent.vehicle_profiles import VEHICLE_PROFILES

MAX_REASON_WORDS = 40

router = APIRouter(tags=["decide"])

# Log en memoria para GET /explain_decision/{order_id}. Un reinicio del
# proceso lo vacia; para replay real (P2.2) esto debe respaldarse en el event
# log JSONL (P1.1, Abraham), no aqui.
_decision_log: dict[str, ExplainDecisionResponse] = {}


def _order_total_time_min(request: DecideRequest) -> float:
    """Tiempo total estimado (min) de esta oferta: pickup + entrega.

    Usa estimated_pickup_min/estimated_delivery_min si vienen en el request
    (event_log_schema.json los documenta como el valor preferente); si no,
    deriva de distancia + velocidad del perfil de vehiculo, tal como pide el
    schema ("if absent, derive from distance and speed").
    """
    profile = VEHICLE_PROFILES[request.vehicle]

    to_pickup_min = request.estimated_pickup_min
    if to_pickup_min is None:
        to_pickup_min = request.distance_pickup_km / profile.speed_kmh * 60.0

    to_dropoff_min = request.estimated_delivery_min
    if to_dropoff_min is None:
        to_dropoff_min = request.distance_delivery_km / profile.speed_kmh * 60.0

    # El repartidor puede llegar al pickup antes de que la orden este lista;
    # el reloj efectivo de esa etapa es el mayor de los dos.
    pickup_ready_min = max(to_pickup_min, request.restaurant_prep_min)
    return pickup_ready_min + to_dropoff_min


def _clamp_reason(reason: str) -> str:
    words = reason.split()
    if len(words) <= MAX_REASON_WORDS:
        return reason
    return " ".join(words[:MAX_REASON_WORDS])


@router.post("/decide", response_model=DecideResponse)
async def decide(request: DecideRequest) -> DecideResponse:
    t0 = time.perf_counter()
    overrides = request.courier_state_overrides

    total_time_min = _order_total_time_min(request)

    economics = evaluate_economics(
        base_pay_mxn=request.base_pay_mxn,
        est_tip_mxn=request.est_tip_mxn,
        surge_multiplier=request.surge_multiplier,
        total_time_min=total_time_min,
        deadhead_km=request.distance_pickup_km,
    )

    violation = evaluate_safety(
        vehicle=request.vehicle,
        weight_kg=request.weight_kg,
        volume_liters=request.volume_liters,
        sim_time=request.sim_time,
        zone_dropoff=request.zone_dropoff,
        continuous_riding_min=overrides.continuous_riding_min,
        order_total_time_min=total_time_min,
        shift_end_time=overrides.shift_end_time,
    )

    if violation is not None:
        decision: str = "SKIP"
        binding_constraint = violation.constraint
        reason = violation.reason
        alternatives = [AlternativeConsidered(option="ACCEPT", rejected_because=reason)]
    elif economics.adjusted_rate_mxn_hr < RESERVATION_WAGE_MXN_HR:
        decision = "SKIP"
        binding_constraint = "reservation_wage"
        reason = (
            f"${economics.adjusted_rate_mxn_hr:.0f}/hr < salario de reserva "
            f"${RESERVATION_WAGE_MXN_HR:.0f}/hr"
        )
        alternatives = [AlternativeConsidered(option="ACCEPT", rejected_because=reason)]
    else:
        decision = "ACCEPT"
        binding_constraint = None
        reason = (
            f"${economics.adjusted_rate_mxn_hr:.0f}/hr >= salario de reserva "
            f"${RESERVATION_WAGE_MXN_HR:.0f}/hr, sin violar constraints de seguridad"
        )
        alternatives = [
            AlternativeConsidered(
                option="SKIP", rejected_because="pasa las 5 constraints y el salario de reserva"
            )
        ]

    reason = _clamp_reason(reason)
    latency_ms = (time.perf_counter() - t0) * 1000

    response = DecideResponse(
        order_id=request.order_id,
        decision=decision,
        reason=reason,
        binding_constraint=binding_constraint,
        latency_ms=latency_ms,
        tier="tier1",  # sin capa tier2/LLM implementada todavia (P2.1)
        degraded=False,
        economics=EconomicsBreakdown(**economics.__dict__),
    )

    _decision_log[request.order_id] = ExplainDecisionResponse(
        order_id=request.order_id,
        decision=decision,
        reason=reason,
        inputs={
            "sim_time": request.sim_time.isoformat(),
            "zone_pickup": request.zone_pickup,
            "zone_dropoff": request.zone_dropoff,
            "vehicle": request.vehicle,
            "weight_kg": request.weight_kg,
            "volume_liters": request.volume_liters,
            "continuous_riding_min": overrides.continuous_riding_min,
            "shift_elapsed_hours": overrides.shift_elapsed_hours,
            "shift_end_time": overrides.shift_end_time.isoformat() if overrides.shift_end_time else None,
            "in_flight_orders": overrides.in_flight_orders,
            "total_time_min": total_time_min,
            "deadhead_km": request.distance_pickup_km,
            "economics": economics.__dict__,
        },
        alternatives_considered=alternatives,
    )

    return response


@router.get("/explain_decision/{order_id}", response_model=ExplainDecisionResponse)
async def explain_decision(order_id: str) -> ExplainDecisionResponse:
    record = _decision_log.get(order_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no hay decision registrada para order_id={order_id!r}")
    return record
