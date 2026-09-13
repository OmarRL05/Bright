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
2. `SHOCKS.effects` -- los shocks vigentes en ESTE sim_time. Lectura de una
   tupla inmutable, sin lock: un shock inyectado en vivo cambia la siguiente
   decision sin estancar el loop (protocolo, seccion 5).
3. Se estima cuanto tarda la oferta -- ya con lluvia, cierre y retraso
   aplicados -- y cuanto falta para terminar lo que ya trae en vuelo.
4. `evaluate_safety_full` -- las 5 constraints duras. No ve el pago.
5. `evaluate_economics` -- tasa efectiva contra el salario de reserva vigente.
6. `combine` -- une las dos. Si la seguridad bloqueo, lo economico se ignora
   por completo; no hay camino de codigo que produzca ACCEPT con una
   constraint violada.
7. `JOURNAL.record` -- O(1), sin formateo, para poder explicar despues.

Nada de esto toca la red, el disco ni un modelo. La capa de estrategia (tier2)
corre en otro hilo, entre pings, y aqui solo se **lee** lo que haya publicado.
Los shocks siguen el mismo patron: `POST /shock` escribe, el fast path lee.

VEHICLE_PROFILES viene de core.models (P0.5, Abraham) y ya trae limites de
peso/volumen. El motor VRPTW de coordenadas (Bloque 3, decision.py/greedy.py)
sigue sin hablar el mismo modelo de zonas enteras que este endpoint -- este
usa `distance_pickup_km`/`distance_delivery_km` tal como llegan en el
request en vez de resolver `zone_pickup`/`zone_dropoff` a coordenadas, para
no depender de que un juez use las mismas zonas que nuestro simulador
interno genera (ver docs/03_Integracion_API_Decide.md).

La UNICA resolucion de zona que si se hace aqui es `_zone_demand()`: busca
`zone_pickup` en `DEFAULT_ZONE_MAP` (16 zonas) para ajustar el salario de
reserva por demanda -- si el juez manda una zona que no conocemos, cae a
demanda neutral (`zone_known: false` queda explicito en explain_decision,
no falla callado).
"""

import time
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api.schemas import (
    ActiveShocksResponse,
    DecideRequest,
    DecideResponse,
    EconomicsBreakdown,
    ExplainDecisionResponse,
    ShockRequest,
    ShockResponse,
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
from core.agent.safety import SafetyVerdict, combine, evaluate_safety_full, profile_for
from core.agent.shocks import SHOCKS, ShockEffects, shock_from_payload
from core.agent.strategy import STRATEGY

router = APIRouter(tags=["decide"])


def _order_total_time_min(
    request: DecideRequest, effects: ShockEffects | None = None
) -> float:
    """Tiempo total estimado (min) de esta oferta: pickup + entrega.

    Usa estimated_pickup_min/estimated_delivery_min si vienen en el request
    (event_log_schema.json los documenta como el valor preferente); si no,
    deriva de distancia + velocidad del perfil de vehiculo, tal como pide el
    schema ("if absent, derive from distance and speed").

    `effects` son los shocks vigentes (evaluation_protocol.md seccion 5). La
    lluvia baja la velocidad y el cierre alarga los km, asi que los dos se
    aplican AQUI, sobre el tiempo -- no sobre el dinero. Es lo que hace que un
    shock pueda empujar una entrega mas alla del fin de turno y disparar
    `shift_end_infeasible`: la reaccion al shock pasa por el gate de
    seguridad, no lo esquiva. El retraso de restaurante (`delay`) se suma a la
    preparacion del pedido que nombra.
    """
    profile = profile_for(request.vehicle)
    effects = effects or ShockEffects()

    # <=0 no puede pasar (RAIN_SPEED_FACTOR es una constante del modulo), pero
    # dividir entre cero en la ventana de decision seria un fallo duro: el
    # max() cuesta nada y quita el unico camino que lleva ahi.
    speed_kmh = profile.avg_speed_kmh * max(0.01, effects.speed_factor)
    distance_factor = max(0.0, effects.distance_factor)

    to_pickup_min = request.estimated_pickup_min
    if to_pickup_min is None:
        to_pickup_min = request.distance_pickup_km * distance_factor / speed_kmh * 60.0
    else:
        to_pickup_min = to_pickup_min * distance_factor / max(0.01, effects.speed_factor)

    to_dropoff_min = request.estimated_delivery_min
    if to_dropoff_min is None:
        to_dropoff_min = request.distance_delivery_km * distance_factor / speed_kmh * 60.0
    else:
        to_dropoff_min = to_dropoff_min * distance_factor / max(0.01, effects.speed_factor)

    prep_min = request.restaurant_prep_min + max(0.0, effects.extra_prep_min)

    # El repartidor puede llegar al pickup antes de que la orden este lista;
    # el reloj efectivo de esa etapa es el mayor de los dos.
    pickup_ready_min = max(to_pickup_min, prep_min)
    return pickup_ready_min + to_dropoff_min


@router.post("/decide", response_model=DecideResponse)
async def decide(request: DecideRequest, background: BackgroundTasks) -> DecideResponse:
    """Transporte HTTP. La decision entera vive en `decide_request`.

    La separacion no es cosmetica: el replay del protocolo (seccion 6) tiene
    que reproducir un turno contra **el sistema que corre**, no contra una
    copia parecida. Llamando los dos a la misma funcion, "es el mismo codigo"
    es una propiedad del programa y no una promesa del README.

    Lo unico que queda aqui es lo que de verdad es transporte: el refresco de
    tier2 como tarea de fondo, que corre DESPUES de que la respuesta salio.
    """
    response = decide_request(request)
    # ENTRE pings, nunca dentro: la tarea de fondo corre despues de que este
    # response ya salio, y `maybe_refresh` ademas solo despacha (no espera al
    # modelo) y respeta su intervalo en tiempo de simulacion. El protocolo es
    # explicito: la capa de estrategia "runs between pings, never inside a
    # decision window".
    background.add_task(_refresh_strategy, request)
    return response


def decide_request(
    request: DecideRequest,
    *,
    reservation_wage_mxn_hr: float | None = None,
    record: bool = True,
) -> DecideResponse:
    """La decision completa, sincrona y sin transporte.

    Nunca debe devolver 500: un crash en la ventana de decision es un
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

    # El arnes de evaluacion fija el umbral por politica y no quiere ensuciar
    # la bitacora con 17 mil decisiones de calibracion. Los dos parametros
    # existen para que el arnes pueda llamar a ESTA funcion en vez de tener su
    # propia copia de la logica -- tener dos caminos de decision es como se
    # acaba reportando una tabla que describe un sistema distinto del que los
    # jueces prueban.
    wage = reservation_wage_mxn_hr if reservation_wage_mxn_hr is not None else strategy.reservation_wage_mxn_hr
    registro: DecisionRecord | None = None

    try:
        overrides = request.courier_state_overrides
        profile = profile_for(request.vehicle)

        # Shocks vigentes EN EL sim_time de este ping. Lectura de una tupla
        # inmutable: no toma lock, no toca la red, no puede estancar el loop
        # (evaluation_protocol.md seccion 5, "must react without stalling").
        effects = SHOCKS.effects(
            request.sim_time,
            zone_pickup=request.zone_pickup,
            zone_dropoff=request.zone_dropoff,
            order_id=request.order_id,
        )

        total_time_min = _order_total_time_min(request, effects)
        in_flight_weight, in_flight_volume = in_flight_totals(overrides.in_flight_orders)
        queue_offset = queue_offset_min(
            overrides.in_flight_orders, request.sim_time, overrides.unavailable_until
        )

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

        # El surge del shock NO se apila sobre el del request: se toma el
        # mayor de los dos. Son la misma senal (lo que la plataforma paga de
        # mas en esa zona) por dos vias distintas, y multiplicarlas volveria
        # rentable cualquier pedido con un solo curl.
        surge_multiplier = max(request.surge_multiplier, effects.surge_multiplier)
        # Un cierre obliga a rodear: mas km reales, mas combustible. El mismo
        # factor que ya alargo el tiempo arriba alarga aqui el kilometraje.
        deadhead_km = request.distance_pickup_km * effects.distance_factor
        delivery_km = request.distance_delivery_km * effects.distance_factor

        economics = evaluate_economics(
            base_pay_mxn=request.base_pay_mxn,
            est_tip_mxn=request.est_tip_mxn,
            surge_multiplier=surge_multiplier,
            total_time_min=total_time_min,
            deadhead_km=deadhead_km,
            delivery_km=delivery_km,
            profile=profile,
            zone_dropoff=request.zone_dropoff,
            reservation_wage_mxn_hr=wage,
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

        # El shock se nombra DESPUES de combine(), nunca antes: anexar texto no
        # puede cambiar el veredicto, y ponerlo aqui deja claro que la nota es
        # explicacion, no entrada. Si la seguridad bloqueo, el reason sigue
        # siendo el de la constraint y el shock queda como contexto.
        reason = reasons.note_shocks(reason, effects.applied)

        economics_breakdown = EconomicsBreakdown(**economics.__dict__)
        registro = DecisionRecord(
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
            # Solo numeros ya calculados y la tupla de descripciones que
            # `effects` ya traia: registrar no formatea nada, igual que el
            # resto del record (ver nota de modulo de journal.py). La lista
            # completa de shocks vigentes se pide a `GET /shocks`, que corre
            # fuera de la ventana de decision.
            shocks={
                "applied": list(effects.applied),
                "surge_multiplier": surge_multiplier,
                "request_surge_multiplier": request.surge_multiplier,
                "distance_factor": effects.distance_factor,
                "speed_factor": effects.speed_factor,
                "extra_prep_min": effects.extra_prep_min,
            },
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
        reason = reasons.internal_error()
        economics_breakdown = None
        # `effects` se define tambien en el camino de error: el cuerpo de
        # abajo lo lee para armar la respuesta, y sin esto un fallo temprano se
        # convertiria en un NameError -- o sea, el try/except que existe para
        # que nunca haya un 500 acabaria provocando uno.
        effects = ShockEffects()

        # Se registra igual, con un veredicto vacio: si no, un juez que
        # pregunte "¿por que saltaste esa?" justo despues de un error interno
        # recibe un 404, que parece que perdimos la decision en vez de que la
        # tomamos mal. El texto de la excepcion vive en el journal, no en el
        # `reason` que se lee en voz alta.
        registro = DecisionRecord(
            order=request,
            overrides=request.courier_state_overrides,
            verdict=SafetyVerdict(),
            decision=decision,
            reason=reason,
            binding_constraint=None,
            latency_ms=0.0,
            degraded=strategy.degraded,
            economics={"error": f"{type(exc).__name__}: {exc}"},
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    if record and registro is not None:
        # `registro` es frozen: se reemplaza por una copia con la latencia
        # real. Medir primero y registrar despues es lo correcto -- el numero
        # que se reporta tiene que incluir todo el trabajo, no todo menos el
        # ultimo paso.
        JOURNAL.record(
            DecisionRecord(**{**registro.__dict__, "latency_ms": latency_ms})
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
        shocks_applied=list(effects.applied),
    )

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


@router.post("/shock", response_model=ShockResponse)
async def shock(request: ShockRequest) -> ShockResponse:
    """Inyeccion de un shock en vivo.

        "At least one shock during the demo is required by the brief. Judges
         may inject shocks live... Your system must react without stalling
         the decision loop."
        -- evaluation_protocol.md, seccion 5

    **Este es el endpoint que los jueces pinchan.** El body es el MISMO objeto
    que el evento `shock` del event log, asi que una linea copiada de un log
    grabado entra tal cual, sin traducirla.

    Lo que hace y lo que deliberadamente NO hace
    ---------------------------------------------
    Registra el shock y devuelve como quedo. Nada mas: no recalcula
    decisiones pasadas, no encola trabajo, no llama al modelo. La reaccion
    ocurre en el siguiente `/decide` -- que lee la tupla de shocks vigentes
    como atributo, sin lock -- porque estancar el loop de decision para
    procesar el shock es precisamente lo que el protocolo prohibe.

    Tampoco devuelve 500 nunca, por la misma razon que `/decide`: un juez que
    inyecta un shock y recibe un error no sabe si el sistema lo registro. Un
    `shock_type` fuera del enum oficial lo rechaza pydantic con 422 antes de
    llegar aqui, que es informacion util y no un crash.
    """
    shock = shock_from_payload(request.model_dump())
    if shock is None:
        # Inalcanzable con el Literal de pydantic delante, pero el contrato de
        # `shock_from_payload` permite None y confiar en que otra capa valide
        # es como se cuelan los 500.
        raise HTTPException(status_code=422, detail=f"shock_type invalido: {request.shock_type!r}")

    registered = SHOCKS.inject(shock)

    clamped = (
        request.multiplier is not None
        and registered.multiplier is not None
        and registered.multiplier != request.multiplier
    )
    note = registered.describe()
    if clamped:
        note += f" (multiplicador {request.multiplier:.1f} recortado a {registered.multiplier:.1f})"
    if request.duration_min is None:
        note += f" (duracion por defecto {registered.duration_min:.0f} min)"
    if registered.sim_time is None:
        note += " (sin sim_time: vigente para toda decision hasta limpiarlo)"

    return ShockResponse(
        accepted=True,
        shock=registered.to_event(),
        active_shocks=len(SHOCKS),
        expires_at=registered.expires_at(),
        note=note,
    )


@router.get("/shocks", response_model=ActiveShocksResponse)
async def shocks(at: datetime | None = None) -> ActiveShocksResponse:
    """Que shocks estan vigentes, en formato de evento oficial.

    `at` es el momento de SIMULACION contra el que se evalua la vigencia (no
    el reloj de pared: este servicio no lo consulta en ningun lado). Sin `at`
    se listan todos los registrados, porque no hay contra que medir la
    ventana.

    Sirve para dos cosas durante la demo: leer en voz alta lo que esta
    mordiendo ahora, y comprobar que un shock caduco solo cuando paso su
    duracion en vez de tener que creerselo.
    """
    active = SHOCKS.active(at) if at is not None else SHOCKS.all_shocks()
    return ActiveShocksResponse(
        active=[s.to_event() for s in active],
        history=[s.to_event() for s in SHOCKS.history()],
        evaluated_at=at,
    )


@router.delete("/shocks", response_model=ActiveShocksResponse)
async def clear_shocks() -> ActiveShocksResponse:
    """Vacia los shocks vigentes. El historial se conserva.

    Existe para el ensayo: entre dos pasadas de la demo hay que poder volver
    al estado limpio sin reiniciar el proceso, y sin perder el registro de lo
    que ya se inyecto.
    """
    SHOCKS.clear()
    return ActiveShocksResponse(
        active=[],
        history=[s.to_event() for s in SHOCKS.history()],
        evaluated_at=None,
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


def decide_sync(request: DecideRequest) -> dict:
    """`decide_request` devuelto como dict, para el driver de replay.

    El replay compara contra los campos del event log, que son dicts; pedirle
    que hable pydantic solo para volver a serializar seria ruido.
    """
    return decide_request(request).model_dump()
