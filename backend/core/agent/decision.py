"""Bloque 3 - Motor de Decision en Tiempo Real.

Evalua cada oferta apenas llega: heuristica de insercion + umbral +
frozen horizon + log explicable. Ver docs/01_Arquitectura.md seccion 3
(Bloque 3).

TODO(equipo): implementar la logica de aceptacion/rechazo.
"""

from dataclasses import dataclass

from core.models import Offer, RouteStop
from core.routing.greedy import cheapest_insertion
from core.simulation.state import CourierStateManager

MIN_PAY_PER_KM = 8.0  # regla minima configurable, ej. MXN/km


@dataclass
class Decision:
    accepted: bool
    offer: Offer
    reason: str
    new_route: list[RouteStop] | None = None


class DecisionEngine:
    def __init__(self, state_manager: CourierStateManager) -> None:
        self.state_manager = state_manager

    def evaluate(self, offer: Offer) -> Decision:
        """TODO(equipo):
        1. snapshot = self.state_manager.snapshot()
        2. insertion = cheapest_insertion(snapshot.route, offer, frozen_index=...)
        3. comparar costo marginal vs pago, tiempo restante, $/km minimo y
           senal de demanda historica de la zona
        4. si acepta: self.state_manager.accept_offer(offer, insertion.new_route)
        5. generar el log con core.agent.llm_log.format_decision_log(...)
        """
        raise NotImplementedError
