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

# Factor de rodeo: cuanto mas largo es el camino real que la linea recta.
#
# Sin esto, el sistema mide con haversine entre centroides de zona -- una
# recta que atraviesa manzanas, el rio y el Cerro de la Silla. No es un
# detalle cosmetico del mapa: con esas distancias se calculan el combustible,
# los tiempos de entrega y, encima de eso, la calibracion del salario de
# reserva. Todo el sistema salia optimista en la misma proporcion.
#
# Medido contra distancias reales en coche sobre cuatro pares del ZoneMap:
#
#     Contry        -> Escobedo          19.15 km recta,  ~26 km  ->  1.36
#     San Pedro     -> Apodaca           26.11 km recta,  ~34 km  ->  1.30
#     Santa Catarina-> Parque Industrial 24.98 km recta,  ~33 km  ->  1.32
#     Centro        -> Tec                3.85 km recta, ~6.5 km  ->  1.69
#
# Se toma 1.35, que es la zona donde caen los trayectos largos. **Sesgo
# conocido**: los trayectos cortos quedan subestimados (el factor real sube
# cuando no puedes ir en diagonal por las manzanas). Un factor dependiente de
# la distancia seria mas exacto y menos defendible -- no tenemos datos para
# calibrar esa curva, y si los tuvieramos convendria usar el grafo vial real.
#
# Mismo criterio y mismo valor que CLOSURE_DETOUR_FACTOR en
# core/agent/shocks.py, que modela el rodeo EXTRA de un cierre sobre este.
ROAD_DETOUR_FACTOR = 1.35

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
    """DistanceProvider sin grafo: haversine con factor de rodeo, a velocidad fija.

    Aproxima la distancia por carretera sin necesitar el grafo vial: recta por
    un factor de circuidad. No da la GEOMETRIA del camino -- para dibujar una
    ruta de verdad en el mapa hace falta `core.routing.graph.RoadNetwork`, que
    necesita osmnx y el graphml de Monterrey. Lo que si da es una **distancia
    honesta**, que es de lo que dependen el combustible, el tiempo y el
    umbral de aceptacion.

    Quien pinte un mapa con esto debe etiquetar la linea como estimacion entre
    zonas, no como ruta: `GET /status` publica `distance_model` justo para que
    esa etiqueta salga del sistema y no de una cadena escrita a mano en el
    frontend.
    """

    def __init__(
        self,
        avg_speed_kmh: float = AVG_SPEED_KMH,
        detour_factor: float = ROAD_DETOUR_FACTOR,
    ) -> None:
        self.avg_speed_kmh = avg_speed_kmh
        self.detour_factor = detour_factor

    def travel_distance(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        return _haversine_km(origin, destination) * self.detour_factor

    def travel_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        distance_km = self.travel_distance(origin, destination)
        return (distance_km / self.avg_speed_kmh) * 60.0
