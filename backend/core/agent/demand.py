"""Señal de demanda historica por zona.

Ahora usa ZoneMap de core.models como única fuente de verdad de zonas y
demand_scores (P0.2). Antes las coordenadas estaban duplicadas aquí y en
engine.py — ya no es el caso.
"""

from typing import Protocol

from core.models import DEFAULT_ZONE_MAP, ZoneMap


class DemandSignal(Protocol):
    def zone_score(self, location: tuple[float, float]) -> float:
        """Score de demanda historica de la zona a la que pertenece `location`.

        Rango 0.0 (zona fria) - 1.0 (zona caliente).
        """
        ...


class StaticDemandSignal:
    """DemandSignal estática: score del ZoneMap para la zona más cercana."""

    def __init__(self, zone_map: ZoneMap | None = None) -> None:
        self._zone_map = zone_map if zone_map is not None else DEFAULT_ZONE_MAP

    def zone_score(self, location: tuple[float, float]) -> float:
        zone = self._zone_map.nearest_zone(location)
        return zone.demand_score
