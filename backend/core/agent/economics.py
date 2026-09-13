"""Economia de la decision: tasa efectiva vs. salario de reserva.

Analogo a MIN_PAY_PER_KM/DEMAND_DISCOUNT en core.agent.decision (el motor
VRPTW de coordenadas), pero en MXN/hr y sobre el contrato oficial de zonas +
vehiculo. Placeholder a calibrar con corridas reales, igual que las demas
constantes de umbral del equipo (ver docs/Bloque 3/01_Plan.md).

Antes NO habia ajuste por demanda ("Bloque 1 no expone esa senal sobre zonas
enteras") -- ya no es cierto: `core.models.ZoneMap` trae `demand_score` por
zona desde el principio, el ajuste solo faltaba conectarse aqui.
"""

from dataclasses import dataclass

BASE_RESERVATION_WAGE_MXN_HR = 150.0

# Relajacion maxima del salario de reserva en la zona de demanda mas caliente
# (demand_score=1.0): reservation_wage_mxn_hr efectivo =
# BASE_RESERVATION_WAGE_MXN_HR * (1 - demand_score * DEMAND_DISCOUNT).
# Mismo criterio y mismo valor que DEMAND_DISCOUNT en core.agent.decision.
DEMAND_DISCOUNT = 0.3

# Demanda neutral para una zona que no existe en nuestro ZoneMap (un juez
# puede mandar cualquier entero de zona -- "you build your own data" no
# significa que conozcamos todas las suyas). Ni castiga ni premia.
NEUTRAL_DEMAND_SCORE = 0.5


@dataclass
class EconomicsResult:
    net_pay_mxn: float
    total_time_min: float
    raw_rate_mxn_hr: float
    adjusted_rate_mxn_hr: float
    reservation_wage_mxn_hr: float
    deadhead_km: float


def evaluate_economics(
    *,
    base_pay_mxn: float,
    est_tip_mxn: float,
    surge_multiplier: float,
    total_time_min: float,
    deadhead_km: float,
    demand_score: float = NEUTRAL_DEMAND_SCORE,
) -> EconomicsResult:
    net_pay_mxn = (base_pay_mxn + est_tip_mxn) * surge_multiplier
    raw_rate_mxn_hr = (net_pay_mxn / total_time_min) * 60.0 if total_time_min > 0 else float("inf")
    adjusted_rate_mxn_hr = raw_rate_mxn_hr
    reservation_wage_mxn_hr = BASE_RESERVATION_WAGE_MXN_HR * (1 - demand_score * DEMAND_DISCOUNT)
    return EconomicsResult(
        net_pay_mxn=net_pay_mxn,
        total_time_min=total_time_min,
        raw_rate_mxn_hr=raw_rate_mxn_hr,
        adjusted_rate_mxn_hr=adjusted_rate_mxn_hr,
        reservation_wage_mxn_hr=reservation_wage_mxn_hr,
        deadhead_km=deadhead_km,
    )
