"""Economia de la decision: tasa efectiva vs. salario de reserva.

Es la segunda mitad del fast path. La primera es `core.agent.safety`, y el
orden entre las dos no es negociable: la seguridad decide antes y, si bloquea,
esta capa no se consulta (ver `safety.combine`). Analogo a `MIN_PAY_PER_KM` en
`core.agent.decision` (el motor VRPTW de coordenadas), pero en MXN/hr y sobre
el contrato oficial de zonas + vehiculo.

Tres cosas que esta capa hace y que conviene poder defender
------------------------------------------------------------

**1. El pago neto es neto de verdad.** `net = base*surge + propina - combustible`.
El combustible sale de `cost_per_km` del perfil de vehiculo, sobre el
kilometraje **completo** (traslado en vacio + entrega). Sin esto el deadhead
sale gratis, y entonces "¿te conviene cruzar la ciudad por este pedido?" no
tiene respuesta numerica -- que es justo lo que sondea la categoria "Dropoff
location value". Tambien es lo que hace que `deadhead_pct_of_km` del CSV de
resultados signifique algo.

**2. El surge multiplica la tarifa, no la propina.** `base*surge + tip`, no
`(base + tip)*surge`. Las plataformas aplican el multiplicador a lo que pagan
ellas; lo que deja el cliente no sube porque haya surge. Inflar la propina con
el surge sobreestima sistematicamente las horas pico, que son justo las que
mas pesan en el resultado.

**3. Donde te deja el pedido vale dinero.** La tasa ajustada modula la tasa
cruda por el `demand_score` de la zona de dropoff: terminar en una zona
caliente vale mas que terminar en una fria, porque el siguiente pedido llega
antes. Es la respuesta directa a la categoria de sondeo "Decisions that must
account for where the courier ends up next, not just current-order pay".

El salario de reserva es un parametro vivo
-------------------------------------------
No es una constante: lo publica la capa de estrategia (tier2), y esta capa lo
lee con `STRATEGY.snapshot()` -- una lectura de atributo, sin lock y sin red.
Cuando el modelo se cae, el snapshot sigue devolviendo el ultimo valor bueno y
el fast path ni se entera. Ver `core/agent/strategy.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.agent import strategy as strategy_layer
from core.models import DEFAULT_ZONE_MAP, VehicleProfile

#: Valor de arranque del salario de reserva, antes de que tier2 opine nunca.
#: Se define en strategy.py para que no existan dos constantes con el mismo
#: significado pudiendo desincronizarse.
RESERVATION_WAGE_MXN_HR = strategy_layer.DEFAULT_RESERVATION_WAGE_MXN_HR

#: Cuanto pesa la zona de dropoff en la tasa ajustada. Con 0.6, terminar en
#: Centro (demand_score 0.9) vale +24% y terminar en Apodaca (0.3) vale -12%.
#:
#: Calibrado sobre TUNING_SEEDS con `scripts/calibrate.py` (ver
#: docs/Bloque 3/RESULTADOS.md). Con 0.6 es a la vez el pico de la rejilla y
#: el punto cuyo PEOR vecino es mas alto, asi que no hay que elegir entre las
#: dos cosas. Sobrevivio sin cambios a la ampliacion del ZoneMap a 16 zonas,
#: que si movio todos los demas umbrales.
DROPOFF_DEMAND_WEIGHT = 0.6

#: demand_score que se considera "ni caliente ni fria" (sin bonus ni castigo).
NEUTRAL_DEMAND_SCORE = 0.5


@dataclass
class EconomicsResult:
    net_pay_mxn: float
    total_time_min: float
    raw_rate_mxn_hr: float
    adjusted_rate_mxn_hr: float
    reservation_wage_mxn_hr: float
    deadhead_km: float
    # --- desglose, para cuando un juez pide la aritmetica ---
    gross_pay_mxn: float = 0.0
    fuel_cost_mxn: float = 0.0
    total_km: float = 0.0
    dropoff_demand_score: float = NEUTRAL_DEMAND_SCORE


def dropoff_demand_score(zone_dropoff: int | None) -> float:
    """`demand_score` de la zona de dropoff segun el ZoneMap.

    Una zona que el juez mande y que nuestro mapa no conozca se trata como
    neutral: no podemos afirmar que sea caliente ni fria, asi que no se premia
    ni se castiga.
    """
    if zone_dropoff is None:
        return NEUTRAL_DEMAND_SCORE
    try:
        return DEFAULT_ZONE_MAP.by_id(int(zone_dropoff)).demand_score
    except (KeyError, TypeError, ValueError):
        return NEUTRAL_DEMAND_SCORE


def evaluate_economics(
    *,
    base_pay_mxn: float,
    est_tip_mxn: float,
    surge_multiplier: float,
    total_time_min: float,
    deadhead_km: float,
    delivery_km: float = 0.0,
    profile: VehicleProfile | None = None,
    zone_dropoff: int | None = None,
    reservation_wage_mxn_hr: float | None = None,
) -> EconomicsResult:
    """Aritmetica de la oferta. Pura: sin I/O y sin reloj.

    `profile` y `delivery_km` son opcionales para no romper a quien llame sin
    ellos; sin `profile` no se puede cobrar combustible y el neto queda igual
    al bruto, cosa que el desglose deja ver (`fuel_cost_mxn = 0`).

    `reservation_wage_mxn_hr` se toma de la capa de estrategia si no se pasa
    explicito -- pasarlo sirve para replay y para los baselines, que deben
    correr contra un umbral fijo y no contra lo que tier2 haya publicado.
    """
    gross_pay_mxn = base_pay_mxn * surge_multiplier + est_tip_mxn

    total_km = max(0.0, deadhead_km) + max(0.0, delivery_km)
    fuel_cost_mxn = total_km * profile.cost_per_km if profile is not None else 0.0

    net_pay_mxn = gross_pay_mxn - fuel_cost_mxn

    raw_rate_mxn_hr = (
        (net_pay_mxn / total_time_min) * 60.0 if total_time_min > 0 else float("inf")
    )

    demand = dropoff_demand_score(zone_dropoff)
    adjusted_rate_mxn_hr = raw_rate_mxn_hr * (
        1.0 + DROPOFF_DEMAND_WEIGHT * (demand - NEUTRAL_DEMAND_SCORE)
    )

    if reservation_wage_mxn_hr is None:
        # Lectura de atributo: sin lock, sin red, sin posibilidad de fallar.
        reservation_wage_mxn_hr = strategy_layer.STRATEGY.snapshot().reservation_wage_mxn_hr

    return EconomicsResult(
        net_pay_mxn=net_pay_mxn,
        total_time_min=total_time_min,
        raw_rate_mxn_hr=raw_rate_mxn_hr,
        adjusted_rate_mxn_hr=adjusted_rate_mxn_hr,
        reservation_wage_mxn_hr=reservation_wage_mxn_hr,
        deadhead_km=deadhead_km,
        gross_pay_mxn=gross_pay_mxn,
        fuel_cost_mxn=fuel_cost_mxn,
        total_km=total_km,
        dropoff_demand_score=demand,
    )
