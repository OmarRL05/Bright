"""Tests de GET /route (linea real sobre calles para el mapa, Bloque 5/6).

Usa un grafo sintetico chico (no el .graphml real de Monterrey, ~40MB) para
que corra rapido y no dependa de que ese archivo este descargado en la
maquina -- ver backend/data/README.md, no se commitea a git.
"""

import networkx as nx
import pytest
from fastapi.testclient import TestClient

import api.route as route_module
from core.routing.graph import RoadNetwork
from main import app

client = TestClient(app)


def make_tiny_graph() -> nx.MultiDiGraph:
    """Un solo tramo, con las mismas coordenadas que las zonas 0 (Tec) y 1
    (San Pedro) de DEFAULT_ZONE_MAP -- asi el mapeo zona->coordenada no
    hace snap a un nodo random y lejano."""
    g = nx.MultiDiGraph()
    g.graph["crs"] = "epsg:4326"
    g.add_node(1, x=-100.289, y=25.651)  # ~Tec (zone_id 0)
    g.add_node(2, x=-100.402, y=25.657)  # ~San Pedro (zone_id 1)
    g.add_edge(1, 2, key=0, travel_time=300, length=1000)
    g.add_edge(2, 1, key=0, travel_time=300, length=1000)
    return g


@pytest.fixture(autouse=True)
def _limpiar_cache():
    """Los `lru_cache` de route.py son a nivel de modulo -- sin limpiarlos
    ANTES de cada test, uno contamina al siguiente. No se limpia despues:
    para entonces `monkeypatch` ya pudo haber reemplazado `_network` por un
    lambda sin `.cache_clear()`, y el orden de teardown entre fixtures no
    esta garantizado."""
    route_module._network.cache_clear()
    route_module._cached_path.cache_clear()
    yield


def test_route_returns_coords_when_graph_available(monkeypatch):
    monkeypatch.setattr(route_module, "_network", lambda: RoadNetwork.from_graph(make_tiny_graph()))
    resp = client.get("/route", params={"from_zone": 0, "to_zone": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["from_zone"] == 0
    assert data["to_zone"] == 1
    assert len(data["coords"]) >= 2
    assert all(len(punto) == 2 for punto in data["coords"])


def test_route_503_when_graphml_missing(monkeypatch):
    monkeypatch.setattr(route_module, "_network", lambda: None)
    resp = client.get("/route", params={"from_zone": 0, "to_zone": 1})
    assert resp.status_code == 503
    assert "monterrey.graphml" in resp.json()["detail"]


def test_route_404_on_unknown_zone(monkeypatch):
    monkeypatch.setattr(route_module, "_network", lambda: RoadNetwork.from_graph(make_tiny_graph()))
    resp = client.get("/route", params={"from_zone": 0, "to_zone": 9999})
    assert resp.status_code == 404
