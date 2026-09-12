"""Bloque 5 - Motor Topologico (OSMnx + NetworkX).

Mantiene el grafo vial de Monterrey y resuelve caminos mas cortos. Ante
eventos de cierre/trafico, ajusta el peso de las aristas afectadas.
Ver docs/01_Arquitectura.md seccion 3 (Bloque 5).

TODO(equipo): implementar carga del grafo y recalculo de rutas.
"""

import networkx as nx
import osmnx as ox

from core.models import RoadEvent


class RoadNetwork:
    def __init__(self, graphml_path: str) -> None:
        """Carga el grafo vial precalculado (ver backend/data/monterrey.graphml).

        TODO(equipo): si no existe el .graphml, descargarlo con
        ox.graph_from_place("Monterrey, Mexico", network_type="drive") y
        guardarlo con ox.save_graphml para no re-descargar en cada arranque.
        """
        self.graph: nx.MultiDiGraph = ox.load_graphml(graphml_path)

    def shortest_path_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Devuelve el tiempo de viaje mas corto entre dos coordenadas.

        TODO(equipo): mapear coordenadas a nodos del grafo (ox.distance.nearest_nodes)
        y resolver con nx.shortest_path_length usando el peso "travel_time".
        """
        raise NotImplementedError

    def apply_road_event(self, event: RoadEvent) -> list[tuple[int, int]]:
        """Ajusta pesos de aristas afectadas por un cierre/trafico.

        Devuelve la lista de aristas afectadas para que Bloque 4/3 sepan si
        el tramo comprometido del courier quedo invalidado.

        TODO(equipo): para "closure" asignar peso infinito; para "traffic"
        aplicar un multiplicador al peso de las aristas en la zona.
        """
        raise NotImplementedError
