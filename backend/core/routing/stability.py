"""Triggers del Bloque 4 (batch / idle / override).

Ver docs/01_Arquitectura.md seccion 3 (Bloque 4, tabla de disparadores).

TODO(equipo): implementar el conteo de ofertas sin optimizar y la deteccion
de idle/override para decidir cuando llamar a GlobalOptimizer.solve_async().
"""

from dataclasses import dataclass
from enum import Enum


class TriggerType(str, Enum):
    BATCH = "batch"
    IDLE = "idle"
    OVERRIDE = "override"


@dataclass
class Trigger:
    type: TriggerType
    is_emergency: bool = False


class StabilityController:
    def __init__(self, batch_size: int = 3) -> None:
        self.batch_size = batch_size
        self._pending_unoptimized = 0

    def on_offer_accepted(self) -> Trigger | None:
        self._pending_unoptimized += 1
        if self._pending_unoptimized >= self.batch_size:
            self._pending_unoptimized = 0
            return Trigger(TriggerType.BATCH)
        return None

    def on_delivery_completed(self, next_leg_pending: bool) -> Trigger | None:
        if next_leg_pending:
            return Trigger(TriggerType.IDLE)
        return None

    def on_committed_leg_invalidated(self) -> Trigger:
        return Trigger(TriggerType.OVERRIDE, is_emergency=True)
