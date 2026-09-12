"""Contratos de datos compartidos entre bloques.

Ver docs/01_Arquitectura.md - seccion 8 ("Contratos de datos compartidos").
Estas estructuras son la interfaz fija entre bloques: no se deben modificar
sin coordinar con el resto del equipo.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Offer:
    id: str
    pickup: tuple[float, float]
    dropoff: tuple[float, float]
    pay: float
    time_window: tuple[float, float]
    received_at: float


@dataclass
class RoadEvent:
    type: Literal["closure", "traffic", "surge"]
    location: tuple[float, float] | list[tuple[float, float]]
    multiplier: float | None
    timestamp: float


@dataclass
class RouteStop:
    offer_id: str
    kind: Literal["pickup", "dropoff"]
    eta: float


@dataclass
class CourierState:
    """Snapshot de solo lectura devuelto por CourierStateManager.snapshot()."""

    version: int
    time_remaining: float
    earnings: float
    backpack: list[Offer] = field(default_factory=list)
    route: list[RouteStop] = field(default_factory=list)
