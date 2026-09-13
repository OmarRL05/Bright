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
| 1 | No dropoff en zona marcada tras 22:00  | `flagged_zone_night`   | `FLAGGED_ZONES`, `NIGHT_CURFEW_HOUR` |
| 2 | Pausa obligatoria tras 4 h continuas   | `mandatory_break`      | `MANDATORY_BREAK_AFTER_MIN`, `MANDATORY_BREAK_DURATION_MIN` |
| 3 | Calor: 90 min continuos de 12 a 16     | `heat_rule`            | `HEAT_RULE_*` |
| 4 | Rechazar lo que no cierra antes del fin | `shift_end_infeasible` | `SHIFT_END_SAFETY_MARGIN_MIN` |
| 5 | Peso y volumen por vehiculo            | `vehicle_capacity`     | `VEHICLE_PROFILES` (core/models.py) |

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
microsegundos y compra la respuesta a "¿y ademas chocaba con X?":
`evaluate_safety_full` devuelve `violations` completo, no solo la que se
reporta.

Retrospectivo vs. prospectivo: por que el calor y la pausa no se tratan igual
-----------------------------------------------------------------------------
Es la pregunta mas fina que un juez puede hacer de este archivo, asi que la
respuesta esta escrita:

- **Calor es un TOPE** ("continuous riding **capped at** 90 minutes during
  12:00-16:00"). Un tope se evalua hacia adelante: la pregunta es si *aceptar
  esto* te pasa del tope. Por eso se proyecta `continuo + duracion del pedido`.
- **La pausa es un DISPARADOR** ("mandatory 20-minute break **after** 4
  continuous hours"). Se evalua hacia atras: ya llevas 4 horas, o no.

Proyectar tambien la pausa rechazaria todo pedido que cruce la marca de las 4
horas, que es mas estricto de lo que el protocolo pide. Evaluar el calor hacia
atras dejaria pasar un pedido de 95 minutos arrancando desde cero, que es
justo lo que el tope existe para impedir.

Precedencia
-----------
Cuando varias violan a la vez hay que reportar UNA en `binding_constraint`. El
orden no es arbitrario: se reporta **la mas dificil de relajar por el pedido**.

    mandatory_break > heat_rule > flagged_zone_night > vehicle_capacity > shift_end_infeasible

Las dos primeras dependen solo del repartidor — ningun pedido distinto las
arregla, hay que parar, y decirlo primero es lo mas util que se le puede
responder. `flagged_zone_night` la evita otro destino; `vehicle_capacity`, un
pedido mas chico; `shift_end_infeasible` es la mas blanda porque depende de una
estimacion y la evita un pedido mas corto.

Datos faltantes
---------------
El PROBE de `validate_format.py` no manda estado ni horas, y `weight_kg` /
`volume_liters` son opcionales en el schema. Regla general: una constraint que
no tiene los datos para evaluarse **no dispara**. Es la direccion segura para
el formato (el endpoint responde 200 con una decision valida) y la honesta para
el juicio (no inventamos una violacion que no podemos sostener con numeros).
Cada caso esta marcado abajo con `# sin datos`.

Historia de este archivo
------------------------
Integra dos versiones: la de Adriana (P0.4, para desbloquear POST /decide) y la
de Omar (P0.3/P1.5, dueño del modulo por reparto). De la primera vienen los
perfiles desde `core.models`, las zonas marcadas derivadas del `ZoneMap`, los
nombres de las constantes y el calor proyectado; de la segunda, la evaluacion
contra la hora de llegada, la capacidad acumulada, el margen de fin de turno,
la reconciliacion contador/pausa, el `SafetyVerdict` y los `detail`. Ver
docs/Bloque 3/RESPUESTA_A_PERSONA3.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from core.agent import reasons
from core.models import DEFAULT_ZONE_MAP, VEHICLE_PROFILES, VehicleProfile, VehicleType

Vehicle = str  # "moto" | "car" | "bike", tal como llega del request HTTP

#: Ids que acepta validate_format.py. No inventar valores nuevos.
BindingConstraint = Literal[
    "flagged_zone_night",
    "mandatory_break",
    "heat_rule",
    "shift_end_infeasible",
    "vehicle_capacity",
    "reservation_wage",
]

Decision = Literal["ACCEPT", "SKIP"]


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
#: Zonas consideradas inseguras de noche. Se derivan del ZoneMap en vez de
#: escribirse como enteros sueltos: asi no pueden desincronizarse del catalogo
#: real de zonas que usa el simulador (Bloque 1). Centro es la de mayor
#: demand_score/densidad urbana -- criterio placeholder del equipo.
FLAGGED_ZONE_NAMES: tuple[str, ...] = ("Centro",)


def _resolve_flagged_zones() -> frozenset[int]:
    """Ids de las zonas marcadas segun el ZoneMap por defecto.

    Tolerante a que el ZoneMap cambie de nombres: una zona que ya no existe se
    omite en vez de tumbar el import del modulo de seguridad.
    """
    ids = set()
    for name in FLAGGED_ZONE_NAMES:
        try:
            ids.add(DEFAULT_ZONE_MAP.zone_by_name(name).zone_id)
        except KeyError:
            continue
    return frozenset(ids)


FLAGGED_ZONES: frozenset[int] = _resolve_flagged_zones()

#: El toque de queda empieza a esta hora. "No dropoff in flagged zones after
#: 22:00" (protocolo, constraint 1).
NIGHT_CURFEW_HOUR = 22

#: ...y termina a esta. El protocolo solo nombra el inicio; el cierre a las
#: 06:00 es extension nuestra, porque una entrega a la 01:00 en zona marcada
#: no es mas segura que a las 22:30. Un turno que no cruza medianoche nunca
#: toca esta constante.
NIGHT_CURFEW_END_HOUR = 6

# --- Constraint 2: pausa obligatoria --------------------------------------
#: 4 horas continuas al volante. Pasado esto, toca parar.
MANDATORY_BREAK_AFTER_MIN = 240.0

#: Duracion de la pausa obligatoria.
MANDATORY_BREAK_DURATION_MIN = 20.0

# --- Constraint 3: regla de calor -----------------------------------------
#: Franja de calor [inicio, fin) en hora local del turno.
HEAT_RULE_START_HOUR = 12
HEAT_RULE_END_HOUR = 16

#: Dentro de la franja el tope de manejo continuo baja de 240 a esto, y se
#: evalua PROYECTADO (ver el docstring del modulo).
HEAT_RULE_MAX_CONTINUOUS_MIN = 90.0

# --- Constraint 4: fin de turno -------------------------------------------
#: Colchon antes del cierre del turno. La entrega debe terminar al menos estos
#: minutos antes de `shift_end_time`: una estimacion que cae exactamente en la
#: hora de cierre no es "a tiempo", es un volado.
SHIFT_END_SAFETY_MARGIN_MIN = 5.0

# --- Constraint 5: capacidad ----------------------------------------------
#: Los limites de peso y volumen viven en VEHICLE_PROFILES (core/models.py),
#: junto con la velocidad y el costo por km, porque los cinco son el mismo
#: perfil de vehiculo y separarlos invitaba a que se desincronizaran.

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

    constraint: str
    #: Ya formateado y garantizado <40 palabras por core.agent.reasons.
    reason: str
    #: Numeros crudos del chequeo, para `explain_decision` y el dashboard.
    #: Nunca se usa para decidir; es evidencia, no entrada.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyVerdict:
    """Resultado completo del gate. `violations` viene ordenado por precedencia."""

    violations: tuple[SafetyViolation, ...] = ()
    #: Minutos desde `sim_time` hasta cerrar la entrega, incluyendo la cola del
    #: trabajo en vuelo. Se expone porque `explain_decision` pide
    #: `time_to_completion`: el numero explicado tiene que ser EL que se uso
    #: para decidir, no uno parecido recalculado despues.
    minutes_to_completion: float = 0.0

    @property
    def blocked(self) -> bool:
        return bool(self.violations)

    @property
    def binding(self) -> SafetyViolation | None:
        """La violacion que se reporta en `binding_constraint`."""
        return self.violations[0] if self.violations else None

    @property
    def constraints(self) -> tuple[str, ...]:
        return tuple(v.constraint for v in self.violations)


def profile_for(vehicle: Vehicle) -> VehicleProfile:
    """Perfil del vehiculo, cayendo al mas restrictivo si es desconocido.

    Nunca lanza: un vehiculo no reconocido en plena ventana de decision no
    puede tumbar el endpoint (seria fallo duro de Feasibility), pero tampoco
    puede relajar limites -- por eso el fallback es `bike`.
    """
    try:
        return VEHICLE_PROFILES[VehicleType(vehicle)]
    except (ValueError, KeyError):
        return VEHICLE_PROFILES[VehicleType.BIKE]


# ==========================================================================
# Utilidades de tiempo
# ==========================================================================


def effective_continuous_riding_min(
    continuous_riding_min: float,
    last_break_end_time: datetime | None,
    sim_time: datetime | None,
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
    counter = max(0.0, continuous_riding_min)

    if last_break_end_time is None or sim_time is None:
        return counter

    since_break = (sim_time - last_break_end_time).total_seconds() / 60.0
    if since_break < 0:
        return counter  # pausa en el futuro: dato incoherente, se ignora
    return min(counter, since_break)


def _in_curfew(moment: datetime) -> bool:
    """¿`moment` cae dentro del toque de queda nocturno?

    La ventana cruza medianoche (22:00 -> 06:00), por eso es un OR y no un
    rango: `hour >= 22 or hour < 6`.
    """
    return moment.hour >= NIGHT_CURFEW_HOUR or moment.hour < NIGHT_CURFEW_END_HOUR


def _in_heat_window(moment: datetime) -> bool:
    """Franja de calor [12:00, 16:00). Cerrada abajo, abierta arriba: a las
    16:00 en punto la regla ya no aplica."""
    return HEAT_RULE_START_HOUR <= moment.hour < HEAT_RULE_END_HOUR


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


# ==========================================================================
# Las cinco constraints. Una funcion cada una, cada una devuelve
# SafetyViolation o None. Ninguna recibe el pago.
# ==========================================================================


def check_mandatory_break(
    continuous_riding_min: float,
    *,
    last_break_end_time: datetime | None = None,
    sim_time: datetime | None = None,
) -> SafetyViolation | None:
    """Constraint 2: pausa obligatoria de 20 min tras 4 h continuas.

    Disparador retrospectivo (ver el docstring del modulo). Dispara con `>=`:
    240 minutos EXACTOS ya obligan a parar. El limite es un tope, no una meta.
    """
    riding = effective_continuous_riding_min(continuous_riding_min, last_break_end_time, sim_time)
    if riding < MANDATORY_BREAK_AFTER_MIN:
        return None
    return SafetyViolation(
        constraint="mandatory_break",
        reason=reasons.mandatory_break(
            riding, MANDATORY_BREAK_AFTER_MIN, MANDATORY_BREAK_DURATION_MIN
        ),
        detail={
            "continuous_riding_min": riding,
            "limit_min": MANDATORY_BREAK_AFTER_MIN,
            "required_break_min": MANDATORY_BREAK_DURATION_MIN,
            "raw_counter_min": continuous_riding_min,
            "last_break_end_time": _iso(last_break_end_time),
        },
    )


def check_heat_rule(
    sim_time: datetime | None,
    continuous_riding_min: float,
    order_total_time_min: float,
    *,
    last_break_end_time: datetime | None = None,
) -> SafetyViolation | None:
    """Constraint 3: de 12:00 a 16:00 el tope continuo baja a 90 min.

    Tope **prospectivo**: se proyecta `continuo + duracion del pedido` y se
    compara contra el tope. Evaluarlo solo sobre el acumulado actual dejaria
    pasar un pedido de 95 minutos arrancando desde cero, que es exactamente lo
    que el tope existe para impedir.
    """
    if sim_time is None:
        return None  # sin datos: sin hora no se sabe si es la franja
    if not _in_heat_window(sim_time):
        return None

    riding = effective_continuous_riding_min(continuous_riding_min, last_break_end_time, sim_time)
    projected = riding + max(0.0, order_total_time_min)
    if projected <= HEAT_RULE_MAX_CONTINUOUS_MIN:
        return None
    return SafetyViolation(
        constraint="heat_rule",
        reason=reasons.heat_rule(
            projected, HEAT_RULE_MAX_CONTINUOUS_MIN, HEAT_RULE_START_HOUR, HEAT_RULE_END_HOUR
        ),
        detail={
            "continuous_riding_min": riding,
            "order_total_time_min": order_total_time_min,
            "projected_continuous_min": projected,
            "limit_min": HEAT_RULE_MAX_CONTINUOUS_MIN,
            "sim_time": _iso(sim_time),
            "window": f"{HEAT_RULE_START_HOUR:02d}:00-{HEAT_RULE_END_HOUR:02d}:00",
        },
    )


def check_flagged_zone_night(
    zone_dropoff: int | None,
    sim_time: datetime | None,
    minutes_to_completion: float = 0.0,
) -> SafetyViolation | None:
    """Constraint 1: nada de dropoffs en zona marcada despues de las 22:00.

    Se evalua contra la **hora estimada de llegada al dropoff**, no contra la
    hora del ping. Es la diferencia entre la regla que queremos y una que se
    burla sola: aceptar a las 21:50 un pedido que entrega a las 22:06 dejaria
    al repartidor exactamente donde la regla dice que no debe estar.
    """
    if zone_dropoff is None or zone_dropoff not in FLAGGED_ZONES:
        return None
    if sim_time is None:
        return None  # sin datos: sin hora no hay toque de queda que evaluar

    arrival = sim_time + timedelta(minutes=max(0.0, minutes_to_completion))
    if not _in_curfew(arrival):
        return None
    return SafetyViolation(
        constraint="flagged_zone_night",
        reason=reasons.flagged_zone_night(zone_dropoff, arrival, NIGHT_CURFEW_HOUR),
        detail={
            "zone_dropoff": zone_dropoff,
            "flagged_zones": sorted(FLAGGED_ZONES),
            "estimated_arrival": _iso(arrival),
            "curfew": f"{NIGHT_CURFEW_HOUR:02d}:00-{NIGHT_CURFEW_END_HOUR:02d}:00",
            "minutes_to_completion": minutes_to_completion,
        },
    )


def check_vehicle_capacity(
    vehicle: Vehicle,
    weight_kg: float | None,
    volume_liters: float | None,
    *,
    in_flight_weight_kg: float = 0.0,
    in_flight_volume_liters: float = 0.0,
) -> SafetyViolation | None:
    """Constraint 5: peso y volumen del perfil del vehiculo.

    **Acumulado sobre la mochila**, no por pedido suelto: lo que importa es lo
    que el repartidor va a estar cargando al mismo tiempo. Un pedido de 2 kg es
    inofensivo solo o imposible si ya lleva 11 en una moto de 12. El pedido
    suelto es el caso particular donde la mochila esta vacia.

    Dispara con `>` estricto: cargar exactamente el limite es legal. El limite
    es el maximo permitido, no el primer valor prohibido.

    `None` en peso o volumen significa "el dato no vino": se cuenta como 0 y se
    registra, en vez de inventar una violacion que no se puede sostener.
    """
    profile = profile_for(vehicle)

    checks = (
        ("peso", in_flight_weight_kg, weight_kg, profile.weight_limit_kg, "kg"),
        ("volumen", in_flight_volume_liters, volume_liters, profile.volume_limit_liters, "L"),
    )

    for dimension, in_flight, incoming, limit, unit in checks:
        incoming_value = 0.0 if incoming is None else float(incoming)
        required = in_flight + incoming_value
        if required <= limit:
            continue
        return SafetyViolation(
            constraint="vehicle_capacity",
            reason=reasons.vehicle_capacity(
                profile.type.value, dimension, required, limit, unit, in_flight, incoming_value
            ),
            detail={
                "vehicle": profile.type.value,
                "dimension": dimension,
                "in_flight": in_flight,
                "incoming": incoming_value,
                "incoming_reported": incoming,
                "required": required,
                "limit": limit,
                "unit": unit,
            },
        )
    return None


def check_shift_end_infeasible(
    sim_time: datetime | None,
    minutes_to_completion: float,
    shift_end_time: datetime | None,
    *,
    queue_offset_min: float = 0.0,
) -> SafetyViolation | None:
    """Constraint 4: rechazar lo que no cierra antes del fin de turno.

    Se compara la **hora de cierre estimada de la entrega** contra
    `shift_end_time` menos el margen -- no el costo marginal del pedido contra
    el tiempo restante. Esa segunda forma es el hallazgo 2 de la auditoria: seis
    pedidos con delta individual aceptable terminan 8 minutos despues del cierre
    porque nadie mira el acumulado. `minutes_to_completion` ya incluye la cola
    del trabajo en vuelo, asi que aqui el acumulado esta contemplado -- que es
    tambien la categoria de sondeo "Stacking and route feasibility".

    `shift_end_time` sale del estado. El schema es explicito: "Read from state,
    never hardcoded - judges check this."
    """
    if sim_time is None or shift_end_time is None:
        return None  # sin datos: sin hora de cierre no hay nada que comparar

    completion = sim_time + timedelta(minutes=max(0.0, minutes_to_completion))
    deadline = shift_end_time - timedelta(minutes=SHIFT_END_SAFETY_MARGIN_MIN)
    if completion <= deadline:
        return None
    return SafetyViolation(
        constraint="shift_end_infeasible",
        reason=reasons.shift_end_infeasible(
            completion, shift_end_time, SHIFT_END_SAFETY_MARGIN_MIN
        ),
        detail={
            "estimated_completion": _iso(completion),
            "shift_end_time": _iso(shift_end_time),
            "safety_margin_min": SHIFT_END_SAFETY_MARGIN_MIN,
            "minutes_to_completion": minutes_to_completion,
            "queue_offset_min": queue_offset_min,
            "overshoot_min": (completion - deadline).total_seconds() / 60.0,
        },
    )


# ==========================================================================
# Entrada publica
# ==========================================================================


def evaluate_safety_full(
    *,
    vehicle: Vehicle,
    weight_kg: float | None,
    volume_liters: float | None,
    sim_time: datetime | None,
    zone_dropoff: int | None,
    continuous_riding_min: float,
    order_total_time_min: float,
    shift_end_time: datetime | None,
    in_flight_weight_kg: float = 0.0,
    in_flight_volume_liters: float = 0.0,
    last_break_end_time: datetime | None = None,
    queue_offset_min: float = 0.0,
) -> SafetyVerdict:
    """Evalua las 5 constraints duras y devuelve el veredicto completo.

    Funcion pura: sin I/O, sin reloj de pared, sin estado de modulo. La misma
    entrada devuelve el mismo veredicto siempre, en el mismo orden -- que es lo
    que hace posible el diff de replay del protocolo (seccion 6).

    No recibe el pago a proposito (ver el docstring del modulo).

    `order_total_time_min` es la duracion de ESTA oferta;
    `queue_offset_min` es lo que falta para terminar lo ya aceptado. La suma es
    cuando cierra de verdad esta entrega, y es contra eso que se evaluan el
    toque de queda y el fin de turno.

    Devuelve TODAS las violaciones, ordenadas por `CONSTRAINT_PRECEDENCE`.
    `verdict.binding` es la que va a `binding_constraint`; `verdict.violations`
    es lo que guarda el journal para responder "¿y ademas chocaba con X?" sin
    re-correr nada.
    """
    minutes_to_completion = max(0.0, queue_offset_min) + max(0.0, order_total_time_min)

    found = (
        check_mandatory_break(
            continuous_riding_min,
            last_break_end_time=last_break_end_time,
            sim_time=sim_time,
        ),
        check_heat_rule(
            sim_time,
            continuous_riding_min,
            order_total_time_min,
            last_break_end_time=last_break_end_time,
        ),
        check_flagged_zone_night(zone_dropoff, sim_time, minutes_to_completion),
        check_vehicle_capacity(
            vehicle,
            weight_kg,
            volume_liters,
            in_flight_weight_kg=in_flight_weight_kg,
            in_flight_volume_liters=in_flight_volume_liters,
        ),
        check_shift_end_infeasible(
            sim_time,
            minutes_to_completion,
            shift_end_time,
            queue_offset_min=queue_offset_min,
        ),
    )

    violations = tuple(
        sorted(
            (v for v in found if v is not None),
            key=lambda v: _PRECEDENCE_RANK.get(v.constraint, len(CONSTRAINT_PRECEDENCE)),
        )
    )
    return SafetyVerdict(violations=violations, minutes_to_completion=minutes_to_completion)


def evaluate_safety(**kwargs: Any) -> SafetyViolation | None:
    """La constraint que manda, o None si ninguna violo.

    Misma firma que `evaluate_safety_full`. Es la forma que consume
    `api/decide.py`; quien quiera todas las violaciones usa la otra.
    """
    return evaluate_safety_full(**kwargs).binding


def combine(
    verdict: SafetyVerdict | SafetyViolation | None,
    *,
    economic_accept: bool,
    economic_reason: str,
    economic_binding: str | None = None,
) -> tuple[Decision, str, str | None]:
    """Une el veredicto de seguridad con el economico. Seguridad manda.

    Existe para que la invariante *Safety-over-pay* no dependa de que el orden
    de los `if` sobreviva a las ultimas horas de un hackathon. Si la seguridad
    bloqueo, los tres argumentos economicos se **ignoran por completo**: no hay
    camino de codigo que produzca ACCEPT con una constraint violada, por mucho
    que mejore la paga.

    Acepta indistintamente un `SafetyVerdict`, una `SafetyViolation` suelta o
    `None`, para que sirva con cualquiera de las dos entradas publicas.

    Devuelve `(decision, reason, binding_constraint)` listos para el body del
    response.
    """
    if isinstance(verdict, SafetyVerdict):
        binding = verdict.binding
    else:
        binding = verdict

    if binding is not None:
        return "SKIP", binding.reason, binding.constraint

    reason = (
        reasons.cap_words(economic_reason)
        if economic_reason and economic_reason.strip()
        else "sin motivo registrado"
    )
    if economic_accept:
        return "ACCEPT", reason, economic_binding
    return "SKIP", reason, economic_binding or "reservation_wage"
