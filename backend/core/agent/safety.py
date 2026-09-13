"""Gate de seguridad (tier1) — las 5 constraints duras del protocolo.

    "Safety constraints must be enforced in code. Not as instructions in a
     model prompt. Judges will ask you to open the file where each limit is
     defined."
    -- student-materials/courier/README.md

**Este es ese archivo.** Los cinco limites viven en la seccion LIMITES, juntos
y arriba de todo, con nombre. No hay un solo numero magico enterrado en la
logica de abajo.

| # | constraint del protocolo               | id oficial             | limite |
|---|----------------------------------------|------------------------|--------|
| 1 | No dropoff en zona marcada tras 22:00  | `flagged_zone_night`   | `FLAGGED_ZONES`, `CURFEW_START_HOUR` |
| 2 | Pausa obligatoria tras 4 h continuas   | `mandatory_break`      | `MAX_CONTINUOUS_RIDING_MIN`, `MANDATORY_BREAK_MIN` |
| 3 | Calor: 90 min continuos de 12 a 16     | `heat_rule`            | `HEAT_WINDOW_*`, `HEAT_MAX_CONTINUOUS_RIDING_MIN` |
| 4 | Rechazar lo que no cierra antes del fin | `shift_end_infeasible` | `SHIFT_END_SAFETY_MARGIN_MIN` |
| 5 | Peso y volumen por vehiculo            | `vehicle_capacity`     | `VEHICLE_PROFILES` (contracts.py) |

Tres propiedades de diseño, y por que cada una existe
-----------------------------------------------------

**1. Es una funcion pura.** Sin I/O, sin red, sin disco, sin `datetime.now()`,
sin estado mutable de modulo. Todo lo que necesita entra por parametro. Eso
resuelve de un golpe tres requisitos que normalmente se persiguen por
separado: la latencia (aritmetica en microsegundos, contra un presupuesto de
50 ms), el determinismo del replay (misma entrada -> misma salida, siempre), y
"enforced in code" (no hay prompt que valga: son `if`s sobre constantes).

**2. Nunca mira el pago.** `evaluate_safety` no recibe ni consulta
`base_pay_mxn`, `surge_multiplier` ni la propina. Es lo que hace verdadera la
invariante *Safety-over-pay* que los jueces prueban a proposito (protocolo
seccion 3): un refusal de seguridad **no puede** volverse ACCEPT porque mejore
la paga, porque esta capa no tiene forma de enterarse de que mejoro. No es
disciplina del equipo, es que el dato no esta a la vista.

**3. Evalua las cinco, siempre.** No corta en la primera violacion. Cuesta
microsegundos y compra la respuesta a "¿y ademas chocaba con X?": el veredicto
trae `violations` completo, no solo la que se reporta.

Precedencia
-----------
Cuando varias violan a la vez hay que reportar UNA en `binding_constraint`. El
orden no es arbitrario: se reporta **la mas dificil de relajar por el pedido**.

    mandatory_break > heat_rule > flagged_zone_night > vehicle_capacity > shift_end_infeasible

Las dos primeras dependen solo del repartidor — ningun pedido distinto las
arregla, hay que parar. `flagged_zone_night` depende del destino, y otro
destino la evita. `vehicle_capacity` depende del tamaño, y un pedido mas chico
la evita. `shift_end_infeasible` es la mas blanda: depende de una estimacion, y
un pedido mas corto la evita. Reportar primero la que menos alternativas deja
es lo que hace que el reason le sirva de verdad al repartidor.

Datos faltantes
---------------
El PROBE de `validate_format.py` no manda estado ni horas. Regla general: una
constraint que no tiene los datos para evaluarse **no dispara**. Es la
direccion segura para el formato (el endpoint responde 200 con una decision
valida) y la honesta para el juicio (no inventamos una violacion que no
podemos sostener con numeros). Cada caso esta marcado abajo con `# sin datos`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from core.agent import reasons
from core.agent.contracts import (
    BindingConstraint,
    CourierRuntimeState,
    Decision,
    OrderRequest,
    VehicleProfile,
)

# ==========================================================================
# LIMITES  --  los cinco limites de seguridad del protocolo, en un solo lugar.
#
# Cambiar un numero de aqui cambia el comportamiento del agente y nada mas:
# ninguna de estas cantidades esta repetida mas abajo. Es a proposito, para
# que la demo pueda editarlos en vivo ("¿que pasa si muevo esto?") y para que
# "abre el archivo donde esta definido el limite" se responda señalando esta
# seccion.
# ==========================================================================

# --- Constraint 1: zonas marcadas de noche --------------------------------
#: Zonas consideradas inseguras de noche. El protocolo no las fija (el
#: simulador es nuestro): esta lista es una decision del equipo y se coordina
#: con las zonas que genera Bloque 1.
FLAGGED_ZONES: frozenset[int] = frozenset({3, 9, 14})

#: El toque de queda empieza a esta hora. "No dropoff in flagged zones after
#: 22:00" (protocolo, constraint 1).
CURFEW_START_HOUR = 22

#: ...y termina a esta. El protocolo solo nombra el inicio; el cierre a las
#: 06:00 es extension nuestra, porque una entrega a la 01:00 en zona marcada
#: no es mas segura que a las 22:30. Un turno que no cruza medianoche nunca
#: toca esta constante.
CURFEW_END_HOUR = 6

# --- Constraint 2: pausa obligatoria --------------------------------------
#: 4 horas continuas al volante. Pasado esto, toca parar.
MAX_CONTINUOUS_RIDING_MIN = 240.0

#: Duracion de la pausa obligatoria.
MANDATORY_BREAK_MIN = 20.0

# --- Constraint 3: regla de calor -----------------------------------------
#: Franja de calor [inicio, fin) en hora local del turno.
HEAT_WINDOW_START_HOUR = 12
HEAT_WINDOW_END_HOUR = 16

#: Dentro de la franja de calor el tope de manejo continuo baja de 240 a esto.
HEAT_MAX_CONTINUOUS_RIDING_MIN = 90.0

# --- Constraint 4: fin de turno -------------------------------------------
#: Colchon antes del cierre del turno. La entrega debe terminar al menos estos
#: minutos antes de `shift_end_time`: una estimacion que cae exactamente en la
#: hora de cierre no es "a tiempo", es un volado.
SHIFT_END_SAFETY_MARGIN_MIN = 5.0

# --- Constraint 5: capacidad ----------------------------------------------
#: Los limites de peso y volumen viven en VEHICLE_PROFILES (contracts.py),
#: junto con la velocidad, porque los tres son el mismo perfil de vehiculo y
#: separarlos invitaba a que se desincronizaran.

# ==========================================================================
# Precedencia. Orden fijo y documentado: sin esto, "cual constraint reporto"
# seria no-determinista y el replay podria diferir en `binding_constraint`
# aunque la decision ACCEPT/SKIP coincidiera.
# ==========================================================================

CONSTRAINT_PRECEDENCE: tuple[BindingConstraint, ...] = (
    "mandatory_break",
    "heat_rule",
    "flagged_zone_night",
    "vehicle_capacity",
    "shift_end_infeasible",
)

_PRECEDENCE_RANK = {name: i for i, name in enumerate(CONSTRAINT_PRECEDENCE)}


# ==========================================================================
# Tipos de salida
# ==========================================================================


@dataclass(frozen=True)
class SafetyViolation:
    """Una constraint que violo, con el numero que la hizo violar."""

    constraint: BindingConstraint
    #: Ya formateado y garantizado <40 palabras por core.agent.reasons.
    reason: str
    #: Numeros crudos del chequeo, para `explain_decision` y para el dashboard.
    #: Nunca se usa para decidir; es evidencia, no entrada.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyVerdict:
    """Resultado del gate. `violations` viene ordenado por precedencia."""

    violations: tuple[SafetyViolation, ...] = ()
    #: La estimacion de cierre que usaron las constraints de horario. Se
    #: expone porque `explain_decision` pide `time_to_completion` dentro de
    #: `inputs`: calcularla y tirarla obligaria a recalcularla al explicar, y
    #: el numero explicado tiene que ser EL que se uso para decidir, no uno
    #: parecido calculado despues.
    estimate: CompletionEstimate | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.violations)

    @property
    def binding(self) -> SafetyViolation | None:
        """La violacion que se reporta en `binding_constraint`."""
        return self.violations[0] if self.violations else None

    @property
    def constraints(self) -> tuple[BindingConstraint, ...]:
        return tuple(v.constraint for v in self.violations)


# ==========================================================================
# Estimacion de tiempos
#
# Dos constraints (zona marcada de noche y fin de turno) necesitan saber
# CUANDO terminaria la entrega. Eso es trabajo de ruteo, no de seguridad, asi
# que entra por un Protocol -- mismo patron que `DistanceProvider` en
# core/routing/, que es el desacople que mejor ha aguantado en este repo.
#
# Consecuencia practica: safety.py se testea con un estimador falso de tres
# lineas, y el dia que `greedy.cheapest_insertion` calcule esto de verdad
# (con la revalidacion de ventanas aguas abajo del hallazgo 1) se cambia la
# instancia que se inyecta y aqui no se toca nada.
# ==========================================================================


@dataclass(frozen=True)
class CompletionEstimate:
    """Cuanto falta, desde `sim_time`, para cerrar esta entrega."""

    #: Minutos hasta el dropoff de ESTA oferta, ya contando el trabajo en curso.
    minutes_to_dropoff: float
    #: Minutos efectivos sobre la moto/bici (sin contar esperas).
    riding_minutes: float = 0.0
    #: Minutos parado esperando al restaurante.
    waiting_minutes: float = 0.0
    #: Minutos que hay que esperar a terminar lo ya aceptado antes de arrancar.
    queue_offset_minutes: float = 0.0


class CompletionEstimator(Protocol):
    def estimate(self, order: OrderRequest, state: CourierRuntimeState) -> CompletionEstimate:
        """Estimacion de cierre de `order` dado el estado actual."""
        ...


class ProfileSpeedEstimator:
    """Estimador por defecto: velocidad del perfil de vehiculo, secuencial.

    Modelo explicito (es lo que se responde cuando un juez pide la aritmetica):

        cola     = max(0, ultimo eta_dropoff en vuelo - sim_time)
        traslado = distance_pickup_km  / speed_kmh * 60     (deadhead)
        espera   = max(0, restaurant_prep_min - traslado)   (el prep corre en paralelo
                                                             mientras vas en camino)
        entrega  = distance_delivery_km / speed_kmh * 60
        total    = cola + traslado + espera + entrega

    `estimated_pickup_min` / `estimated_delivery_min` del payload, si vienen,
    ganan sobre el calculo por distancia: el schema los ofrece como dato
    autoritativo ("if absent, derive from distance and speed").

    El termino `cola` es lo que hace que la constraint de fin de turno vea la
    RUTA COMBINADA y no solo el pedido suelto -- la categoria de sondeo
    "Stacking and route feasibility" del protocolo.
    """

    def estimate(self, order: OrderRequest, state: CourierRuntimeState) -> CompletionEstimate:
        profile: VehicleProfile = order.profile
        speed = profile.speed_kmh or 1.0

        if order.estimated_pickup_min is not None:
            to_pickup = float(order.estimated_pickup_min)
        else:
            to_pickup = (order.distance_pickup_km / speed) * 60.0

        if order.estimated_delivery_min is not None:
            to_dropoff = float(order.estimated_delivery_min)
        else:
            to_dropoff = (order.distance_delivery_km / speed) * 60.0

        waiting = max(0.0, order.restaurant_prep_min - to_pickup)
        queue = _queue_offset_minutes(order.sim_time, state)

        return CompletionEstimate(
            minutes_to_dropoff=queue + to_pickup + waiting + to_dropoff,
            riding_minutes=to_pickup + to_dropoff,
            waiting_minutes=waiting,
            queue_offset_minutes=queue,
        )


def _queue_offset_minutes(sim_time: datetime | None, state: CourierRuntimeState) -> float:
    """Minutos que falta para terminar lo ya aceptado."""
    if sim_time is None:
        return 0.0  # sin datos: no inventamos cola
    etas = [o.eta_dropoff for o in state.in_flight_orders if o.eta_dropoff is not None]
    if not etas:
        return 0.0
    latest = max(etas)
    return max(0.0, (latest - sim_time).total_seconds() / 60.0)


_DEFAULT_ESTIMATOR = ProfileSpeedEstimator()


# ==========================================================================
# Utilidades de tiempo
# ==========================================================================


def effective_continuous_riding_min(
    state: CourierRuntimeState, sim_time: datetime | None
) -> float:
    """Minutos de manejo continuo, reconciliando contador y ultima pausa.

    Los jueces mandan `courier_state_overrides` armados a mano, asi que las dos
    señales pueden contradecirse: `continuous_riding_min=250` junto a un
    `last_break_end_time` de hace 10 minutos. Fisicamente no puedes llevar 250
    minutos seguidos manejando si paraste hace 10, asi que gana el minimo de
    las dos. La alternativa (creerle solo al contador) haria disparar la pausa
    obligatoria justo despues de una pausa: un falso positivo que el sondeo de
    "Continuous-riding safeguards" encuentra enseguida.
    """
    counter = max(0.0, state.continuous_riding_min)

    if state.last_break_end_time is None or sim_time is None:
        return counter

    since_break = (sim_time - state.last_break_end_time).total_seconds() / 60.0
    if since_break < 0:
        return counter  # pausa en el futuro: dato incoherente, se ignora
    return min(counter, since_break)


def _in_curfew(moment: datetime) -> bool:
    """¿`moment` cae dentro del toque de queda nocturno?

    La ventana cruza medianoche (22:00 -> 06:00), por eso es un OR y no un
    rango: `hour >= 22 or hour < 6`.
    """
    return moment.hour >= CURFEW_START_HOUR or moment.hour < CURFEW_END_HOUR


def _in_heat_window(moment: datetime) -> bool:
    """Franja de calor [12:00, 16:00). Cerrada abajo, abierta arriba: a las
    16:00 en punto la regla ya no aplica."""
    return HEAT_WINDOW_START_HOUR <= moment.hour < HEAT_WINDOW_END_HOUR


# ==========================================================================
# Las cinco constraints. Una funcion cada una, cada una devuelve
# SafetyViolation o None. Ninguna recibe el pago.
# ==========================================================================


def _check_mandatory_break(
    state: CourierRuntimeState, sim_time: datetime | None
) -> SafetyViolation | None:
    """Constraint 2: pausa obligatoria de 20 min tras 4 h continuas.

    Dispara con `>=`: 240 minutos EXACTOS ya obligan a parar. El limite es un
    tope, no una meta.
    """
    riding = effective_continuous_riding_min(state, sim_time)
    if riding < MAX_CONTINUOUS_RIDING_MIN:
        return None
    return SafetyViolation(
        constraint="mandatory_break",
        reason=reasons.mandatory_break(riding, MAX_CONTINUOUS_RIDING_MIN, MANDATORY_BREAK_MIN),
        detail={
            "continuous_riding_min": riding,
            "limit_min": MAX_CONTINUOUS_RIDING_MIN,
            "required_break_min": MANDATORY_BREAK_MIN,
            "raw_counter_min": state.continuous_riding_min,
            "last_break_end_time": _iso(state.last_break_end_time),
        },
    )


def _check_heat_rule(
    state: CourierRuntimeState, sim_time: datetime | None
) -> SafetyViolation | None:
    """Constraint 3: de 12:00 a 16:00 el tope continuo baja a 90 min.

    Se evalua sobre la hora del ping. Dispara con `>=`, igual que la pausa.
    """
    if sim_time is None:
        return None  # sin datos: sin hora no se puede saber si es la franja
    if not _in_heat_window(sim_time):
        return None

    riding = effective_continuous_riding_min(state, sim_time)
    if riding < HEAT_MAX_CONTINUOUS_RIDING_MIN:
        return None
    return SafetyViolation(
        constraint="heat_rule",
        reason=reasons.heat_rule(
            riding, HEAT_MAX_CONTINUOUS_RIDING_MIN, HEAT_WINDOW_START_HOUR, HEAT_WINDOW_END_HOUR
        ),
        detail={
            "continuous_riding_min": riding,
            "limit_min": HEAT_MAX_CONTINUOUS_RIDING_MIN,
            "sim_time": _iso(sim_time),
            "window": f"{HEAT_WINDOW_START_HOUR:02d}:00-{HEAT_WINDOW_END_HOUR:02d}:00",
        },
    )


def _check_flagged_zone_night(
    order: OrderRequest, sim_time: datetime | None, estimate: CompletionEstimate
) -> SafetyViolation | None:
    """Constraint 1: nada de dropoffs en zona marcada despues de las 22:00.

    Se evalua contra la **hora estimada de llegada al dropoff**, no contra la
    hora del ping. Es la diferencia entre la regla que queremos y una que se
    burla sola: aceptar a las 21:50 un pedido que entrega a las 22:40 dejaria
    al repartidor exactamente donde la regla dice que no debe estar.
    """
    if order.zone_dropoff is None or order.zone_dropoff not in FLAGGED_ZONES:
        return None
    if sim_time is None:
        return None  # sin datos: sin hora no hay toque de queda que evaluar

    arrival = sim_time + timedelta(minutes=estimate.minutes_to_dropoff)
    if not _in_curfew(arrival):
        return None
    return SafetyViolation(
        constraint="flagged_zone_night",
        reason=reasons.flagged_zone_night(order.zone_dropoff, arrival, CURFEW_START_HOUR),
        detail={
            "zone_dropoff": order.zone_dropoff,
            "flagged_zones": sorted(FLAGGED_ZONES),
            "estimated_arrival": _iso(arrival),
            "curfew": f"{CURFEW_START_HOUR:02d}:00-{CURFEW_END_HOUR:02d}:00",
            "minutes_to_dropoff": estimate.minutes_to_dropoff,
        },
    )


def _check_vehicle_capacity(
    order: OrderRequest, state: CourierRuntimeState
) -> SafetyViolation | None:
    """Constraint 5: peso y volumen del perfil del vehiculo.

    **Acumulado sobre la mochila**, no por pedido suelto: lo que importa es lo
    que el repartidor va a estar cargando al mismo tiempo. Un pedido de 2 kg es
    inofensivo solo o imposible si ya lleva 11 en una moto de 12.

    Dispara con `>` estricto: cargar exactamente el limite es legal. El limite
    es el maximo permitido, no el primer valor prohibido.
    """
    profile = order.profile

    checks = (
        ("peso", state.in_flight_weight_kg, order.weight_kg, profile.max_weight_kg, "kg"),
        (
            "volumen",
            state.in_flight_volume_liters,
            order.volume_liters,
            profile.max_volume_liters,
            "L",
        ),
    )

    for dimension, in_flight, incoming, limit, unit in checks:
        required = in_flight + incoming
        if required <= limit:
            continue
        return SafetyViolation(
            constraint="vehicle_capacity",
            reason=reasons.vehicle_capacity(
                profile.name, dimension, required, limit, unit, in_flight, incoming
            ),
            detail={
                "vehicle": profile.name,
                "dimension": dimension,
                "in_flight": in_flight,
                "incoming": incoming,
                "required": required,
                "limit": limit,
                "unit": unit,
            },
        )
    return None


def _check_shift_end(
    state: CourierRuntimeState, sim_time: datetime | None, estimate: CompletionEstimate
) -> SafetyViolation | None:
    """Constraint 4: rechazar lo que no cierra antes del fin de turno.

    Se compara la **hora de cierre estimada de la entrega** contra
    `shift_end_time` menos el margen -- no el costo marginal del pedido contra
    el tiempo restante. Esa segunda forma es el hallazgo 2 de la auditoria: seis
    pedidos con delta individual aceptable terminan 8 minutos despues del cierre
    porque nadie mira el acumulado. Como `estimate` ya incluye la cola del
    trabajo en vuelo, aqui el acumulado esta contemplado.

    `shift_end_time` sale del estado. El schema es explicito: "Read from state,
    never hardcoded - judges check this."
    """
    if sim_time is None or state.shift_end_time is None:
        return None  # sin datos: sin hora de cierre no hay nada que comparar

    completion = sim_time + timedelta(minutes=estimate.minutes_to_dropoff)
    deadline = state.shift_end_time - timedelta(minutes=SHIFT_END_SAFETY_MARGIN_MIN)
    if completion <= deadline:
        return None
    return SafetyViolation(
        constraint="shift_end_infeasible",
        reason=reasons.shift_end_infeasible(
            completion, state.shift_end_time, SHIFT_END_SAFETY_MARGIN_MIN
        ),
        detail={
            "estimated_completion": _iso(completion),
            "shift_end_time": _iso(state.shift_end_time),
            "safety_margin_min": SHIFT_END_SAFETY_MARGIN_MIN,
            "minutes_to_dropoff": estimate.minutes_to_dropoff,
            "queue_offset_minutes": estimate.queue_offset_minutes,
            "overshoot_min": (completion - deadline).total_seconds() / 60.0,
        },
    )


# ==========================================================================
# Entrada publica
# ==========================================================================


def evaluate_safety(
    order: OrderRequest,
    state: CourierRuntimeState,
    *,
    estimator: CompletionEstimator | None = None,
) -> SafetyVerdict:
    """Evalua las 5 constraints duras sobre `order` dado `state`.

    Funcion pura: sin I/O, sin reloj de pared, sin estado de modulo. La misma
    entrada devuelve el mismo veredicto siempre, en el mismo orden -- que es
    lo que hace posible el diff de replay del protocolo (seccion 6).

    No recibe el pago a proposito (ver el docstring del modulo).

    Devuelve un `SafetyVerdict` con TODAS las violaciones, ordenadas por
    `CONSTRAINT_PRECEDENCE`. `verdict.binding` es la que va a
    `binding_constraint`; `verdict.violations` es lo que se guarda en el
    journal para responder "¿y ademas chocaba con X?" sin re-correr nada.
    """
    estimate = (estimator or _DEFAULT_ESTIMATOR).estimate(order, state)
    sim_time = order.sim_time

    found = (
        _check_mandatory_break(state, sim_time),
        _check_heat_rule(state, sim_time),
        _check_flagged_zone_night(order, sim_time, estimate),
        _check_vehicle_capacity(order, state),
        _check_shift_end(state, sim_time, estimate),
    )

    violations = tuple(
        sorted((v for v in found if v is not None), key=lambda v: _PRECEDENCE_RANK[v.constraint])
    )
    return SafetyVerdict(violations=violations, estimate=estimate)


def combine(
    verdict: SafetyVerdict,
    *,
    economic_accept: bool,
    economic_reason: str,
    economic_binding: BindingConstraint | None = None,
) -> tuple[Decision, str, BindingConstraint | None]:
    """Une el veredicto de seguridad con el economico. Seguridad manda.

    Existe para que la invariante *Safety-over-pay* no dependa de que el orden
    de los `if` sobreviva a las ultimas horas de un hackathon. Si
    `verdict.blocked`, los tres argumentos economicos se **ignoran por
    completo**: no hay camino de codigo que produzca ACCEPT con una constraint
    violada, por mucho que mejore la paga.

    Devuelve `(decision, reason, binding_constraint)` listos para el body del
    response. Persona 3 llama esto; ver docs/Bloque 3/CONTRATO_PERSONA3.md.
    """
    binding = verdict.binding
    if binding is not None:
        return "SKIP", binding.reason, binding.constraint

    reason = reasons.cap_words(economic_reason) if economic_reason.strip() else "sin motivo registrado"
    if economic_accept:
        return "ACCEPT", reason, economic_binding
    return "SKIP", reason, economic_binding or "reservation_wage"


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
