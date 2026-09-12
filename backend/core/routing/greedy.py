"""Heuristica de insercion mas barata (cheapest insertion), O(n) por oferta.

Usada por Bloque 3 para decidir "en segundos" sin resolver el VRP completo.
Ver docs/02_Documentacion_Tecnica.md seccion 1.

TODO(equipo): implementar el calculo de costo marginal.
"""

from dataclasses import dataclass

from core.models import Offer, RouteStop


@dataclass
class InsertionResult:
    extra_distance: float
    extra_time: float
    new_route: list[RouteStop]


def cheapest_insertion(
    current_route: list[RouteStop],
    offer: Offer,
    frozen_index: int,
) -> InsertionResult:
    """Calcula la posicion mas barata para insertar `offer` en `current_route`.

    `frozen_index` marca hasta donde el tramo esta comprometido (frozen
    horizon, ver docs/01_Arquitectura.md seccion 6): no se debe insertar
    antes de ese indice salvo que el costo marginal sea ~0.

    TODO(equipo): implementar usando core.routing.graph.RoadNetwork.eta_min /
    .distance_km entre paradas (bajo demanda, no matriz precalculada -- esa
    es solo para el optimizador global de Bloque 4).
    """
    raise NotImplementedError
