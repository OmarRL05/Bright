"""Placeholder de distancias: haversine + velocidad promedio asumida.

Implementa el Protocol DistanceProvider (ver distance_provider.py) sin
depender del grafo vial real (Bloque 5, core/routing/graph.py, todavia sin
implementar). Permite a Bloque 3 arrancar ya, tal como pide el reparto de
tareas ("puede arrancar con distancias euclidianas de placeholder mientras
Persona C termina el grafo real"). Ver docs/Bloque 3/01_Plan.md seccion 3.

Cuando graph.py este listo, se reemplaza la instancia de
EuclideanDistanceProvider por RoadNetwork en el punto donde se arme
quien lo consuma -- greedy.py no cambia, porque solo
conocen el Protocol.
"""

import math

EARTH_RADIUS_KM = 6371.0

# Velocidad promedio asumida para un repartidor urbano en Monterrey.
# Placeholder: no viene de datos reales, hay que calibrarlo con corridas de
# prueba (ver docs/02_Documentacion_Tecnica.md seccion 5 y docs/Bloque 3/01_Plan.md).
AVG_SPEED_KMH = 20.0


def _haversine_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lat1, lon1 = origin
    lat2, lon2 = destination

    if lat1 == lat2 and lon1 == lon2:
        return 0.0

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


class EuclideanDistanceProvider:
    """Placeholder de DistanceProvider: linea recta (haversine) a velocidad fija."""

    def __init__(self, avg_speed_kmh: float = AVG_SPEED_KMH) -> None:
        self.avg_speed_kmh = avg_speed_kmh

    def travel_distance(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        return _haversine_km(origin, destination)

    def travel_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        distance_km = self.travel_distance(origin, destination)
        return (distance_km / self.avg_speed_kmh) * 60.0
