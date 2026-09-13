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
frontera sin tener que orquestar un turno completo.

Que pasa dentro de la ventana de 50 ms, en orden
-------------------------------------------------
1. pydantic parsea el body (`extra="ignore"`: los jueces pueden mandar campos
   que no conocemos y eso no puede ser un 422).
2. Se estima cuanto tarda la oferta y cuanto falta para terminar lo que ya
   trae en vuelo.
3. `evaluate_safety_full` -- las 5 constraints duras. No ve el pago.
4. `evaluate_economics` -- tasa efectiva contra el salario de reserva vigente.
5. `combine` -- une las dos. Si la seguridad bloqueo, lo economico se ignora
   por completo; no hay camino de codigo que produzca ACCEPT con una
   constraint violada.
6. `JOURNAL.record` -- O(1), sin formateo, para poder explicar despues.

Nada de esto toca la red, el disco ni un modelo. La capa de estrategia (tier2)
corre en otro hilo, entre pings, y aqui solo se **lee** lo que haya publicado.

VEHICLE_PROFILES viene de core.models (P0.5, Abraham) y ya trae limites de
peso/volumen. El motor VRPTW de coordenadas (Bloque 3, decision.py/greedy.py)
sigue sin hablar el mismo modelo de zonas enteras que este endpoint -- este
usa `distance_pickup_km`/`distance_delivery_km` tal como llegan en el
request en vez de resolver `zone_pickup`/`zone_dropoff` a coordenadas, para
no depender de que un juez use las mismas zonas que nuestro simulador
interno genera (ver docs/03_Integracion_API_Decide.md).
"""

import time

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api.schemas import (
    DecideRequest,
    DecideResponse,
    EconomicsBreakdown,
    ExplainDecisionResponse,
    StatusResponse,
)
from core.agent import reasons
from core.agent.economics import evaluate_economics
from core.agent.journal import (
    JOURNAL,
    DecisionRecord,
    in_flight_totals,
    queue_offset_min,
)
from core.agent.safety import combine, evaluate_safety_full, profile_for
from core.agent.strategy import STRATEGY

router = APIRouter(tags=["decide"])


def _order_total_time_min(request: DecideRequest) -> float:
    """Tiempo total estimado (min) de esta oferta: pickup + entrega.

    Usa estimated_pickup_min/estimated_delivery_min si vienen en el request
    (event_log_schema.json los documenta como el valor preferente); si no,
    deriva de distancia + velocidad del perfil de vehiculo, tal como pide el
    schema ("if absent, derive from distance and speed").
    """
    profile = profile_for(request.vehicle)

    to_pickup_min = request.estimated_pickup_min
    if to_pickup_min is None:
        to_pickup_min = request.distance_pickup_km / profile.avg_speed_kmh * 60.0

    to_dropoff_min = request.estimated_delivery_min
    if to_dropoff_min is None:
        to_dropoff_min = request.distance_delivery_km / profile.avg_speed_kmh * 60.0

    # El repartidor puede llegar al pickup antes de que la orden este lista;
    # el reloj efectivo de esa etapa es el mayor de los dos.
    pickup_ready_min = max(to_pickup_min, request.restaurant_prep_min)
    return pickup_ready_min + to_dropoff_min


@router.post("/decide", response_model=DecideResponse)
async def decide(request: DecideRequest, background: BackgroundTasks) -> DecideResponse:
    """Nunca debe devolver 500: un crash en la ventana de decision es un
    hard failure de Feasibility (evaluation_protocol.md seccion 7). El
    parseo del body ya paso por pydantic antes de llegar aqui (422 si el
    request esta mal formado, lo cual es comportamiento esperado del
    framework, no un crash); lo que este try/except cubre es cualquier
    excepcion INESPERADA una vez que el request ya es valido -- un vehicle
    profile faltante, un override raro, etc. -- para responder SKIP con un
    reason honesto en vez de propagarla.
    """
    t0 = time.perf_counter()
    strategy = STRATEGY.snapshot()  # lectura de atributo: no bloquea, no falla
    record: DecisionRecord | None = None

    try:
        overrides = request.courier_state_overrides
        profile = profile_for(request.vehicle)

        total_time_min = _order_total_time_min(request)
        in_flight_weight, in_flight_volume = in_flight_totals(overrides.in_flight_orders)
        queue_offset = queue_offset_min(overrides.in_flight_orders, request.sim_time)

        verdict = evaluate_safety_full(
            vehicle=request.vehicle,
            weight_kg=request.weight_kg,
            volume_liters=request.volume_liters,
            sim_time=request.sim_time,
            zone_dropoff=request.zone_dropoff,
            continuous_riding_min=overrides.continuous_riding_min,
            order_total_time_min=total_time_min,
            shift_end_time=overrides.shift_end_time,
            in_flight_weight_kg=in_flight_weight,
            in_flight_volume_liters=in_flight_volume,
            last_break_end_time=overrides.last_break_end_time,
            queue_offset_min=queue_offset,
        )

        economics = evaluate_economics(
            base_pay_mxn=request.base_pay_mxn,
            est_tip_mxn=request.est_tip_mxn,
            surge_multiplier=request.surge_multiplier,
            total_time_min=total_time_min,
            deadhead_km=request.distance_pickup_km,
            delivery_km=request.distance_delivery_km,
            profile=profile,
            zone_dropoff=request.zone_dropoff,
            reservation_wage_mxn_hr=strategy.reservation_wage_mxn_hr,
        )

        economic_accept = economics.adjusted_rate_mxn_hr >= economics.reservation_wage_mxn_hr
        economic_reason = (
            reasons.accepted(economics.adjusted_rate_mxn_hr, economics.reservation_wage_mxn_hr)
            if economic_accept
            else reasons.reservation_wage(
                economics.adjusted_rate_mxn_hr,
                economics.reservation_wage_mxn_hr,
                economics.deadhead_km,
            )
        )

        # La seguridad manda. Si `verdict.blocked`, los tres argumentos
        # economicos se ignoran: la invariante safety-over-pay vive aqui, no
        # en el orden de unos `if` que alguien pueda reordenar sin querer.
        decision, reason, binding_constraint = combine(
            verdict,
            economic_accept=economic_accept,
            economic_reason=economic_reason,
            economic_binding=None if economic_accept else "reservation_wage",
        )

        economics_breakdown = EconomicsBreakdown(**economics.__dict__)
        record = DecisionRecord(
            order=request,
            overrides=overrides,
            verdict=verdict,
            decision=decision,
            reason=reason,
            binding_constraint=binding_constraint,
            latency_ms=0.0,  # se fija abajo, cuando el reloj ya paro
            tier="tier1",
            degraded=strategy.degraded,
            economics=economics.__dict__,
            strategy={
                "reservation_wage_mxn_hr": strategy.reservation_wage_mxn_hr,
                "target_zone": strategy.target_zone,
                "confidence": strategy.confidence,
                "revision": strategy.revision,
                "source": strategy.source,
                "reasoning": strategy.reasoning,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- deliberado, ver docstring
        decision, binding_constraint = "SKIP", None
        reason = reasons.cap_words(f"error interno al evaluar la oferta: {exc}")
        economics_breakdown = None

    latency_ms = (time.perf_counter() - t0) * 1000

    if record is not None:
        # `record` es frozen: se reemplaza por una copia con la latencia real.
        # Medir primero y registrar despues es lo correcto -- el numero que se
        # reporta tiene que incluir todo el trabajo, no todo menos el ultimo paso.
        JOURNAL.record(
            DecisionRecord(**{**record.__dict__, "latency_ms": latency_ms})
        )

    response = DecideResponse(
        order_id=request.order_id,
        decision=decision,
        reason=reason,
        binding_constraint=binding_constraint,
        latency_ms=latency_ms,
        tier="tier1",  # las constraints de seguridad salen siempre del fast path
        degraded=strategy.degraded,
        economics=economics_breakdown,
    )

    # ENTRE pings, nunca dentro: la tarea de fondo corre despues de que este
    # response ya salio, y `maybe_refresh` ademas solo despacha (no espera al
    # modelo) y respeta su intervalo en tiempo de simulacion. El protocolo es
    # explicito: la capa de estrategia "runs between pings, never inside a
    # decision window".
    background.add_task(_refresh_strategy, request)

    return response


def _refresh_strategy(request: DecideRequest) -> None:
    """Le da a tier2 la oportunidad de revisar sus parametros.

    Nada de lo que pase aqui puede afectar a la decision que ya se devolvio.
    Si el modelo esta caido, `maybe_refresh` marca degradado y el siguiente
    /decide lo reporta; ningun pedido espera por esto.
    """
    STRATEGY.maybe_refresh(
        request.sim_time,
        {
            "sim_time": request.sim_time.isoformat(),
            "vehicle": request.vehicle,
            "zona_actual": request.zone_pickup,
            "decisiones_registradas": len(JOURNAL),
            "ultimas_decisiones": [
                {"decision": r.decision, "binding_constraint": r.binding_constraint}
                for r in JOURNAL.recent(10)
            ],
        },
    )


@router.get("/explain_decision/{order_id}", response_model=ExplainDecisionResponse)
async def explain_decision(order_id: str) -> ExplainDecisionResponse:
    """"Why did you skip that order?" — contestado desde la bitacora, con los
    numeros del momento de decidir, sin volver a correr el sistema."""
    payload = JOURNAL.explain(order_id)
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"no hay decision registrada para order_id={order_id!r}"
        )
    return ExplainDecisionResponse(**payload)


@router.get("/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    """Salud del servicio, incluido el modo degradado.

    El protocolo exige que la caida del modelo se **señale**; un fallback
    silencioso solo da credito parcial. Se señala por tres canales: el campo
    `degraded` de cada respuesta de /decide, el evento `strategy_update` del
    event log, y este endpoint.
    """
    health = STRATEGY.status()
    params = STRATEGY.snapshot()
    return StatusResponse(
        degraded=health.degraded,
        tier="tier1",
        reservation_wage_mxn_hr=params.reservation_wage_mxn_hr,
        strategy_revision=health.revision,
        strategy_source=params.source,
        strategy_reasoning=params.reasoning,
        consecutive_model_failures=health.consecutive_failures,
        last_model_error=health.last_error,
        advisor=health.advisor,
        decisions_recorded=len(JOURNAL),
    )
