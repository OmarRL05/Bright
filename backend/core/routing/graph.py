"""Bloque 5 - Motor Topologico (OSMnx + NetworkX).

Mantiene el grafo vial de Monterrey y resuelve caminos mas cortos. Ante
eventos de cierre/trafico, ajusta el peso de las aristas afectadas.
Ver docs/01_Arquitectura.md seccion 3 (Bloque 5).
"""

from __future__ import annotations

import threading

import networkx as nx
import osmnx as ox

from core.models import RoadEvent

# Nombres alineados a core.routing.distance_provider.DistanceProvider (ver
# docs/Bloque 3/01_Plan.md seccion 3 y seccion 11 pregunta #1): RoadNetwork
# debe satisfacer ese Protocol estructuralmente para que DecisionEngine
# pueda intercambiarlo con EuclideanDistanceProvider sin adaptador.


class RoadNetwork:
    def __init__(self, graphml_path: str | None = None, graph: nx.MultiDiGraph | None = None) -> None:
        """Carga el grafo vial precalculado (ver backend/data/monterrey.graphml).

        Acepta `graph` directamente (en vez de `graphml_path`) para poder
        inyectar un grafo sintetico pequeno en tests, sin depender del
        .graphml real de Monterrey.
        """
        if graph is not None:
            self.graph = graph
        elif graphml_path is not None:
            self.graph = ox.load_graphml(graphml_path)
        else:
            raise ValueError("Debe pasar graphml_path o graph")

        if not self._has_travel_times():
            self.graph = ox.routing.add_edge_speeds(self.graph)
            self.graph = ox.routing.add_edge_travel_times(self.graph)

        # Bloque 3 (hilo principal) y Bloque 4 (hilo background) consultan y
        # mutan este objeto de forma concurrente: ver docs/01_Arquitectura.md
        # seccion 5. En vez de mutar los pesos del grafo directamente (lo que
        # obligaria a tomar el lock incluso para lecturas), los cierres y el
        # trafico se guardan aparte y se aplican via la funcion de peso.
        self._lock = threading.RLock()
        self._blocked_edges: set[tuple[int, int]] = set()
        self._traffic_multipliers: dict[tuple[int, int], float] = {}

    @classmethod
    def from_graph(cls, graph: nx.MultiDiGraph) -> "RoadNetwork":
        """Constructor para tests: evita cargar/descargar un .graphml real."""
        return cls(graph=graph)

    def _has_travel_times(self) -> bool:
        try:
            _, _, data = next(iter(self.graph.edges(data=True)))
        except StopIteration:
            return True
        return "travel_time" in data

    def _weight_fn(self, attr: str, apply_traffic: bool):
        """Construye la funcion de peso para Dijkstra sobre `attr`
        ("travel_time" o "length"), respetando cierres siempre y el
        multiplicador de trafico solo cuando `apply_traffic` (el trafico
        cambia el tiempo de viaje, no la distancia fisica).
        """

        def weight(u: int, v: int, data: dict) -> float:
            if (u, v) in self._blocked_edges:
                return float("inf")
            base = min(attrs[attr] for attrs in data.values())
            if apply_traffic:
                return base * self._traffic_multipliers.get((u, v), 1.0)
            return base

        return weight

    def _nearest_node(self, coord: tuple[float, float]) -> int:
        lat, lon = coord
        return ox.distance.nearest_nodes(self.graph, X=lon, Y=lat)

    def travel_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Tiempo de viaje mas corto entre dos coordenadas, en minutos.

        Bajo demanda (no matriz precalculada): es lo que consulta Bloque 3
        por cada oferta. La matriz completa entre paradas pendientes, para
        el VRPTW de Bloque 4, se arma llamando esto en pares, no aqui.

        Los cierres se modelan como peso infinito en vez de remover aristas,
        asi que si todas las rutas posibles estan cerradas esto devuelve
        `float("inf")` en vez de lanzar `networkx.NetworkXNoPath`.
        """
        with self._lock:
            o = self._nearest_node(origin)
            d = self._nearest_node(destination)
            seconds = nx.shortest_path_length(self.graph, o, d, weight=self._weight_fn("travel_time", apply_traffic=True))
            return seconds / 60.0

    def travel_distance(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Distancia mas corta entre dos coordenadas, en km.

        No se ve afectada por multiplicadores de trafico (el trafico cambia
        cuanto tarda el trayecto, no cuantos metros mide); si tiene un
        cierre en el camino, tambien devuelve `float("inf")`.
        """
        with self._lock:
            o = self._nearest_node(origin)
            d = self._nearest_node(destination)
            meters = nx.shortest_path_length(self.graph, o, d, weight=self._weight_fn("length", apply_traffic=False))
            return meters / 1000.0

    def shortest_path_coords(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> list[tuple[float, float]] | None:
        """Coordenadas (lat, lon) del camino mas corto, nodo por nodo.

        Para dibujar la ruta REAL sobre calles (Bloque 6, mapa del
        dashboard) en vez de una linea recta zona->zona. `travel_time`/
        `travel_distance` ya resuelven este mismo Dijkstra pero solo
        devuelven el numero -- esto devuelve la geometria.

        None solo si el grafo esta genuinamente desconectado (sin ninguna
        arista entre origen y destino) -- NO si "todo esta cerrado": un
        cierre es peso infinito, no arista removida (mismo criterio que
        `travel_time`/`travel_distance`, ver su docstring), asi que Dijkstra
        igual devuelve un camino aunque sea carisimo. Quien dibuja el mapa
        decide que hacer con una ruta cara (o con `None`), no esta funcion.
        """
        with self._lock:
            o = self._nearest_node(origin)
            d = self._nearest_node(destination)
            try:
                nodes = nx.shortest_path(
                    self.graph, o, d, weight=self._weight_fn("travel_time", apply_traffic=True)
                )
            except nx.NetworkXNoPath:
                return None
            return [(self.graph.nodes[n]["y"], self.graph.nodes[n]["x"]) for n in nodes]

    def apply_road_event(self, event: RoadEvent) -> list[tuple[int, int]]:
        """Ajusta pesos de aristas afectadas por un cierre/trafico.

        Devuelve la lista de aristas afectadas para que Bloque 4/3 sepan si
        el tramo comprometido del courier quedo invalidado.
        """
        points = event.location if isinstance(event.location, list) else [event.location]
        nodes = [self._nearest_node(p) for p in points]

        with self._lock:
            affected = self._edges_for_nodes(nodes)
            for u, v in affected:
                if event.type == "closure":
                    self._blocked_edges.add((u, v))
                elif event.type == "traffic":
                    self._traffic_multipliers[(u, v)] = event.multiplier or 1.0
            return affected

    def _edges_for_nodes(self, nodes: list[int]) -> list[tuple[int, int]]:
        """Aristas afectadas por un evento.

        Un solo punto (cierre puntual) bloquea todas las aristas incidentes
        a ese nodo, en ambos sentidos. Una lista de puntos (tramo de calle)
        bloquea las aristas entre puntos consecutivos, tambien en ambos
        sentidos (una calle cerrada lo esta para los dos sentidos de
        circulacion salvo que el evento diga lo contrario).
        """
        affected: set[tuple[int, int]] = set()
        if len(nodes) == 1:
            node = nodes[0]
            for _, v in self.graph.out_edges(node):
                affected.add((node, v))
            for u, _ in self.graph.in_edges(node):
                affected.add((u, node))
        else:
            for u, v in zip(nodes, nodes[1:]):
                affected.add((u, v))
                affected.add((v, u))
        return list(affected)
