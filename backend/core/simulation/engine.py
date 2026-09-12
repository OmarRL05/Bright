"""Bloque 1 - Simulador de Entorno (Event Streamer).

Controla el reloj del turno y genera el stream de eventos (ofertas, surge,
cierres/trafico). Debe usar una semilla fija para que el agente IA y el
baseline reciban EXACTAMENTE el mismo stream. Ver docs/01_Arquitectura.md
seccion 3 (Bloque 1).

TODO(equipo): implementar la logica de generacion de eventos.
"""

import random
from collections.abc import Iterator

from core.models import Offer, RoadEvent


class SimulationEngine:
    def __init__(self, seed: int, shift_duration: float) -> None:
        self.seed = seed
        self.shift_duration = shift_duration
        self._rng = random.Random(seed)

    def event_stream(self) -> Iterator[Offer | RoadEvent]:
        """Genera el stream reproducible de eventos del turno.

        TODO(equipo): leer datasets (Solomon VRPTW / Kaggle food-delivery,
        ver docs/02_Documentacion_Tecnica.md seccion 4) y emitir Offer /
        RoadEvent en orden de tiempo, usando self._rng para cualquier
        aleatoriedad (nunca random global) para mantener reproducibilidad.
        """
        raise NotImplementedError
