"""Tests de GET /zones (soporte del mapa del dashboard, Bloque 6)."""

from fastapi.testclient import TestClient

from core.agent.safety import FLAGGED_ZONES
from core.models import DEFAULT_ZONE_MAP
from main import app

client = TestClient(app)


def test_zones_returns_all_sixteen():
    resp = client.get("/zones")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["zones"]) == len(DEFAULT_ZONE_MAP.zones) == 16


def test_zones_have_the_shape_the_map_needs():
    resp = client.get("/zones")
    zone = resp.json()["zones"][0]
    assert set(zone.keys()) == {"zone_id", "name", "coord", "demand_score", "flagged"}
    assert isinstance(zone["coord"], list) and len(zone["coord"]) == 2


def test_zones_flagged_matches_safety_flagged_zones():
    """No es un color elegido a mano en el frontend -- es la misma constraint
    que usa /decide."""
    resp = client.get("/zones")
    flagged_ids = {z["zone_id"] for z in resp.json()["zones"] if z["flagged"]}
    assert flagged_ids == set(FLAGGED_ZONES)
    assert len(flagged_ids) > 0  # que no se nos olvide marcar ninguna
