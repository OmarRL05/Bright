import networkx as nx
import pytest

from core.models import Offer, RouteStop
from core.routing.graph import RoadNetwork
from core.routing.ortools_optimizer import GlobalOptimizer
from core.simulation.state import CourierStateManager

# Linea de 4 nodos igualmente espaciados: N0 -- N10 -- N20 -- N30 (lon en
# grados, lat fija en 0). Al ser un grafo de "linea", el tiempo/distancia
# mas corto entre dos puntos siempre es la suma de los tramos intermedios
# -- como una recta -- lo que hace faciles de predecir las rutas optimas.
N0, N10, N20, N30 = 1, 2, 3, 4


def make_line_network() -> RoadNetwork:
    g = nx.MultiDiGraph()
    g.graph["crs"] = "epsg:4326"
    coords = {N0: 0.000, N10: 0.010, N20: 0.020, N30: 0.030}
    for node_id, lon in coords.items():
        g.add_node(node_id, x=lon, y=0.0)

    def add_edge(u, v):
        g.add_edge(u, v, key=0, travel_time=10.0, length=10.0)
        g.add_edge(v, u, key=0, travel_time=10.0, length=10.0)

    add_edge(N0, N10)
    add_edge(N10, N20)
    add_edge(N20, N30)
    return RoadNetwork.from_graph(g)


def make_offers() -> tuple[Offer, Offer]:
    # o1: pickup en N0 (ya recogido, es el tramo comprometido), dropoff en N30.
    # o2: pickup en N10, dropoff en N20 -- geograficamente "en medio".
    o1 = Offer(
        id="o1",
        pickup=(0.0, 0.000),
        dropoff=(0.0, 0.030),
        pay=50.0,
        time_window=(0.0, 999.0),
        received_at=0.0,
    )
    o2 = Offer(
        id="o2",
        pickup=(0.0, 0.010),
        dropoff=(0.0, 0.020),
        pay=30.0,
        time_window=(0.0, 999.0),
        received_at=0.0,
    )
    return o1, o2


def setup_manager_with_suboptimal_order() -> CourierStateManager:
    o1, o2 = make_offers()
    manager = CourierStateManager(shift_duration=480.0)
    # Ruta dada en orden ingenuo: primero terminar o1 (el mas lejano), luego
    # atender o2 -- obliga a "pasar de largo" y regresar.
    route = [
        RouteStop(offer_id="o1", kind="pickup", eta=0.0),  # frozen: ya en curso
        RouteStop(offer_id="o1", kind="dropoff", eta=30.0),
        RouteStop(offer_id="o2", kind="pickup", eta=50.0),
        RouteStop(offer_id="o2", kind="dropoff", eta=60.0),
    ]
    manager.accept_offer(o1, route)
    manager._backpack.append(o2)  # simula que Bloque 3 ya acepto tambien o2
    return manager


def test_reorders_pending_stops_when_improvement_exceeds_threshold():
    manager = setup_manager_with_suboptimal_order()
    optimizer = GlobalOptimizer(manager, make_line_network())

    thread = optimizer.solve_async(override=False)
    thread.join()

    snap = manager.snapshot()
    assert snap.version == 2  # 1 por accept_offer + 1 por la ruta optimizada
    kinds = [(s.offer_id, s.kind) for s in snap.route]
    assert kinds == [
        ("o1", "pickup"),  # frozen, sin cambios
        ("o2", "pickup"),
        ("o2", "dropoff"),
        ("o1", "dropoff"),
    ]


def test_precedence_pickup_before_dropoff_is_respected():
    manager = setup_manager_with_suboptimal_order()
    optimizer = GlobalOptimizer(manager, make_line_network())

    optimizer.solve_async(override=False).join()

    route = manager.snapshot().route
    pickup_idx = next(i for i, s in enumerate(route) if s.offer_id == "o2" and s.kind == "pickup")
    dropoff_idx = next(i for i, s in enumerate(route) if s.offer_id == "o2" and s.kind == "dropoff")
    assert pickup_idx < dropoff_idx


def test_skips_update_when_route_is_already_optimal():
    o1, o2 = make_offers()
    manager = CourierStateManager(shift_duration=480.0)
    already_optimal_route = [
        RouteStop(offer_id="o1", kind="pickup", eta=0.0),
        RouteStop(offer_id="o2", kind="pickup", eta=10.0),
        RouteStop(offer_id="o2", kind="dropoff", eta=20.0),
        RouteStop(offer_id="o1", kind="dropoff", eta=30.0),
    ]
    manager.accept_offer(o1, already_optimal_route)
    manager._backpack.append(o2)
    version_before = manager.snapshot().version

    optimizer = GlobalOptimizer(manager, make_line_network())
    optimizer.solve_async(override=False).join()

    assert manager.snapshot().version == version_before


def test_override_applies_even_without_improvement():
    o1, o2 = make_offers()
    manager = CourierStateManager(shift_duration=480.0)
    already_optimal_route = [
        RouteStop(offer_id="o1", kind="pickup", eta=0.0),
        RouteStop(offer_id="o2", kind="pickup", eta=10.0),
        RouteStop(offer_id="o2", kind="dropoff", eta=20.0),
        RouteStop(offer_id="o1", kind="dropoff", eta=30.0),
    ]
    manager.accept_offer(o1, already_optimal_route)
    manager._backpack.append(o2)
    version_before = manager.snapshot().version

    optimizer = GlobalOptimizer(manager, make_line_network())
    optimizer.solve_async(override=True).join()

    assert manager.snapshot().version == version_before + 1


def test_stale_snapshot_is_discarded_if_state_changed_mid_solve():
    manager = setup_manager_with_suboptimal_order()
    optimizer = GlobalOptimizer(manager, make_line_network())

    # Simula que Bloque 3 acepta OTRA oferta mientras el optimizador seguia
    # resolviendo con un snapshot viejo: apply_optimized_route debe
    # descartar el resultado en vez de sobreescribir la mochila nueva.
    stale_snapshot = manager.snapshot()
    manager.tick(1.0)  # cualquier mutacion que no pase por accept_offer
    manager._version += 1  # fuerza el desfase de version directamente

    applied = manager.apply_optimized_route(stale_snapshot.version, [])
    assert applied is False
