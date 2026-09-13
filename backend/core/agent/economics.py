"""Economia de la decision: tasa efectiva vs. salario de reserva.

Analogo a MIN_PAY_PER_KM en core.agent.decision (el motor VRPTW de
coordenadas), pero en MXN/hr y sobre el contrato oficial de zonas + vehiculo.
Placeholder a calibrar con corridas reales, igual que las demas constantes de
umbral del equipo (ver docs/Bloque 3/01_Plan.md).
"""

from dataclasses import dataclass

RESERVATION_WAGE_MXN_HR = 150.0


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
) -> EconomicsResult:
    net_pay_mxn = (base_pay_mxn + est_tip_mxn) * surge_multiplier
    raw_rate_mxn_hr = (net_pay_mxn / total_time_min) * 60.0 if total_time_min > 0 else float("inf")
    # Sin ajuste por demanda historica todavia -- Bloque 1 no expone esa senal
    # sobre zonas enteras (StaticDemandSignal solo cubre coordenadas).
    adjusted_rate_mxn_hr = raw_rate_mxn_hr
    return EconomicsResult(
        net_pay_mxn=net_pay_mxn,
        total_time_min=total_time_min,
        raw_rate_mxn_hr=raw_rate_mxn_hr,
        adjusted_rate_mxn_hr=adjusted_rate_mxn_hr,
        reservation_wage_mxn_hr=RESERVATION_WAGE_MXN_HR,
        deadhead_km=deadhead_km,
    )
