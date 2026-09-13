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

Corregido tras el merge a B5 (ver docs/03_Integracion_API_Decide.md): los 8
EventType ahora son EXACTAMENTE los que exige
student-materials/courier/event_log_schema.json (antes eran un vocabulario
interno distinto -- tick/offer_received/... -- que hacia fallar
validate_format.py --event-log linea por linea).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# ---------------------------------------------------------------------------
# Tipos de evento del log (P1.1) -- deben coincidir 1:1 con
# student-materials/courier/event_log_schema.json (REQUIRED_BY_EVENT en su
# validate_format.py). No renombrar sin actualizar tambien ese contrato.
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """Los 8 tipos de evento del contrato oficial."""
    SHIFT_START = "shift_start"
    ORDER_OFFERED = "order_offered"
    DECISION = "decision"
    POSITION_UPDATE = "position_update"
    EARNINGS_UPDATE = "earnings_update"
    SHOCK = "shock"
    STRATEGY_UPDATE = "strategy_update"
    SHIFT_END = "shift_end"


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

    16 zonas (0-15), no 4: los ejemplos ilustrativos del material oficial
    usan zone_pickup/zone_dropoff hasta 11 (ver
    decision_response_schema.json, event_log_schema.json). Con solo 4 zonas
    (0-3), cualquier id oficial >=4 caia fuera de nuestro propio universo de
    zonas -- no rompia nada (`by_id_or_none` existe para eso, ver abajo),
    pero reducia el area donde `flagged_zone_night` podia dispararse a una
    sola zona conocida.
    """

    _DEFAULT_ZONES: list[Zone] = [
        Zone(0,  "Tec",               (25.651, -100.289), demand_score=0.70),
        Zone(1,  "San Pedro",         (25.657, -100.402), demand_score=0.50),
        Zone(2,  "Centro",            (25.680, -100.310), demand_score=0.90),
        Zone(3,  "Apodaca",           (25.780, -100.180), demand_score=0.30),
        Zone(4,  "Guadalupe",         (25.677, -100.256), demand_score=0.60),
        Zone(5,  "San Nicolas",       (25.750, -100.281), demand_score=0.55),
        Zone(6,  "Santa Catarina",    (25.673, -100.458), demand_score=0.40),
        Zone(7,  "Escobedo",          (25.796, -100.318), demand_score=0.35),
        Zone(8,  "Cumbres",           (25.716, -100.371), demand_score=0.45),
        Zone(9,  "Contry",            (25.630, -100.267), demand_score=0.50),
        Zone(10, "Del Valle",         (25.649, -100.357), demand_score=0.65),
        Zone(11, "Parque Industrial", (25.740, -100.220), demand_score=0.25),
        Zone(12, "Mitras",            (25.681, -100.345), demand_score=0.50),
        Zone(13, "Obispado",          (25.674, -100.336), demand_score=0.55),
        Zone(14, "Valle Oriente",     (25.646, -100.360), demand_score=0.70),
        Zone(15, "Linda Vista",       (25.712, -100.253), demand_score=0.40),
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

    def by_id_or_none(self, zone_id: int) -> Zone | None:
        """Como `by_id`, pero None en vez de excepcion.

        Un judge puede mandar cualquier entero de zona a POST /decide —
        nuestro universo de zonas es nuestro (el reto dice "you build your
        own data"), pero no podemos asumir que siempre coincide. Quien
        consuma esto decide el fallback (demanda neutral, `zone_known:
        false` en el log, etc.) en vez de que ZoneMap decida por todos con
        una excepcion.
        """
        for z in self._zones:
            if z.zone_id == zone_id:
                return z
        return None


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
    """Una oferta del stream. Habla los dos idiomas del proyecto a la vez.

    Los primeros campos son los del motor VRPTW interno (coordenadas y
    minutos relativos al turno); los de la seccion v3 son los que el contrato
    oficial exige en `order_offered` (zonas enteras, peso, volumen, propina,
    surge). Tener las dos vistas en la MISMA oferta es lo que permite que el
    arnes de evaluacion y el endpoint /decide midan el mismo stream en vez de
    dos streams parecidos -- que es exactamente la divergencia que ya costo
    dos reconciliaciones en este repo.

    Todos los campos nuevos tienen default, asi que construir un Offer "a la
    v1" sigue funcionando igual.
    """

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
    surge_multiplier: float = 1.0

    # --- v3: campos del contrato oficial (student-materials/courier) -------
    #: Zonas enteras, tal como las identifica el material oficial. El
    #: generador ya las conoce al elegir el pickup/dropoff; guardarlas evita
    #: re-derivarlas con nearest_zone() y que dos consumidores lleguen a
    #: respuestas distintas sobre la misma oferta.
    zone_pickup: int | None = None
    zone_dropoff: int | None = None
    #: Requeridos para la constraint `vehicle_capacity`. Sin ellos en el
    #: stream, esa constraint no puede dispararse nunca en un turno completo y
    #: `safety_violations` del CSV de resultados no mide nada.
    weight_kg: float = 0.0
    volume_liters: float = 0.0
    #: Requeridos para que la economia del turno sea la misma que la del
    #: endpoint: el surge multiplica la tarifa y la propina se suma aparte.
    est_tip_mxn: float = 0.0
    surge_multiplier: float = 1.0
    restaurant_prep_min: float = 0.0
    platform: str | None = None


@dataclass
class RoadEvent:
    type: Literal["closure", "traffic", "surge"]
    location: tuple[float, float] | list[tuple[float, float]]
    multiplier: float | None
    timestamp: float
    duration_min: float = 30.0


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
    """Una línea del event log JSONL, en construcción (ver SimulationEngine._log).

    Al serializar se APLANA: la línea final es
    `{"event": event_type.value, "sim_time": ..., **payload, "agent_id": ...}`
    -- no un objeto anidado -- porque asi lo exige
    student-materials/courier/event_log_schema.json (campos al nivel raiz,
    p.ej. `order_offered.zone_pickup`, no `order_offered.payload.zone_pickup`).

    Ocho tipos (ver EventType): shift_start, order_offered, decision,
    position_update, earnings_update, shock, strategy_update, shift_end.
    """
    event_type: EventType
    sim_time: str                           # ISO 8601, no minuto float
    payload: dict                           # campos especificos del evento, se aplanan al nivel raiz
    agent_id: str = "ai"                    # "ai" o "baseline" -- campo extra, no forma parte del contrato oficial pero no lo rompe
