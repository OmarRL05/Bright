"""Bloque 3 - Motor de Decision en Tiempo Real.

Evalua cada oferta apenas llega: heuristica de insercion + umbral +
frozen horizon + log explicable. Ver docs/01_Arquitectura.md seccion 3
(Bloque 3) y docs/Bloque 3/01_Plan.md secciones 5 y 6 para el detalle de
diseno (derivacion de frozen_index, orden de las reglas de umbral).

Nota de diseno: el stub original solo recibia `state_manager` en el
constructor. Se agregaron `distance_provider` y `demand_signal` (ambos
Protocol, ver core.routing.distance_provider y core.agent.demand) como
dependencias inyectadas, siguiendo el mismo patron de desacople que
greedy.py. `Decision` gano un campo `log` con el string ya formateado, para
que quien consuma esto (loop de simulacion, feed del dashboard) no tenga que
volver a llamar a format_decision_log. Ver docs/Bloque 3/doc-scripts/ para
el desglose completo.
"""

from dataclasses import dataclass

from core.agent.demand import DemandSignal
from core.agent.llm_log import format_decision_log
from core.models import Offer, RouteStop
from core.routing.distance_provider import DistanceProvider
from core.routing.greedy import InsertionResult, cheapest_insertion
from core.simulation.state import CourierStateManager

MIN_PAY_PER_KM = 8.0  # regla minima configurable, ej. MXN/km

# Relajacion maxima del umbral $/km en la zona de demanda mas caliente
# (demand_score=1.0): min_pay_per_km efectivo = MIN_PAY_PER_KM * (1 - DEMAND_DISCOUNT).
# Placeholder a calibrar con corridas reales (Fase 4 del cronograma).
DEMAND_DISCOUNT = 0.3

# Por debajo de este desvio (minutos) se considera "practicamente cero" para
# la excepcion de frozen horizon (docs/01_Arquitectura.md seccion 6, regla 1):
# "esta literalmente en el camino". Placeholder a calibrar.
MARGINAL_TIME_EPSILON = 1.0

# Por debajo de este desvio de distancia (km) se acepta sin evaluar $/km, para
# no dividir entre un numero practicamente cero.
MIN_EXTRA_DISTANCE_KM = 0.05


@dataclass
class Decision:
    accepted: bool
    offer: Offer
    reason: str
    new_route: list[RouteStop] | None = None
    log: str = ""


class DecisionEngine:
    def __init__(
        self,
        state_manager: CourierStateManager,
        distance_provider: DistanceProvider,
        demand_signal: DemandSignal,
    ) -> None:
        self.state_manager = state_manager
        self.distance_provider = distance_provider
        self.demand_signal = demand_signal

    def evaluate(self, offer: Offer) -> Decision:
        snapshot = self.state_manager.snapshot()
        accepted_offers = {o.id: o for o in snapshot.backpack}

        # El primer RouteStop (si existe) es el tramo en transito -- no se
        # reordena salvo la excepcion de costo marginal ~0 (ver mas abajo).
        # Ver docs/Bloque 3/01_Plan.md seccion 5.
        frozen_index = 1 if snapshot.route else 0

        # Busqueda sin restriccion: unica forma de detectar el caso "va en el
        # camino" (regla 1 de docs/01_Arquitectura.md seccion 6), y ademas un
        # atajo correcto -- si ni siquiera aqui hay una posicion factible, la
        # busqueda restringida (subconjunto de estas posiciones) tampoco la va
        # a tener.
        unrestricted = cheapest_insertion(
            snapshot.route,
            offer,
            frozen_index=0,
            accepted_offers=accepted_offers,
            distance_provider=self.distance_provider,
        )

        if not unrestricted.feasible:
            return self._reject(offer, "no cabe en ninguna posicion dentro de su ventana de tiempo")

        if unrestricted.extra_time <= MARGINAL_TIME_EPSILON:
            if unrestricted.extra_time > snapshot.time_remaining:
                return self._reject(
                    offer,
                    f"quedan {snapshot.time_remaining:.0f} min, se necesitan {unrestricted.extra_time:.1f}",
                )
            reason = f"desvio ~0 ({unrestricted.extra_time:.1f} min), va en la ruta"
            return self._accept(offer, unrestricted, reason)

        insertion = cheapest_insertion(
            snapshot.route,
            offer,
            frozen_index=frozen_index,
            accepted_offers=accepted_offers,
            distance_provider=self.distance_provider,
        )

        if not insertion.feasible:
            reason = "solo cabe invadiendo el tramo comprometido, y el desvio no es despreciable"
            return self._reject(offer, reason)

        if insertion.extra_time > snapshot.time_remaining:
            reason = f"quedan {snapshot.time_remaining:.0f} min, se necesitan {insertion.extra_time:.0f}"
            return self._reject(offer, reason)

        if insertion.extra_distance <= MIN_EXTRA_DISTANCE_KM:
            reason = f"desvio de distancia despreciable ({insertion.extra_distance:.2f} km)"
            return self._accept(offer, insertion, reason)

        demand_score = self.demand_signal.zone_score(offer.pickup)
        min_pay_per_km = MIN_PAY_PER_KM * (1 - demand_score * DEMAND_DISCOUNT)
        pay_per_km = offer.pay / insertion.extra_distance

        if pay_per_km < min_pay_per_km:
            reason = (
                f"desvio de {insertion.extra_time:.0f} min por ${offer.pay:.0f} "
                f"(${pay_per_km:.1f}/km < minimo ${min_pay_per_km:.1f}/km, demanda={demand_score:.2f})"
            )
            return self._reject(offer, reason)

        reason = (
            f"${pay_per_km:.1f}/km >= minimo ${min_pay_per_km:.1f}/km "
            f"(demanda={demand_score:.2f}), desvio de {insertion.extra_time:.0f} min"
        )
        return self._accept(offer, insertion, reason)

    def _accept(self, offer: Offer, insertion: InsertionResult, reason: str) -> Decision:
        self.state_manager.accept_offer(offer, insertion.new_route)
        log = format_decision_log(True, offer.id, reason)
        return Decision(accepted=True, offer=offer, reason=reason, new_route=insertion.new_route, log=log)

    def _reject(self, offer: Offer, reason: str) -> Decision:
        log = format_decision_log(False, offer.id, reason)
        return Decision(accepted=False, offer=offer, reason=reason, new_route=None, log=log)
