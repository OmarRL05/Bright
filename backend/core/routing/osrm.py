"""Geometria de calle real para el mapa, via OSRM.

Por que OSRM y no el grafo local
--------------------------------
Hubo un intento anterior con `monterrey.graphml` (OSMnx + NetworkX, ver
`core/routing/graph.py`) y **daba rutas equivocadas con apariencia de
correctas**. La causa: `graph_from_place("Monterrey, Nuevo Leon, Mexico")`
descarga el MUNICIPIO, no el area metropolitana, y 9 de las 16 zonas del
simulador caen fuera de el -- Apodaca a 11.9 km del nodo mas cercano,
Santa Catarina a 6.1 km, Parque Industrial a 6.0 km, Escobedo a 4.9 km.
`osmnx.nearest_nodes` nunca falla: devuelve *algo*, asi que el mapa dibujaba
con toda confianza una ruta que terminaba a 5 km del destino real. Medido:
Contry -> Escobedo daba 20.94 km contra los 24.83 km reales.

Un grafo del area metropolitana completa lo arreglaria (~120 MB, no
commiteable). OSRM cubre el planeta, pesa cero en el repo y devuelve
tambien la distancia de conduccion, que el grafo local no daba calibrada.

Esto NO esta en el fast path
----------------------------
`/decide` jamas llama aqui. Las decisiones siguen midiendo con haversine por
`ROAD_DETOUR_FACTOR` (`core/routing/euclidean.py`): sin red, sin I/O y sin
reloj, que es lo que el presupuesto de 50 ms y el replay determinista exigen.
Esto es solo lo que el mapa DIBUJA.

La red se apaga en la demo
--------------------------
El protocolo (seccion 7) pide un ensayo con el modelo caido, y la forma
practica de hacerlo es apagar el wifi. Por eso la geometria se guarda en
disco (`data/route_cache.json`, ~350 KB con los 240 pares posibles) y ese
archivo SI se commitea: con la red apagada el mapa sigue trazando calle real
desde la cache. Solo un par que no este cacheado *y* sin red cae a linea
recta, y en ese caso el mapa lo dice con todas sus letras en vez de fingir.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------

#: Servidor publico de demostracion del proyecto OSRM. Sin llave y sin
#: registro. Su politica pide uso moderado, y por eso todo lo que se resuelve
#: se cachea en disco: una maquina ya calentada no vuelve a pedirle nada.
OSRM_ENDPOINT = "https://router.project-osrm.org/route/v1/driving/{coords}"

#: Medido contra 8 pares de zonas: 484 ms en caliente, hasta 5.1 s en frio.
#: Seis segundos cubre el caso frio sin dejar el mapa colgado indefinidamente
#: si el servidor no contesta.
OSRM_TIMEOUT_SECONDS = 6.0

#: Tolerancia de Douglas-Peucker, en metros. OSRM devuelve la geometria a
#: resolucion de nodo (782 puntos para la ruta mas larga del area) y eso son
#: 18 KB por ruta, demasiado para una cache commiteada. A 10 m la misma ruta
#: baja a 92 puntos y 2.2 KB, y 10 m es menos de un pixel en los niveles de
#: zoom en que el dashboard se usa (a zoom 11 un pixel son ~60 m), asi que la
#: linea dibujada es indistinguible de la completa.
#:
#: `overview=simplified` de OSRM haria algo parecido en el servidor, pero su
#: tolerancia no se puede pedir: daba 26 puntos, que a zoom 13 ya se ve
#: cortando esquinas.
SIMPLIFY_TOLERANCE_M = 10.0

#: Metros por grado. Suficiente para una tolerancia de 10 m en una ciudad de
#: 40 km de ancho; no es una proyeccion, es la escala local en la latitud de
#: Monterrey.
_METROS_POR_GRADO_LAT = 110540.0
_METROS_POR_GRADO_LON_ECUADOR = 111320.0

#: Cinco decimales son ~1.1 m. Redondear achica la cache a la mitad y la hace
#: legible en un diff.
_DECIMALES = 5

Coord = tuple[float, float]
"""(lat, lon), el mismo orden que usan las zonas y Leaflet."""


@dataclass(frozen=True)
class Ruta:
    """Una ruta resuelta por OSRM."""

    #: Geometria (lat, lon) ya simplificada, del origen al destino.
    coords: tuple[Coord, ...]
    #: Distancia de conduccion en km. Es la cifra REAL por calle, util para
    #: contrastar `ROAD_DETOUR_FACTOR` -- no para sustituirlo: las decisiones
    #: no pueden depender de la red.
    distance_km: float
    #: Duracion estimada por OSRM, en minutos.
    duration_min: float

    def to_dict(self) -> dict:
        return {
            "coords": [list(c) for c in self.coords],
            "distance_km": round(self.distance_km, 3),
            "duration_min": round(self.duration_min, 2),
        }

    @classmethod
    def from_dict(cls, crudo: dict) -> "Ruta":
        return cls(
            coords=tuple((float(c[0]), float(c[1])) for c in crudo["coords"]),
            distance_km=float(crudo["distance_km"]),
            duration_min=float(crudo["duration_min"]),
        )


# ---------------------------------------------------------------------------
# Simplificacion
# ---------------------------------------------------------------------------


def simplify(coords: Sequence[Coord], tolerancia_m: float = SIMPLIFY_TOLERANCE_M) -> tuple[Coord, ...]:
    """Douglas-Peucker sobre una polilinea geografica.

    Iterativo y no recursivo a proposito: una ruta de 800 puntos casi
    degenerada puede anidar mas hondo que el limite de recursion de Python, y
    reventar ahi seria un fallo absurdo para un adorno del mapa.
    """
    if len(coords) < 3:
        return tuple(coords)

    # Escala local: a esta latitud un grado de longitud mide menos que uno de
    # latitud, y sin corregirlo la tolerancia seria anisotropa.
    escala_lon = _METROS_POR_GRADO_LON_ECUADOR * math.cos(math.radians(coords[0][0]))

    def distancia_al_segmento(p: Coord, a: Coord, b: Coord) -> float:
        ax, ay = a[1] * escala_lon, a[0] * _METROS_POR_GRADO_LAT
        bx, by = b[1] * escala_lon, b[0] * _METROS_POR_GRADO_LAT
        px, py = p[1] * escala_lon, p[0] * _METROS_POR_GRADO_LAT
        dx, dy = bx - ax, by - ay
        largo2 = dx * dx + dy * dy
        if largo2 == 0.0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / largo2))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    conservar = [False] * len(coords)
    conservar[0] = conservar[-1] = True
    pila = [(0, len(coords) - 1)]
    while pila:
        i, j = pila.pop()
        if j - i < 2:
            continue
        peor, indice = 0.0, i
        for k in range(i + 1, j):
            d = distancia_al_segmento(coords[k], coords[i], coords[j])
            if d > peor:
                peor, indice = d, k
        if peor > tolerancia_m:
            conservar[indice] = True
            pila.append((i, indice))
            pila.append((indice, j))

    return tuple(c for c, k in zip(coords, conservar) if k)


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------


def fetch_route(
    puntos: Sequence[Coord],
    *,
    timeout: float = OSRM_TIMEOUT_SECONDS,
    tolerancia_m: float = SIMPLIFY_TOLERANCE_M,
) -> Ruta | None:
    """Pide a OSRM la ruta por calles que pasa por `puntos`, en orden.

    Devuelve None ante cualquier fallo -- sin red, timeout, servidor caido,
    respuesta rara, o un par sin camino. Quien llama dibuja linea recta y lo
    rotula. Nunca levanta: esto alimenta un mapa, no una decision, y tumbar la
    peticion entera por un adorno seria peor que el adorno ausente.

    Acepta mas de dos puntos: OSRM resuelve la polilinea completa de un tiron
    (origen -> pickup -> dropoff), que es una peticion en vez de dos y ademas
    respeta los giros permitidos en el punto intermedio.
    """
    if len(puntos) < 2:
        return None

    # OSRM habla lon,lat -- al reves que todo lo demas en este proyecto. Es la
    # inversion mas facil de pasar por alto de toda la integracion.
    trazo = ";".join(f"{lon},{lat}" for lat, lon in puntos)
    url = OSRM_ENDPOINT.format(coords=trazo) + "?overview=full&geometries=geojson"

    try:
        with urllib.request.urlopen(url, timeout=timeout) as respuesta:
            cuerpo = json.load(respuesta)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    rutas = cuerpo.get("routes") or []
    if not rutas:
        return None

    try:
        geometria = rutas[0]["geometry"]["coordinates"]
        metros = float(rutas[0]["distance"])
        segundos = float(rutas[0]["duration"])
    except (KeyError, TypeError, ValueError):
        return None

    if len(geometria) < 2:
        return None

    coords = [
        (round(float(par[1]), _DECIMALES), round(float(par[0]), _DECIMALES))
        for par in geometria
    ]
    return Ruta(
        coords=simplify(coords, tolerancia_m),
        distance_km=metros / 1000.0,
        duration_min=segundos / 60.0,
    )


# ---------------------------------------------------------------------------
# Cache en disco
# ---------------------------------------------------------------------------


class RouteCache:
    """Rutas resueltas, guardadas en JSON al lado del codigo.

    Se commitea (ver la cabecera del modulo): es lo que hace que el mapa
    siga trazando calle real durante el ensayo con la red apagada.

    Carga perezosa y una sola vez por proceso. Un archivo ausente o corrupto
    se trata como cache vacia, no como error: lo peor que puede pasar es que
    haya que volver a pedirle a OSRM.
    """

    def __init__(self, ruta_archivo: Path) -> None:
        self._archivo = ruta_archivo
        self._entradas: dict[str, Ruta] | None = None

    def _cargar(self) -> dict[str, Ruta]:
        if self._entradas is not None:
            return self._entradas
        entradas: dict[str, Ruta] = {}
        if self._archivo.exists():
            try:
                crudo = json.loads(self._archivo.read_text())
                for clave, valor in crudo.items():
                    entradas[clave] = Ruta.from_dict(valor)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
                entradas = {}
        self._entradas = entradas
        return entradas

    def get(self, clave: str) -> Ruta | None:
        return self._cargar().get(clave)

    def put(self, clave: str, ruta: Ruta) -> None:
        self._cargar()[clave] = ruta

    def __contains__(self, clave: str) -> bool:
        return clave in self._cargar()

    def __len__(self) -> int:
        return len(self._cargar())

    def save(self) -> None:
        """Escribe la cache ordenada por clave.

        Ordenada para que el diff de git sea legible cuando alguien vuelva a
        calentarla: sin esto, un recalentado reordenaria el archivo entero y
        el diff no diria que ruta cambio de verdad.
        """
        entradas = self._cargar()
        self._archivo.parent.mkdir(parents=True, exist_ok=True)
        serializado = {clave: entradas[clave].to_dict() for clave in sorted(entradas)}
        self._archivo.write_text(json.dumps(serializado, indent=0, sort_keys=True) + "\n")
