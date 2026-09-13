"""Ruta real sobre calles para el mapa del dashboard (Bloque 5/6).

El mapa dibujaba una linea recta zona->zona porque nada conectaba el grafo
vial real (Bloque 5, `core/routing/graph.py`) con el frontend -- el grafo
ya existe y ya lo consume el motor VRPTW de coordenadas (Bloque 3/4), pero
nadie lo exponia por HTTP para visualizacion.

No es parte del fast path: `/decide` nunca llama esto, asi que cargar el
`.graphml` (~2s la primera vez, ver benchmark en el commit) no cuenta contra
el presupuesto de 50ms de la ventana de decision.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from core.models import DEFAULT_ZONE_MAP
from core.routing.graph import RoadNetwork

router = APIRouter(tags=["route"])

#: No se commitea a git (pesa ~40MB, ver backend/data/README.md) -- en una
#: maquina nueva puede no existir todavia. El endpoint lo dice con un 503
#: legible en vez de tronar con un traceback de OSMnx.
GRAPHML_PATH = Path(__file__).resolve().parents[1] / "data" / "monterrey.graphml"


@lru_cache(maxsize=1)
def _network() -> RoadNetwork | None:
    """Carga el grafo UNA sola vez por proceso. None si el .graphml no esta."""
    if not GRAPHML_PATH.exists():
        return None
    return RoadNetwork(graphml_path=str(GRAPHML_PATH))


@lru_cache(maxsize=256)
def _cached_path(zone_from: int, zone_to: int) -> tuple[tuple[float, float], ...] | None:
    """Cache por par de zonas: acotado (16 x 16 = 256 combinaciones como
    maximo hoy) y valido mientras nadie dispare un cierre vial sobre este
    grafo -- nada lo hace todavia, asi que cachear indefinido por proceso
    es un trade-off razonable, no un descuido."""
    network = _network()
    if network is None:
        return None
    origin = DEFAULT_ZONE_MAP.by_id(zone_from).coord
    destination = DEFAULT_ZONE_MAP.by_id(zone_to).coord
    coords = network.shortest_path_coords(origin, destination)
    return tuple(coords) if coords else None


@router.get("/route")
async def route(from_zone: int, to_zone: int) -> dict:
    """Geometria real (lat/lon por nodo) del camino mas corto entre dos zonas.

    El frontend cae a una linea recta si esto responde 404/503 -- ver
    src/components/Map.tsx -- asi que un juez sin `monterrey.graphml`
    descargado sigue viendo un mapa funcional, solo menos preciso.
    """
    if _network() is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "grafo vial no disponible: falta backend/data/monterrey.graphml "
                "(no se commitea a git, ver backend/data/README.md para generarlo)."
            ),
        )
    if DEFAULT_ZONE_MAP.by_id_or_none(from_zone) is None or DEFAULT_ZONE_MAP.by_id_or_none(to_zone) is None:
        raise HTTPException(status_code=404, detail="zona desconocida")

    coords = _cached_path(from_zone, to_zone)
    if coords is None:
        raise HTTPException(status_code=404, detail="sin camino entre esas zonas (todo bloqueado)")
    return {"from_zone": from_zone, "to_zone": to_zone, "coords": [list(c) for c in coords]}
