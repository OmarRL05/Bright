"""Safety gate: las 5 constraints de seguridad del contrato oficial.

Ver student-materials/courier/evaluation_protocol.md seccion 4. Deben vivir
en codigo como constantes nombradas -- los jueces piden abrir este archivo y
ver el limite exacto.

TODO(Omar, P0.3/P1.5 en el roadmap de la auditoria del 12 sep): dueno de este
modulo por el reparto de tareas del equipo. Esta es una primera version
funcional (constantes + 5 checks puros) para no bloquear POST /decide (P0.4,
Adriana) mientras se coordina el reparto real.

VEHICLE_PROFILES viene de core.models (P0.5, Abraham) -- unica fuente de
verdad de perfiles de vehiculo, compartida con el motor VRPTW interno. Este
modulo ya no define su propio placeholder (ver merge de
abraham/bloques-1-2 -- antes eran dos VehicleProfile distintos y sin
weight/volume, se consolido en uno solo con los 5 campos).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from core.models import DEFAULT_ZONE_MAP, VEHICLE_PROFILES, VehicleType

Vehicle = str  # "moto" | "car" | "bike", tal como llega del request HTTP

# --- Constraint 1: zona marcada de noche ---------------------------------
NIGHT_CURFEW_HOUR = 22  # dropoff en zona marcada a esta hora o despues -> refuse

# Zona marcada: Centro (zone_id=2 en DEFAULT_ZONE_MAP, ver core.models) -- la
# de mayor demand_score/densidad urbana, elegida como placeholder de riesgo
# nocturno. Ajustar cuando el equipo defina un criterio real de zonas de riesgo.
FLAGGED_ZONES: frozenset[int] = frozenset({DEFAULT_ZONE_MAP.zone_by_name("Centro").zone_id})

# --- Constraint 2: break obligatorio --------------------------------------
MANDATORY_BREAK_AFTER_MIN = 240.0  # 4 horas continuas
MANDATORY_BREAK_DURATION_MIN = 20.0

# --- Constraint 3: regla de calor ------------------------------------------
HEAT_RULE_START_HOUR = 12
HEAT_RULE_END_HOUR = 16
HEAT_RULE_MAX_CONTINUOUS_MIN = 90.0


@dataclass
class SafetyViolation:
    constraint: str
    reason: str


def check_vehicle_capacity(
    vehicle: Vehicle, weight_kg: float | None, volume_liters: float | None
) -> SafetyViolation | None:
    profile = VEHICLE_PROFILES[VehicleType(vehicle)]
    if weight_kg is not None and weight_kg > profile.weight_limit_kg:
        return SafetyViolation(
            "vehicle_capacity",
            f"{weight_kg:.1f}kg excede el limite de {profile.weight_limit_kg:.0f}kg para {vehicle}",
        )
    if volume_liters is not None and volume_liters > profile.volume_limit_liters:
        return SafetyViolation(
            "vehicle_capacity",
            f"{volume_liters:.1f}L excede el limite de {profile.volume_limit_liters:.0f}L para {vehicle}",
        )
    return None


def check_mandatory_break(continuous_riding_min: float) -> SafetyViolation | None:
    if continuous_riding_min >= MANDATORY_BREAK_AFTER_MIN:
        return SafetyViolation(
            "mandatory_break",
            f"{continuous_riding_min:.0f} min continuos, break de "
            f"{MANDATORY_BREAK_DURATION_MIN:.0f} min obligatorio antes de aceptar otra orden",
        )
    return None


def check_heat_rule(
    sim_time: datetime, continuous_riding_min: float, order_total_time_min: float
) -> SafetyViolation | None:
    if HEAT_RULE_START_HOUR <= sim_time.hour < HEAT_RULE_END_HOUR:
        projected = continuous_riding_min + order_total_time_min
        if projected > HEAT_RULE_MAX_CONTINUOUS_MIN:
            return SafetyViolation(
                "heat_rule",
                f"regla de calor 12:00-16:00: {projected:.0f} min continuos proyectados "
                f"superan el limite de {HEAT_RULE_MAX_CONTINUOUS_MIN:.0f}",
            )
    return None


def check_flagged_zone_night(zone_dropoff: int, sim_time: datetime) -> SafetyViolation | None:
    if zone_dropoff in FLAGGED_ZONES and sim_time.hour >= NIGHT_CURFEW_HOUR:
        return SafetyViolation(
            "flagged_zone_night",
            f"zona {zone_dropoff} marcada, dropoff a las {sim_time:%H:%M} "
            f"(>= {NIGHT_CURFEW_HOUR}:00)",
        )
    return None


def check_shift_end_infeasible(
    sim_time: datetime, order_total_time_min: float, shift_end_time: datetime | None
) -> SafetyViolation | None:
    if shift_end_time is None:
        return None
    projected_completion = sim_time + timedelta(minutes=order_total_time_min)
    if projected_completion > shift_end_time:
        return SafetyViolation(
            "shift_end_infeasible",
            f"completaria a las {projected_completion:%H:%M}, turno termina a las {shift_end_time:%H:%M}",
        )
    return None


def evaluate_safety(
    *,
    vehicle: Vehicle,
    weight_kg: float | None,
    volume_liters: float | None,
    sim_time: datetime,
    zone_dropoff: int,
    continuous_riding_min: float,
    order_total_time_min: float,
    shift_end_time: datetime | None,
) -> SafetyViolation | None:
    """Corre las 5 constraints en orden fijo y devuelve la primera que se viole.

    Orden: capacidad del vehiculo (fisico, no depende del reloj) -> break
    obligatorio -> regla de calor -> zona marcada nocturna -> factibilidad de
    fin de turno. La economia (reservation_wage) se evalua aparte, en
    core.agent.economics, y solo si ninguna de estas 5 se dispara --
    evaluation_protocol.md exige "safety-over-pay invariance": una constraint
    de seguridad no debe ceder ante mejor pago.
    """
    checks = (
        check_vehicle_capacity(vehicle, weight_kg, volume_liters),
        check_mandatory_break(continuous_riding_min),
        check_heat_rule(sim_time, continuous_riding_min, order_total_time_min),
        check_flagged_zone_night(zone_dropoff, sim_time),
        check_shift_end_infeasible(sim_time, order_total_time_min, shift_end_time),
    )
    for violation in checks:
        if violation is not None:
            return violation
    return None
