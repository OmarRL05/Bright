"""Catalogo de zonas para el mapa del dashboard (Bloque 6, P2.3).

El frontend necesita resolver `zone_pickup`/`zone_dropoff` (enteros) a una
coordenada para poder dibujar algo -- `core.models.DEFAULT_ZONE_MAP` es la
unica fuente de verdad de eso (ver P0.2), asi que este endpoint solo la
expone, no la duplica. `flagged` sale de `core.agent.safety.FLAGGED_ZONES`
por la misma razon: es la constraint real que usa `/decide`, no un color
elegido a mano en el frontend.
"""

from __future__ import annotations

from fastapi import APIRouter

from core.agent.safety import FLAGGED_ZONES
from core.models import DEFAULT_ZONE_MAP

router = APIRouter(tags=["zones"])


@router.get("/zones")
async def zones() -> dict:
    return {
        "zones": [
            {
                "zone_id": zone.zone_id,
                "name": zone.name,
                "coord": list(zone.coord),
                "demand_score": zone.demand_score,
                "flagged": zone.zone_id in FLAGGED_ZONES,
            }
            for zone in DEFAULT_ZONE_MAP.zones
        ]
    }
