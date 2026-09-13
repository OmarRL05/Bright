import os

import pytest

# Bloque 4/5 dependen de paquetes pesados (osmnx/ortools) que no siempre estan
# instalados. Sin esto, su ausencia aborta la RECOLECCION de todo el suite y
# ningun test corre -- no solo estos dos archivos.
nx = pytest.importorskip("networkx")
pytest.importorskip("osmnx")
pytest.importorskip("ortools")

from core.models import RoadEvent
from core.routing.graph import RoadNetwork

GRAPHML_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "monterrey.graphml")


# IDs numericos porque osmnx.distance.nearest_nodes asume nodos estilo OSM
# (castea a int internamente) -- un grafo con IDs tipo string revienta ahi.
A, B, C, D = 1, 2, 3, 4


def make_diamond_graph() -> nx.MultiDiGraph:
    """A -> B -> D (rapido, 5 min / 1 km por tramo) y A -> C -> D (lento, 20
    min / 4 km por tramo), en ambos sentidos.

    Coordenadas ficticias solo para que `nearest_nodes` funcione; no
    corresponden a Monterrey real. `travel_time` en segundos y `length` en
    metros, igual que los produce osmnx, para que `travel_time`/`travel_distance`
    se comporten igual que sobre el grafo real.
    """
    g = nx.MultiDiGraph()
    g.graph["crs"] = "epsg:4326"
    nodes = {
        A: (0.000, 0.000),
        B: (0.010, 0.000),
        C: (0.000, 0.010),
        D: (0.010, 0.010),
    }
    for node_id, (lat, lon) in nodes.items():
        g.add_node(node_id, x=lon, y=lat)

    def add_edge(u, v, travel_time_s, length_m):
        g.add_edge(u, v, key=0, travel_time=travel_time_s, length=length_m)
        g.add_edge(v, u, key=0, travel_time=travel_time_s, length=length_m)

    add_edge(A, B, travel_time_s=300, length_m=1000)
    add_edge(B, D, travel_time_s=300, length_m=1000)
    add_edge(A, C, travel_time_s=1200, length_m=4000)
    add_edge(C, D, travel_time_s=1200, length_m=4000)
    return g


@pytest.fixture
def diamond_network() -> RoadNetwork:
    return RoadNetwork.from_graph(make_diamond_graph())


def test_eta_picks_the_faster_route_in_minutes(diamond_network):
    eta = diamond_network.travel_time((0.000, 0.000), (0.010, 0.010))
    assert eta == pytest.approx(10.0)


def test_distance_km_matches_the_faster_route(diamond_network):
    km = diamond_network.travel_distance((0.000, 0.000), (0.010, 0.010))
    assert km == pytest.approx(2.0)


def test_closure_on_committed_node_forces_reroute(diamond_network):
    diamond_network.apply_road_event(
        RoadEvent(type="closure", location=(0.010, 0.000), multiplier=None, timestamp=0.0)
    )
    eta = diamond_network.travel_time((0.000, 0.000), (0.010, 0.010))
    assert eta == pytest.approx(40.0)
    km = diamond_network.travel_distance((0.000, 0.000), (0.010, 0.010))
    assert km == pytest.approx(8.0)


def test_closure_returns_affected_edges_in_both_directions(diamond_network):
    affected = diamond_network.apply_road_event(
        RoadEvent(type="closure", location=(0.010, 0.000), multiplier=None, timestamp=0.0)
    )
    assert (B, D) in affected
    assert (A, B) in affected


def test_traffic_multiplier_increases_eta_but_not_distance(diamond_network):
    diamond_network.apply_road_event(
        RoadEvent(type="traffic", location=(0.010, 0.000), multiplier=3.0, timestamp=0.0)
    )
    # Un evento puntual en B afecta las 4 aristas incidentes a B (A->B, B->A,
    # B->D, D->B), asi que A->B->D pasa a costar (5*3) + (5*3) = 30 min,
    # todavia mas barato que A->C->D (40 min).
    eta = diamond_network.travel_time((0.000, 0.000), (0.010, 0.010))
    assert eta == pytest.approx(30.0)
    # El trafico no cambia cuantos metros mide el camino.
    km = diamond_network.travel_distance((0.000, 0.000), (0.010, 0.010))
    assert km == pytest.approx(2.0)


def test_eta_is_infinite_when_every_route_is_closed(diamond_network):
    # Los cierres se modelan como peso infinito, no como aristas removidas:
    # el camino "existe" estructuralmente pero cuesta infinito, no se lanza
    # NetworkXNoPath.
    for location in [(0.010, 0.000), (0.000, 0.010)]:
        diamond_network.apply_road_event(
            RoadEvent(type="closure", location=location, multiplier=None, timestamp=0.0)
        )
    eta = diamond_network.travel_time((0.000, 0.000), (0.010, 0.010))
    assert eta == float("inf")


@pytest.mark.skipif(not os.path.exists(GRAPHML_PATH), reason="monterrey.graphml no descargado")
class TestRealMonterreyGraph:
    @staticmethod
    @pytest.fixture(scope="class")
    def network() -> RoadNetwork:
        return RoadNetwork(graphml_path=GRAPHML_PATH)

    def test_graph_loads_with_travel_times(self, network):
        assert network.graph.number_of_nodes() > 0
        _, _, data = next(iter(network.graph.edges(data=True)))
        assert "travel_time" in data

    def test_eta_and_distance_between_real_points_are_positive_and_finite(self, network):
        macroplaza = (25.6714, -100.3097)
        tec_de_monterrey = (25.6514, -100.2895)

        eta = network.travel_time(macroplaza, tec_de_monterrey)
        km = network.travel_distance(macroplaza, tec_de_monterrey)

        assert 0 < eta < 60  # menos de una hora, sanity check
        assert 0 < km < 30  # menos de 30 km, sanity check

    def test_closure_near_origin_changes_or_maintains_eta(self, network):
        macroplaza = (25.6714, -100.3097)
        tec_de_monterrey = (25.6514, -100.2895)
        baseline = network.travel_time(macroplaza, tec_de_monterrey)

        affected = network.apply_road_event(
            RoadEvent(type="closure", location=macroplaza, multiplier=None, timestamp=0.0)
        )

        assert len(affected) > 0
        rerouted = network.travel_time(macroplaza, tec_de_monterrey)
        assert rerouted >= baseline


# ==========================================================================
# shortest_path_coords -- geometria real para el mapa (Bloque 6)
# ==========================================================================


def test_shortest_path_coords_follows_the_faster_route(diamond_network):
    coords = diamond_network.shortest_path_coords((0.000, 0.000), (0.010, 0.010))
    # A -> B -> D (la ruta rapida), no A -> C -> D.
    assert coords == [(0.000, 0.000), (0.010, 0.000), (0.010, 0.010)]


def test_shortest_path_coords_reroutes_after_closure(diamond_network):
    diamond_network.apply_road_event(
        RoadEvent(type="closure", location=(0.010, 0.000), multiplier=None, timestamp=0.0)
    )
    coords = diamond_network.shortest_path_coords((0.000, 0.000), (0.010, 0.010))
    assert coords == [(0.000, 0.000), (0.000, 0.010), (0.010, 0.010)]


def test_shortest_path_coords_survives_a_fully_closed_diamond(diamond_network):
    """Cerrar las dos rutas posibles NO produce None aqui -- un cierre se
    modela como peso infinito, no como arista removida (mismo criterio que
    `travel_time`/`travel_distance`, ver su docstring), asi que Dijkstra
    sigue encontrando *un* camino, solo que carisimo. `None` es para grafos
    genuinamente desconectados (ver el test de abajo), no para "todo
    cerrado" -- son dos escenarios distintos y confundirlos fue el error
    original de este test."""
    diamond_network.apply_road_event(
        RoadEvent(type="closure", location=[(0.000, 0.000), (0.010, 0.000)], multiplier=None, timestamp=0.0)
    )
    diamond_network.apply_road_event(
        RoadEvent(type="closure", location=[(0.000, 0.000), (0.000, 0.010)], multiplier=None, timestamp=0.0)
    )
    coords = diamond_network.shortest_path_coords((0.000, 0.000), (0.010, 0.010))
    assert coords is not None and coords[0] == (0.000, 0.000) and coords[-1] == (0.010, 0.010)


def test_shortest_path_coords_none_when_graph_is_disconnected():
    """None es para esto: dos componentes sin NINGUNA arista entre ellos --
    no hay peso que Dijkstra pueda seguir, ni siquiera uno infinito."""
    g = nx.MultiDiGraph()
    g.graph["crs"] = "epsg:4326"
    g.add_node(1, x=0.0, y=0.0)
    g.add_node(2, x=1.0, y=1.0)  # sin ninguna arista hacia/desde el nodo 1
    network = RoadNetwork.from_graph(g)
    assert network.shortest_path_coords((0.0, 0.0), (1.0, 1.0)) is None
