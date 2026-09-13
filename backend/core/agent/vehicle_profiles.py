"""Perfiles de vehiculo: velocidad y limites de carga por tipo.

TODO(Abraham, P0.5 en el roadmap de la auditoria del 12 sep): estos valores
son un placeholder para poder levantar POST /decide (P0.4) sin esperar a que
el reparto de tareas real llegue a este bloque. La firma es estable
(VehicleProfile, VEHICLE_PROFILES[vehicle]) -- los numeros se recalibran aqui
sin tocar a quien los consume (core.agent.safety, api.decide).
"""

from dataclasses import dataclass
from typing import Literal

Vehicle = Literal["moto", "car", "bike"]


@dataclass(frozen=True)
class VehicleProfile:
    speed_kmh: float
    weight_limit_kg: float
    volume_limit_liters: float


VEHICLE_PROFILES: dict[Vehicle, VehicleProfile] = {
    "moto": VehicleProfile(speed_kmh=28.0, weight_limit_kg=15.0, volume_limit_liters=40.0),
    "bike": VehicleProfile(speed_kmh=15.0, weight_limit_kg=8.0, volume_limit_liters=20.0),
    "car": VehicleProfile(speed_kmh=25.0, weight_limit_kg=50.0, volume_limit_liters=150.0),
}
