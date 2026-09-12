"""Interfaz compartida de distancias/tiempos entre paradas.

Bloque 3 (motor de decision) y, eventualmente, Bloque 5 (grafo real de
Monterrey, ver core/routing/graph.py) deben poder intercambiarse sin que
decision.py ni greedy.py sepan cual de los dos estan usando. Ver
docs/Bloque 3/01_Plan.md seccion 3 para el porque de este desacople.

RoadNetwork (Bloque 5) todavia no implementa este Protocol -- graph.py sigue
en NotImplementedError. Mientras tanto, core.routing.euclidean.EuclideanDistanceProvider
lo satisface como placeholder.
"""

from typing import Protocol


class DistanceProvider(Protocol):
    def travel_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Tiempo de viaje estimado entre dos coordenadas, en minutos."""
        ...

    def travel_distance(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Distancia estimada entre dos coordenadas, en kilometros."""
        ...
