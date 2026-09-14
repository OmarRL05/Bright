"""Tests de GET /route (geometria de calle real para el mapa, Bloque 5/6).

Ningun test de aqui toca la red. OSRM se sustituye por un doble, y la cache
por una vacia, porque la propiedad que interesa probar no es "OSRM funciona"
-- eso es del servidor de OSRM -- sino las tres que son nuestras:

1. Con cache, la red no se toca. Es lo que hace que el mapa trace calle real
   durante el ensayo con el wifi apagado (protocolo, seccion 7).
2. Sin cache y sin red, la respuesta es un 503 explicito. El frontend dibuja
   linea recta y la ROTULA -- nunca una aproximacion disfrazada de medicion,
   que es exactamente el fallo por el que se retiro el grafo local.
3. `source` dice de donde salio la geometria, siempre.
"""

import pytest
from fastapi.testclient import TestClient

import api.route as route_module
from core.routing.osrm import Ruta, RouteCache, simplify
from main import app

client = TestClient(app)

RUTA_DE_PRUEBA = Ruta(
    coords=((25.651, -100.289), (25.654, -100.35), (25.657, -100.402)),
    distance_km=14.2,
    duration_min=28.0,
)


@pytest.fixture(autouse=True)
def cache_vacia(monkeypatch, tmp_path):
    """Cache aislada por test.

    La real vive en `data/route_cache.json` y esta COMMITEADA con 240 pares:
    sin aislarla, los tests que prueban el camino "sin geometria" pasarian por
    la cache de verdad y no probarian nada.
    """
    cache = RouteCache(tmp_path / "route_cache.json")
    monkeypatch.setattr(route_module, "_cache", cache)
    return cache


def sin_red(*args, **kwargs):
    """OSRM caido / sin internet. `fetch_route` devuelve None, nunca levanta."""
    return None


def test_devuelve_geometria_de_la_cache_sin_tocar_la_red(cache_vacia, monkeypatch):
    cache_vacia.put("0-1", RUTA_DE_PRUEBA)

    def explota(*args, **kwargs):
        raise AssertionError("no debio pedirle nada a OSRM: el par estaba cacheado")

    monkeypatch.setattr(route_module, "fetch_route", explota)

    resp = client.get("/route", params={"from_zone": 0, "to_zone": 1})
    assert resp.status_code == 200
    datos = resp.json()
    assert datos["source"] == "cache"
    assert len(datos["coords"]) == 3
    assert datos["distance_km"] == 14.2


def test_pide_a_osrm_lo_que_no_esta_cacheado_y_lo_guarda_en_memoria(cache_vacia, monkeypatch):
    llamadas = []

    def falso_fetch(puntos, **kwargs):
        llamadas.append(puntos)
        return RUTA_DE_PRUEBA

    monkeypatch.setattr(route_module, "fetch_route", falso_fetch)

    primera = client.get("/route", params={"from_zone": 0, "to_zone": 1})
    assert primera.status_code == 200
    assert primera.json()["source"] == "osrm"

    # La segunda ya sale de la cache: una peticion por par, no una por render.
    segunda = client.get("/route", params={"from_zone": 0, "to_zone": 1})
    assert segunda.json()["source"] == "cache"
    assert len(llamadas) == 1


def test_503_explicito_cuando_no_hay_cache_ni_red(cache_vacia, monkeypatch):
    monkeypatch.setattr(route_module, "fetch_route", sin_red)
    resp = client.get("/route", params={"from_zone": 0, "to_zone": 1})
    assert resp.status_code == 503
    # El mensaje tiene que decir como arreglarlo, no solo que fallo.
    assert "warm_routes" in resp.json()["detail"]


def test_404_en_zona_desconocida(cache_vacia, monkeypatch):
    monkeypatch.setattr(route_module, "fetch_route", sin_red)
    resp = client.get("/route", params={"from_zone": 0, "to_zone": 9999})
    assert resp.status_code == 404


def test_404_cuando_origen_y_destino_son_la_misma_zona(cache_vacia, monkeypatch):
    """OSRM devolveria una ruta de longitud cero y el mapa la dibujaria como
    un punto suelto sobre la zona."""
    monkeypatch.setattr(route_module, "fetch_route", sin_red)
    resp = client.get("/route", params={"from_zone": 4, "to_zone": 4})
    assert resp.status_code == 404


def test_la_cache_commiteada_cubre_los_240_pares():
    """La que de verdad se sirve en la demo, no la aislada de los tests.

    Si esto falla, el mapa va a caer a linea recta en algun par durante la
    presentacion: correr `python scripts/warm_routes.py` con internet.
    """
    cache = RouteCache(route_module.CACHE_PATH)
    assert len(cache) == 240, "faltan pares en data/route_cache.json"
    assert "9-7" in cache


class TestSimplificacion:
    """Douglas-Peucker sobre la geometria, para que la cache sea commiteable.

    Sin esto la ruta mas larga del area ocupa 18 KB y los 240 pares no caben
    en un repo con dignidad.
    """

    def test_conserva_extremos(self):
        pts = [(25.0, -100.0), (25.1, -100.05), (25.2, -100.1), (25.3, -100.2)]
        salida = simplify(pts, 10.0)
        assert salida[0] == pts[0]
        assert salida[-1] == pts[-1]

    def test_colapsa_una_recta_a_sus_dos_extremos(self):
        recta = [(25.0 + i * 0.001, -100.0) for i in range(50)]
        assert len(simplify(recta, 10.0)) == 2

    def test_conserva_un_desvio_mas_grande_que_la_tolerancia(self):
        # ~0.005 grados de latitud son ~550 m: muy por encima de los 10 m.
        con_desvio = [(25.0, -100.0), (25.0, -100.005), (25.005, -100.005), (25.005, -100.01)]
        assert len(simplify(con_desvio, 10.0)) == 4

    def test_no_revienta_la_pila_con_una_ruta_larga(self):
        """Iterativo y no recursivo: 800 puntos casi colineales anidan mas
        hondo que el limite de recursion de Python."""
        larga = [(25.0 + i * 1e-5, -100.0 + (i % 2) * 1e-7) for i in range(3000)]
        assert len(simplify(larga, 10.0)) >= 2

    def test_dos_puntos_pasan_intactos(self):
        pts = [(25.0, -100.0), (25.1, -100.1)]
        assert simplify(pts, 10.0) == tuple(pts)
