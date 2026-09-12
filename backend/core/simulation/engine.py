"""Bloque 1 - Simulador de Entorno (Event Streamer).

Controla el reloj del turno y genera el stream de eventos (ofertas, surge,
cierres/trafico). Debe usar una semilla fija para que el agente IA y el
baseline reciban EXACTAMENTE el mismo stream. Ver docs/01_Arquitectura.md
seccion 3 (Bloque 1).
"""

import random
from collections.abc import Iterator

from core.models import Offer, RoadEvent


class SimulationEngine:

    def __init__(self, seed: int, shift_duration: float) -> None:
        self.seed = seed
        self.shift_duration = shift_duration
        self._rng = random.Random(seed)

        self.current_time = 0.0
        self.offer_counter = 0

        self._zones = [
            (25.651, -100.289),  # Tec
            (25.657, -100.402),  # San Pedro
            (25.680, -100.310),  # Centro
            (25.780, -100.180),  # Apodaca
        ]

    def _generate_offer(self) -> Offer:
        """Genera una oferta nueva reproducible."""

        self.offer_counter += 1

        pickup = self._rng.choice(self._zones)
        dropoff = self._rng.choice(self._zones)

        pay = float(self._rng.randint(40, 150))

        return Offer(
            id=f"offer_{self.offer_counter}",
            pickup=pickup,
            dropoff=dropoff,
            pay=pay,
            time_window=(
                self.current_time,
                self.current_time + 30.0,
            ),
            received_at=self.current_time,
        )

    def _generate_road_event(self) -> RoadEvent:
        """Genera eventos externos del entorno."""

        event_type = self._rng.choice(
            [
                "traffic",
                "closure",
                "surge",
            ]
        )

        location = self._rng.choice(self._zones)

        multiplier = None

        if event_type == "surge":
            multiplier = 1.5

        return RoadEvent(
            type=event_type,
            location=location,
            multiplier=multiplier,
            timestamp=self.current_time,
        )

    def event_stream(self) -> Iterator[Offer | RoadEvent]:
        """Genera el stream reproducible de eventos del turno.

        Implementación inicial:
        Genera eventos sintéticos reproducibles usando self._rng.

        TODO(equipo): sustituir posteriormente la fuente sintética por datasets
        Solomon VRPTW / Kaggle food-delivery manteniendo los contratos Offer /
        RoadEvent y la reproducibilidad mediante seed.
        """

        while self.current_time < self.shift_duration:

            self.current_time += 1

            chance = self._rng.random()

            # 40% probabilidad de nueva oferta
            if chance < 0.40:
                yield self._generate_offer()

            # 10% probabilidad de evento externo
            elif chance < 0.50:
                yield self._generate_road_event()