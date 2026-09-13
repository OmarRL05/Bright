"""Interfaz compartida de distancias/tiempos entre paradas.

Bloque 3 (motor de decision) y, eventualmente, Bloque 5 (grafo real de
Monterrey, ver core/routing/graph.py) deben poder intercambiarse sin que
decision.py ni greedy.py sepan cual de los dos estan usando. Ver
docs/Bloque 3/01_Plan.md seccion 3 para el porque de este desacople.

core.routing.euclidean.EuclideanDistanceProvider (placeholder) y
core.routing.graph.RoadNetwork (Bloque 5, grafo real) implementan ambos este
Protocol con los mismos nombres de metodo -- se pueden intercambiar en
DecisionEngine sin adaptador.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class DistanceProvider(Protocol):
    def travel_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Tiempo de viaje estimado entre dos coordenadas, en minutos."""
        ...

    def travel_distance(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Distancia estimada entre dos coordenadas, en kilometros."""
        ...
