"""Ruta real sobre calles para el mapa del dashboard (Bloque 5/6).

El mapa dibujaba una linea recta zona->zona. El primer intento de arreglarlo
fue el grafo local de OSMnx, y resulto peor que la linea recta: trazaba con
confianza rutas que terminaban a kilometros del destino, porque el grafo
descargado cubre el municipio de Monterrey y 9 de las 16 zonas del simulador
estan fuera de el. Ver `core/routing/osrm.py` para la medicion completa.

Ahora la geometria viene de OSRM, que cubre el area metropolitana entera, y
la respuesta dice SIEMPRE de donde salio (`source`). Ese campo existe para
que el mapa pueda rotular la diferencia en vez de dejar que se confunda una
aproximacion con una medicion -- que es exactamente el fallo del intento
anterior.

No es parte del fast path: `/decide` nunca llama aqui, asi que ni la red ni
la cache cuentan contra el presupuesto de 50 ms de la ventana de decision.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from core.models import DEFAULT_ZONE_MAP
from core.routing.osrm import RouteCache, Ruta, fetch_route

router = APIRouter(tags=["route"])

#: Este archivo SI se commitea (a diferencia del .graphml de 38 MB que este
#: endpoint usaba antes): pesa ~350 KB con los 240 pares posibles y es lo que
#: permite que el mapa trace calle real con la red apagada, que es el ensayo
#: de la seccion 7 del protocolo. Se rellena con `scripts/warm_routes.py`.
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "route_cache.json"

_cache = RouteCache(CACHE_PATH)


def clave_de(zone_from: int, zone_to: int) -> str:
    return f"{zone_from}-{zone_to}"


def ruta_entre_zonas(zone_from: int, zone_to: int, *, permitir_red: bool = True) -> tuple[Ruta | None, str]:
    """Geometria entre dos zonas, y de donde salio.

    El orden importa: cache primero, red despues. Una maquina calentada nunca
    le pide nada a OSRM, que es lo que hace el mapa instantaneo en la demo y
    lo que respeta la politica de uso del servidor publico.

    Lo que se resuelve por red se guarda en memoria pero NO se escribe a
    disco aqui: un endpoint HTTP escribiendo en un archivo del repo en cada
    peticion es una carrera esperando a ocurrir. A disco lo baja el script de
    calentado, que corre una vez y a proposito.
    """
    clave = clave_de(zone_from, zone_to)
    cacheada = _cache.get(clave)
    if cacheada is not None:
        return cacheada, "cache"

    if not permitir_red:
        return None, "ninguna"

    origen = DEFAULT_ZONE_MAP.by_id(zone_from).coord
    destino = DEFAULT_ZONE_MAP.by_id(zone_to).coord
    viva = fetch_route([origen, destino])
    if viva is None:
        return None, "ninguna"

    _cache.put(clave, viva)
    return viva, "osrm"


@router.get("/route")
async def route(from_zone: int, to_zone: int) -> dict:
    """Geometria real (lat/lon) del camino por calles entre dos zonas.

    `source` dice de donde salio: `cache` (disco, funciona sin red) u `osrm`
    (recien pedido). Si no hay ninguna de las dos responde 503 y el frontend
    dibuja linea recta ROTULADA como tal -- ver src/components/Map.tsx. Un
    juez sin internet sigue viendo un mapa que no miente.
    """
    if (
        DEFAULT_ZONE_MAP.by_id_or_none(from_zone) is None
        or DEFAULT_ZONE_MAP.by_id_or_none(to_zone) is None
    ):
        raise HTTPException(status_code=404, detail="zona desconocida")

    if from_zone == to_zone:
        # Mismo origen y destino: no hay nada que trazar, y OSRM devolveria
        # una ruta de longitud cero que el mapa dibujaria como un punto.
        raise HTTPException(status_code=404, detail="origen y destino son la misma zona")

    ruta, fuente = ruta_entre_zonas(from_zone, to_zone)
    if ruta is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "sin geometria vial: el par no esta en data/route_cache.json y "
                "OSRM no respondio (¿sin red?). Correr scripts/warm_routes.py "
                "con internet para dejar la cache lista."
            ),
        )

    return {
        "from_zone": from_zone,
        "to_zone": to_zone,
        "source": fuente,
        **ruta.to_dict(),
    }
