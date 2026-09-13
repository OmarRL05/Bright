"""Contratos de datos compartidos entre bloques — v2.

Ver docs/01_Arquitectura.md - seccion 8 ("Contratos de datos compartidos").
Estas estructuras son la interfaz fija entre bloques: no se deben modificar
sin coordinar con el resto del equipo.

Cambios v2 (Abraham, P0.2 / P0.5):
- VehicleProfile: perfiles de vehiculo (moto/car/bike) con velocidad y
  capacidad de mochila diferenciadas.
- ZoneMap: adaptador zona↔coordenada; unica fuente de verdad de las zonas
  conocidas (antes estaban duplicadas en engine.py y demand.py).
- DistanceMatrix: cache de distancias precalculado (coordenada↔coordenada)
  para evitar recalculos repetidos en el loop de decision.
- CourierState gana campo `position` (coordenada actual del courier) y
  `vehicle` (perfil activo), necesarios para P0.1 (posicion real en el mapa
  y comparacion fin-de-turno vs ETA final).
- EventLogEntry: estructura de entrada del event log JSONL (P1.1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# ---------------------------------------------------------------------------
# Tipos de evento del log (P1.1)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """Ocho tipos de evento que el tick() emite al JSONL."""
    TICK = "tick"
    OFFER_RECEIVED = "offer_received"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_REJECTED = "offer_rejected"
    STOP_COMPLETED = "stop_completed"    # pickup o dropoff terminado
    ROAD_EVENT = "road_event"
    SHIFT_END = "shift_end"
    ROUTE_OPTIMIZED = "route_optimized"


# ---------------------------------------------------------------------------
# P0.5 — Perfiles de vehículo
# ---------------------------------------------------------------------------

class VehicleType(str, Enum):
    MOTO = "moto"
    CAR = "car"
    BIKE = "bike"


@dataclass(frozen=True)
class VehicleProfile:
    """Parámetros de un tipo de vehículo que afectan decisiones y tiempos.

    avg_speed_kmh:       velocidad promedio en zona urbana (Monterrey).
    max_backpack:        número máximo de pedidos simultáneos en mochila
                          (motor VRPTW de coordenadas, Bloque 3).
    cost_per_km:         costo operativo por km (combustible/desgaste), en
                          MXN. Usado por el motor de decisión para calcular
                          ganancia neta.
    weight_limit_kg:     límite de peso por pedido (contrato oficial,
                          constraint vehicle_capacity — ver
                          student-materials/courier/evaluation_protocol.md).
    volume_limit_liters: límite de volumen por pedido (idem).
    """
    type: VehicleType
    avg_speed_kmh: float
    max_backpack: int
    cost_per_km: float
    weight_limit_kg: float
    volume_limit_liters: float

    @property
    def min_per_km(self) -> float:
        """Minutos por km a la velocidad promedio del vehículo."""
        return 60.0 / self.avg_speed_kmh


# Perfiles predefinidos — valores calibrables (ver docs/02, sección 5).
VEHICLE_PROFILES: dict[VehicleType, VehicleProfile] = {
    VehicleType.MOTO: VehicleProfile(
        type=VehicleType.MOTO,
        avg_speed_kmh=25.0,
        max_backpack=3,
        cost_per_km=1.5,
        weight_limit_kg=15.0,
        volume_limit_liters=40.0,
    ),
    VehicleType.CAR: VehicleProfile(
        type=VehicleType.CAR,
        avg_speed_kmh=20.0,
        max_backpack=6,
        cost_per_km=3.0,
        weight_limit_kg=50.0,
        volume_limit_liters=150.0,
    ),
    VehicleType.BIKE: VehicleProfile(
        type=VehicleType.BIKE,
        avg_speed_kmh=15.0,
        max_backpack=2,
        cost_per_km=0.2,
        weight_limit_kg=8.0,
        volume_limit_liters=20.0,
    ),
}


# ---------------------------------------------------------------------------
# P0.2 — ZoneMap: adaptador zona↔coordenada (única fuente de verdad)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Zone:
    zone_id: int
    name: str
    coord: tuple[float, float]   # (lat, lon)
    demand_score: float          # [0, 1]; 1 = zona más caliente


class ZoneMap:
    """Catálogo de zonas conocidas de Monterrey.

    Fuente de verdad única: antes de este modelo, las coordenadas estaban
    duplicadas en engine.py y demand.py. Ahora ambos importan de aquí.

    `zone_id` es el mismo entero que el contrato oficial usa en
    `zone_pickup`/`zone_dropoff` (ver
    student-materials/courier/decision_response_schema.json) — es el puente
    entre lo que un juez manda por HTTP a POST /decide y las coordenadas que
    usa el motor VRPTW interno (Bloque 3/4/5).
    """

    _DEFAULT_ZONES: list[Zone] = [
        Zone(0, "Tec",       (25.651, -100.289), demand_score=0.7),
        Zone(1, "San Pedro", (25.657, -100.402), demand_score=0.5),
        Zone(2, "Centro",    (25.680, -100.310), demand_score=0.9),
        Zone(3, "Apodaca",   (25.780, -100.180), demand_score=0.3),
    ]

    def __init__(self, zones: list[Zone] | None = None) -> None:
        self._zones = zones if zones is not None else list(self._DEFAULT_ZONES)

    @property
    def zones(self) -> list[Zone]:
        return list(self._zones)

    @property
    def coords(self) -> list[tuple[float, float]]:
        return [z.coord for z in self._zones]

    def nearest_zone(self, coord: tuple[float, float]) -> Zone:
        """Zona más cercana a `coord` (distancia euclidiana en grados)."""
        return min(self._zones, key=lambda z: _sq_dist(z.coord, coord))

    def zone_by_name(self, name: str) -> Zone:
        for z in self._zones:
            if z.name == name:
                return z
        raise KeyError(f"Zona '{name}' no encontrada en ZoneMap")

    def by_id(self, zone_id: int) -> Zone:
        for z in self._zones:
            if z.zone_id == zone_id:
                return z
        raise KeyError(f"zone_id {zone_id} no encontrado en ZoneMap")


def _sq_dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


# Instancia global por defecto — se puede reemplazar en tests inyectando otra.
DEFAULT_ZONE_MAP = ZoneMap()


# ---------------------------------------------------------------------------
# P0.2 — DistanceMatrix: cache de distancias precalculado
# ---------------------------------------------------------------------------

class DistanceMatrix:
    """Matriz de distancias y tiempos de viaje entre un conjunto de coordenadas.

    Se precalcula una vez al inicio para evitar llamadas repetidas al proveedor
    de distancias durante el loop de decisión. El tamaño es O(n²) donde n es
    el número de zonas/paradas conocidas — manejable para n < 50.

    Uso típico:
        matrix = DistanceMatrix.build(zone_map.coords, distance_provider)
        t = matrix.travel_time(coord_a, coord_b)   # O(1) lookup
    """

    def __init__(
        self,
        coords: list[tuple[float, float]],
        time_matrix: list[list[float]],
        dist_matrix: list[list[float]],
    ) -> None:
        self._coords = coords
        self._time_matrix = time_matrix
        self._dist_matrix = dist_matrix
        self._index: dict[tuple[float, float], int] = {c: i for i, c in enumerate(coords)}

    @classmethod
    def build(cls, coords: list[tuple[float, float]], distance_provider) -> "DistanceMatrix":
        """Construye la matriz llamando al proveedor de distancias en todos los pares."""
        n = len(coords)
        time_matrix = [[0.0] * n for _ in range(n)]
        dist_matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                time_matrix[i][j] = distance_provider.travel_time(coords[i], coords[j])
                dist_matrix[i][j] = distance_provider.travel_distance(coords[i], coords[j])
        return cls(coords, time_matrix, dist_matrix)

    def _nearest_idx(self, coord: tuple[float, float]) -> int:
        """Índice de la coordenada más cercana en la matriz (snap to grid)."""
        if coord in self._index:
            return self._index[coord]
        return min(range(len(self._coords)), key=lambda i: _sq_dist(self._coords[i], coord))

    def travel_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Tiempo de viaje en minutos — O(1) lookup."""
        i = self._nearest_idx(origin)
        j = self._nearest_idx(destination)
        return self._time_matrix[i][j]

    def travel_distance(self, origin: tuple[float, float], destination: tuple[float, float]) -> float:
        """Distancia en km — O(1) lookup."""
        i = self._nearest_idx(origin)
        j = self._nearest_idx(destination)
        return self._dist_matrix[i][j]


# ---------------------------------------------------------------------------
# Contratos originales (sin cambios de interfaz, compatibles con todo el código)
# ---------------------------------------------------------------------------

@dataclass
class Offer:
    id: str
    pickup: tuple[float, float]
    dropoff: tuple[float, float]
    pay: float
    time_window: tuple[float, float]
    received_at: float
    # Senal de demanda historica de la zona del pickup, en [0, 1] (percentil
    # respecto al resto de zonas/horas). Bloque 1 la calcula a partir del
    # dataset de Kaggle (ver docs/02_Documentacion_Tecnica.md seccion 4);
    # Bloque 3 la usa en la evaluacion de umbral. Default neutral para no
    # romper a quien construya un Offer sin este dato todavia.
    demand_percentile: float = 0.5


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
    eta: float                      # minuto absoluto del turno


@dataclass
class CourierState:
    """Snapshot de solo lectura devuelto por CourierStateManager.snapshot().

    Campos nuevos v2:
    - position: coordenada actual del courier (lat, lon). None antes del
      primer tick o si no hay ruta activa.
    - vehicle: perfil de vehículo activo (velocidad, capacidad, costo/km).
    - sim_time: tiempo de simulación transcurrido en minutos.
    """

    version: int
    time_remaining: float
    earnings: float
    backpack: list[Offer] = field(default_factory=list)
    route: list[RouteStop] = field(default_factory=list)
    # --- v2 ---
    position: tuple[float, float] | None = None
    vehicle: VehicleProfile = field(
        default_factory=lambda: VEHICLE_PROFILES[VehicleType.MOTO]
    )
    sim_time: float = 0.0          # minutos transcurridos desde inicio del turno


# ---------------------------------------------------------------------------
# P1.1 — EventLogEntry: entrada del event log JSONL
# ---------------------------------------------------------------------------

@dataclass
class EventLogEntry:
    """Una línea del event log JSONL emitido por tick().

    Se serializa con dataclasses.asdict() + json.dumps() antes de escribir.
    Ocho tipos (ver EventType): tick, offer_received, offer_accepted,
    offer_rejected, stop_completed, road_event, shift_end, route_optimized.
    """
    event_type: EventType
    sim_time: float                         # minuto absoluto del turno
    payload: dict                           # datos específicos del evento
    agent_id: str = "ai"                    # "ai" o "baseline"
