"""Politicas comparables: los baselines nombrados y nuestro agente.

    "Compare against named baselines. A single number with nothing beside it
     is not a result."
    -- student-materials/courier/README.md

Las filas de `results_table_template.csv` son exactamente estas: AcceptAll,
HighestPay, NearestFirst, GreedyRate, OurAgent, Oracle.

La regla que hace util la comparacion
--------------------------------------
**Los baselines no consultan el gate de seguridad.** No es un descuido: es el
punto. Un baseline es como se comporta alguien que optimiza solo por dinero, y
la columna `safety_violations` existe para mostrar cuanto cuesta eso. Si todas
las politicas pasaran por el gate, la tabla compararia seis variantes del mismo
agente y la columna seria cero en todas las filas.

El arnes cuenta las violaciones **por fuera**, sobre lo que cada politica
acepto (ver `shift.ShiftRunner._count_violations`), asi que ninguna puede
esconderlas por no mirarlas.

`OurAgent` corre el mismo codigo que `POST /decide`
----------------------------------------------------
Literalmente: `evaluate_safety_full` + `evaluate_economics` + `combine`, los
mismos tres que `api/decide.py`. No es una reimplementacion "equivalente" --
si lo fuera, el numero reportado seria el de un sistema que los jueces no van a
probar.

Calibracion
-----------
Los umbrales de los baselines estan calibrados sobre `TUNING_SEEDS`, nunca
sobre `REPORTING_SEEDS` (ver `core/evaluation/seeds.py`). Estan aqui como
constantes nombradas por la misma razon que los limites de seguridad: para
poder abrirlos y moverlos delante de un juez.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from api.schemas import DecideRequest
from core.agent import reasons
from core.agent.economics import evaluate_economics
from core.agent.safety import combine, evaluate_safety_full
from core.agent.strategy import DEFAULT_RESERVATION_WAGE_MXN_HR

# --------------------------------------------------------------------------
# Umbrales de los baselines. Calibrados sobre TUNING_SEEDS.
# --------------------------------------------------------------------------

# CADA baseline esta calibrado sobre TUNING_SEEDS con el mismo procedimiento
# que el agente. No es un detalle: comparar nuestro umbral afinado contra
# umbrales puestos a ojo convertiria la tabla en un espantapajaros, y
# "¿por que deberia confiar en ese numero?" es una de las preguntas que los
# jueces hacen por escrito.

#: HighestPay acepta si el bruto (tarifa*surge + propina) llega a esto.
HIGHEST_PAY_MIN_MXN = 170.0

#: NearestFirst acepta si el traslado en vacio no pasa de esto. El barrido dio
#: el mismo resultado para 1, 2 y 3 km: en este mapa de 4 zonas casi no hay
#: traslados intermedios, asi que el umbral separa "misma zona" de "otra zona".
NEAREST_FIRST_MAX_DEADHEAD_KM = 1.0

#: GreedyRate acepta si la tasa cruda llega a esto. Coincide con el umbral del
#: agente, y no por casualidad: el barrido dio el mismo optimo para los dos.
#: Eso es lo que permite que la comparacion entre las dos filas aisle lo que
#: aportan la seguridad y el valor de la zona, y no una calibracion distinta.
GREEDY_RATE_MIN_MXN_HR = 400.0


@dataclass(frozen=True)
class PolicyDecision:
    """Lo que una politica decide, con su motivo.

    El motivo no es decorativo: sin el, el evento `decision` del event log
    tendria que inventar un `reason`, y ese campo lo valida
    `validate_format.py` y lo lee un juez en voz alta.
    """

    accept: bool
    reason: str
    binding_constraint: str | None = None

    def __bool__(self) -> bool:
        return self.accept


class Policy(Protocol):
    name: str

    def decide(
        self,
        request: DecideRequest,
        runner,
        state,
        sim_time: datetime,
    ) -> PolicyDecision:
        """¿Acepta esta oferta, y por que?"""
        ...


# ==========================================================================
# Baselines. Ninguno consulta el gate de seguridad.
# ==========================================================================


class AcceptAll:
    """Acepta todo. El piso absoluto: cuanto dinero deja 'no decidir'."""

    name = "AcceptAll"

    def decide(self, request, runner, state, sim_time) -> PolicyDecision:
        return PolicyDecision(True, "acepta toda oferta, sin evaluar nada")


class HighestPay:
    """Solo pedidos con buena tarifa, sin mirar cuanto cuestan.

    Es el error clasico del repartidor novato: perseguir el numero grande sin
    descontar el traslado. La comparacion contra `GreedyRate` aisla justo eso.
    """

    name = "HighestPay"

    def __init__(self, min_pay_mxn: float = HIGHEST_PAY_MIN_MXN) -> None:
        self.min_pay_mxn = min_pay_mxn

    def decide(self, request, runner, state, sim_time) -> PolicyDecision:
        gross = request.base_pay_mxn * request.surge_multiplier + request.est_tip_mxn
        ok = gross >= self.min_pay_mxn
        return PolicyDecision(
            ok, f"bruto ${gross:.0f} {'>=' if ok else '<'} minimo ${self.min_pay_mxn:.0f}"
        )


class NearestFirst:
    """Solo pedidos cerca, sin mirar cuanto pagan.

    El error simetrico al anterior: minimizar el traslado en vacio es
    razonable, pero por si solo deja pasar los pedidos que valen la pena.
    """

    name = "NearestFirst"

    def __init__(self, max_deadhead_km: float = NEAREST_FIRST_MAX_DEADHEAD_KM) -> None:
        self.max_deadhead_km = max_deadhead_km

    def decide(self, request, runner, state, sim_time) -> PolicyDecision:
        ok = request.distance_pickup_km <= self.max_deadhead_km
        return PolicyDecision(
            ok,
            f"traslado en vacio {request.distance_pickup_km:.1f} km "
            f"{'<=' if ok else '>'} maximo {self.max_deadhead_km:.1f} km",
        )


class GreedyRate:
    """Acepta si la tasa cruda llega al umbral. Sin seguridad, sin zona.

    Es el baseline exigente: ya hace la division correcta (dinero neto entre
    tiempo total). Lo que le falta es exactamente lo que aporta nuestro agente,
    asi que es la fila contra la que se mide de verdad.
    """

    name = "GreedyRate"

    def __init__(self, min_rate_mxn_hr: float = GREEDY_RATE_MIN_MXN_HR) -> None:
        self.min_rate_mxn_hr = min_rate_mxn_hr

    def decide(self, request, runner, state, sim_time) -> bool:
        total_min, _, _ = runner.order_timing(request, state, sim_time)
        economics = evaluate_economics(
            base_pay_mxn=request.base_pay_mxn,
            est_tip_mxn=request.est_tip_mxn,
            surge_multiplier=request.surge_multiplier,
            total_time_min=total_min,
            deadhead_km=request.distance_pickup_km,
            delivery_km=request.distance_delivery_km,
            profile=runner.config.profile,
            zone_dropoff=None,  # sin valor de zona: eso es lo que le falta
            reservation_wage_mxn_hr=self.min_rate_mxn_hr,
        )
        ok = economics.raw_rate_mxn_hr >= self.min_rate_mxn_hr
        return PolicyDecision(
            ok,
            f"tasa cruda ${economics.raw_rate_mxn_hr:.0f}/hr "
            f"{'>=' if ok else '<'} minimo ${self.min_rate_mxn_hr:.0f}/hr",
            None if ok else "reservation_wage",
        )


# ==========================================================================
# Nuestro agente
# ==========================================================================


class OurAgent:
    """El sistema real: gate de seguridad + economia, en ese orden.

    Mismo codigo que `POST /decide`. `reservation_wage_mxn_hr` se fija
    explicito en vez de leerlo de `STRATEGY`, por dos razones: el arnes no
    tiene capa tier2 corriendo, y una corrida de resultados tiene que ser
    reproducible -- si el umbral dependiera de lo que un modelo publico a
    media corrida, dos ejecuciones del mismo seed podrian no coincidir.
    """

    name = "OurAgent"

    def __init__(self, reservation_wage_mxn_hr: float | None = None) -> None:
        self.reservation_wage_mxn_hr = (
            reservation_wage_mxn_hr
            if reservation_wage_mxn_hr is not None
            else DEFAULT_RESERVATION_WAGE_MXN_HR
        )

    def decide(self, request, runner, state, sim_time) -> bool:
        total_min, _, _ = runner.order_timing(request, state, sim_time)

        verdict = evaluate_safety_full(
            vehicle=request.vehicle,
            weight_kg=request.weight_kg,
            volume_liters=request.volume_liters,
            sim_time=sim_time,
            zone_dropoff=request.zone_dropoff,
            continuous_riding_min=state.continuous_riding_min,
            order_total_time_min=total_min,
            shift_end_time=runner.config.shift_end_time,
            in_flight_weight_kg=sum(o["weight_kg"] for o in state.in_flight),
            in_flight_volume_liters=sum(o["volume_liters"] for o in state.in_flight),
            last_break_end_time=state.last_break_end_time,
            queue_offset_min=runner.queue_offset_min(state, sim_time),
        )

        economics = evaluate_economics(
            base_pay_mxn=request.base_pay_mxn,
            est_tip_mxn=request.est_tip_mxn,
            surge_multiplier=request.surge_multiplier,
            total_time_min=total_min,
            deadhead_km=request.distance_pickup_km,
            delivery_km=request.distance_delivery_km,
            profile=runner.config.profile,
            zone_dropoff=request.zone_dropoff,
            reservation_wage_mxn_hr=self.reservation_wage_mxn_hr,
        )

        economic_accept = economics.adjusted_rate_mxn_hr >= economics.reservation_wage_mxn_hr
        decision, reason, binding = combine(
            verdict,
            economic_accept=economic_accept,
            economic_reason=(
                reasons.accepted(economics.adjusted_rate_mxn_hr, economics.reservation_wage_mxn_hr)
                if economic_accept
                else reasons.reservation_wage(
                    economics.adjusted_rate_mxn_hr,
                    economics.reservation_wage_mxn_hr,
                    economics.deadhead_km,
                )
            ),
            economic_binding=None if economic_accept else "reservation_wage",
        )
        return PolicyDecision(decision == "ACCEPT", reason, binding)


class GreedyRateSafe(OurAgent):
    """Fila de diagnostico: GreedyRate + gate de seguridad, sin valor de zona.

    No es un baseline del template: es el eslabon que hace legible la tabla.
    Entre `GreedyRate` y esta fila la unica diferencia es la seguridad, y entre
    esta y `OurAgent` la unica diferencia es el valor de la zona de dropoff. Con
    las tres juntas, "¿por que ganan menos que el baseline?" se contesta con
    dos restas en vez de con una explicacion.
    """

    name = "GreedyRateSafe"

    def decide(self, request, runner, state, sim_time) -> PolicyDecision:
        import core.agent.economics as eco

        anterior = eco.DROPOFF_DEMAND_WEIGHT
        eco.DROPOFF_DEMAND_WEIGHT = 0.0
        try:
            return super().decide(request, runner, state, sim_time)
        finally:
            eco.DROPOFF_DEMAND_WEIGHT = anterior


# ==========================================================================
# Cota superior
# ==========================================================================


class Oracle:
    """Cota superior: nuestra politica con el umbral afinado en retrospectiva.

    **Que es exactamente, para no venderlo como lo que no es.** Seleccionar el
    mejor subconjunto de pedidos con ventanas de tiempo es NP-duro y no lo
    resolvemos. Lo que hace el Oracle es correr el turno completo una vez por
    cada salario de reserva candidato y quedarse con el que mas dinero dejo --
    es decir, la mejor version de nuestra propia politica si hubieramos sabido
    de antemano como iba a venir el turno.

    Eso lo hace **una cota superior de nuestra familia de politicas**, no el
    optimo teorico. Es la afirmacion honesta, y ademas es la util: la distancia
    entre `OurAgent` y `Oracle` es exactamente cuanto se pierde por tener que
    elegir el umbral a ciegas, que es una pregunta que si podemos contestar.

    Respeta las mismas constraints de seguridad, asi que su columna de
    violaciones tambien es cero -- si no, no seria comparable.

    Un intento anterior de Oracle (clarividente y goloso: rechazar un pedido
    sabiendo que viene uno mejor) quedo POR DEBAJO de OurAgent, o sea que no
    era cota de nada. Se conserva la anecdota porque explica por que esta
    version barre umbrales en vez de mirar el futuro pedido por pedido.
    """

    name = "Oracle"

    #: Umbrales candidatos. Incluye explicitamente el que usa OurAgent, para
    #: que el Oracle no pueda quedar por debajo de el.
    WAGE_SWEEP: tuple[float, ...] = tuple(
        sorted({float(w) for w in range(40, 601, 20)} | {GREEDY_RATE_MIN_MXN_HR})
    )

    @staticmethod
    def run_shift(config):
        """Corre el turno con cada umbral candidato y devuelve el mejor."""
        from core.evaluation.shift import ShiftRunner

        mejor = None
        for wage in Oracle.WAGE_SWEEP:
            estado = ShiftRunner(config).run(OurAgent(reservation_wage_mxn_hr=wage))
            if mejor is None or estado.earnings_mxn > mejor.earnings_mxn:
                mejor = estado
        return mejor


#: Orden de las filas del CSV, igual al del template oficial.
ALL_POLICIES: tuple[type, ...] = (
    AcceptAll,
    HighestPay,
    NearestFirst,
    GreedyRate,
    GreedyRateSafe,   # diagnostico, no es del template
    OurAgent,
    Oracle,
)
