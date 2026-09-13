"""Contratos de datos del protocolo oficial (student-materials/courier/).

Espejo 1:1 de `decision_response_schema.json` y del evento `order_offered` de
`event_log_schema.json`. Este archivo es la frontera entre el mundo HTTP
(Persona 3) y el motor de decision (Bloque 3): todo lo que entra por /decide
se convierte aqui en dataclasses inmutables, y a partir de ese punto nadie
vuelve a tocar un dict crudo.

Por que vive en core/agent/ y no en core/models.py
---------------------------------------------------
`core/models.py` es el contrato interno del simulador (Offer/RouteStop en
coordenadas y minutos relativos). El contrato oficial es otra cosa: zonas
enteras, tiempo absoluto ISO 8601 y perfiles de vehiculo. Mezclarlos en el
mismo archivo obliga a que Bloque 3 espere a que Bloque 1 termine su
migracion. Se mantienen separados y se traducen en `adapters.py`.

Invariantes que este modulo garantiza
-------------------------------------
1. **Sin reloj de pared.** Ninguna funcion aqui llama a `datetime.now()` ni
   a `time.time()`. Todo el tiempo entra por `sim_time` / los overrides. Es
   lo que hace que el replay del protocolo (seccion 6) sea byte-identico.
2. **Tolerante a campos extra.** Los jueces pueden mandar campos que no
   conocemos; `from_payload` los ignora en vez de fallar. Un 422 de pydantic
   ante un campo desconocido seria un fallo duro de Feasibility.
3. **Tolerante a campos opcionales ausentes.** El PROBE de
   `validate_format.py --endpoint` no manda `courier_state_overrides`, asi
   que todo el estado del repartidor tiene default seguro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# --------------------------------------------------------------------------
# Vocabulario fijo del protocolo. Estos literales NO se inventan: salen de
# decision_response_schema.json y los valida validate_format.py.
# --------------------------------------------------------------------------

Vehicle = Literal["moto", "car", "bike"]

Decision = Literal["ACCEPT", "SKIP"]

Tier = Literal["tier1", "tier2"]

#: Ids de constraint que el validador oficial acepta. `None` significa "la
#: decision salio de criterios de pago, no de una constraint dura".
BindingConstraint = Literal[
    "flagged_zone_night",
    "mandatory_break",
    "heat_rule",
    "shift_end_infeasible",
    "vehicle_capacity",
    "reservation_wage",
]


# --------------------------------------------------------------------------
# Perfiles de vehiculo (requisito 5 del protocolo: "Three vehicle types ...
# with distinct speed profiles and distinct weight and volume limits").
#
# Los tres perfiles deben ser DISTINTOS en las tres dimensiones -- los jueces
# lo revisan explicitamente. Son constantes nombradas y calibrables, no
# numeros magicos enterrados en la logica.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VehicleProfile:
    name: str
    speed_kmh: float
    max_weight_kg: float
    max_volume_liters: float


VEHICLE_PROFILES: dict[str, VehicleProfile] = {
    # moto: la mas rapida en ciudad (se filtra en el trafico), mochila media.
    "moto": VehicleProfile(name="moto", speed_kmh=28.0, max_weight_kg=12.0, max_volume_liters=45.0),
    # car: mas lento que la moto en zona urbana (trafico + estacionarse),
    # pero es el unico que mueve pedidos grandes.
    "car": VehicleProfile(name="car", speed_kmh=22.0, max_weight_kg=40.0, max_volume_liters=200.0),
    # bike: el mas lento y el mas limitado; tambien el que mas sufre las
    # constraints de calor y de descanso continuo.
    "bike": VehicleProfile(name="bike", speed_kmh=14.0, max_weight_kg=6.0, max_volume_liters=25.0),
}

#: Perfil usado cuando el payload no trae `vehicle` (no deberia pasar: el
#: campo es requerido en order_offered). Se elige el mas restrictivo para que
#: un dato faltante nunca AFLOJE una constraint de capacidad.
DEFAULT_VEHICLE: Vehicle = "bike"


def profile_for(vehicle: str | None) -> VehicleProfile:
    """Perfil del vehiculo, cayendo al mas restrictivo si es desconocido.

    Nunca lanza: un vehiculo no reconocido en pleno /decide no puede tumbar
    el endpoint (seria un fallo duro de Feasibility), pero tampoco puede
    relajar limites -- por eso el fallback es `bike`.
    """
    return VEHICLE_PROFILES.get(vehicle or "", VEHICLE_PROFILES[DEFAULT_VEHICLE])


# --------------------------------------------------------------------------
# Utilidades de parseo. Toleran basura sin lanzar: el endpoint debe responder
# SKIP razonado antes que 500.
# --------------------------------------------------------------------------


def parse_sim_time(value: Any) -> datetime | None:
    """ISO 8601 -> datetime naive. Devuelve None si no se puede interpretar.

    Se normaliza a naive (sin tzinfo) a proposito: el protocolo usa
    `"2026-03-21T18:42:00"` sin zona, y mezclar aware/naive en las
    comparaciones de las constraints de horario lanzaria TypeError dentro de
    la ventana de decision.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# La oferta que llega por POST /decide (== evento order_offered).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderRequest:
    order_id: str
    sim_time: datetime | None
    zone_pickup: int | None
    zone_dropoff: int | None
    distance_pickup_km: float
    distance_delivery_km: float
    base_pay_mxn: float
    surge_multiplier: float
    vehicle: str
    # Opcionales del schema. weight/volume por defecto 0.0: ausentes no deben
    # inventar una violacion de capacidad (ver doc-scripts/safety.md, seccion
    # "datos faltantes"). El journal registra que se asumieron.
    weight_kg: float = 0.0
    volume_liters: float = 0.0
    est_tip_mxn: float = 0.0
    restaurant_prep_min: float = 0.0
    estimated_pickup_min: float | None = None
    estimated_delivery_min: float | None = None
    platform: str | None = None
    decision_deadline: datetime | None = None
    zone_pickup_name: str | None = None
    zone_dropoff_name: str | None = None
    #: Campos del payload que no reconocemos. Se conservan para el journal
    #: (responder "que cambio en el input" en 10 s) pero nunca se usan para
    #: decidir.
    extras: dict[str, Any] = field(default_factory=dict, compare=False)

    _KNOWN = frozenset(
        {
            "event", "order_id", "sim_time", "zone_pickup", "zone_dropoff",
            "distance_pickup_km", "distance_delivery_km", "base_pay_mxn",
            "surge_multiplier", "vehicle", "weight_kg", "volume_liters",
            "est_tip_mxn", "restaurant_prep_min", "estimated_pickup_min",
            "estimated_delivery_min", "platform", "decision_deadline",
            "zone_pickup_name", "zone_dropoff_name", "courier_state_overrides",
        }
    )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OrderRequest:
        """Construye desde el dict crudo del POST. No lanza nunca."""
        payload = payload or {}
        return cls(
            order_id=str(payload.get("order_id", "")),
            sim_time=parse_sim_time(payload.get("sim_time")),
            zone_pickup=_as_int(payload.get("zone_pickup")),
            zone_dropoff=_as_int(payload.get("zone_dropoff")),
            distance_pickup_km=_as_float(payload.get("distance_pickup_km")),
            distance_delivery_km=_as_float(payload.get("distance_delivery_km")),
            base_pay_mxn=_as_float(payload.get("base_pay_mxn")),
            surge_multiplier=_as_float(payload.get("surge_multiplier"), 1.0),
            vehicle=str(payload.get("vehicle") or DEFAULT_VEHICLE),
            weight_kg=_as_float(payload.get("weight_kg")),
            volume_liters=_as_float(payload.get("volume_liters")),
            est_tip_mxn=_as_float(payload.get("est_tip_mxn")),
            restaurant_prep_min=_as_float(payload.get("restaurant_prep_min")),
            estimated_pickup_min=(
                _as_float(payload["estimated_pickup_min"])
                if payload.get("estimated_pickup_min") is not None
                else None
            ),
            estimated_delivery_min=(
                _as_float(payload["estimated_delivery_min"])
                if payload.get("estimated_delivery_min") is not None
                else None
            ),
            platform=payload.get("platform"),
            decision_deadline=parse_sim_time(payload.get("decision_deadline")),
            zone_pickup_name=payload.get("zone_pickup_name"),
            zone_dropoff_name=payload.get("zone_dropoff_name"),
            extras={k: v for k, v in payload.items() if k not in cls._KNOWN},
        )

    @property
    def profile(self) -> VehicleProfile:
        return profile_for(self.vehicle)

    @property
    def gross_pay_mxn(self) -> float:
        """Pago bruto con surge y propina estimada aplicados."""
        return (self.base_pay_mxn * self.surge_multiplier) + self.est_tip_mxn

    @property
    def total_distance_km(self) -> float:
        """Deadhead al pickup + tramo de entrega."""
        return self.distance_pickup_km + self.distance_delivery_km


# --------------------------------------------------------------------------
# Estado del repartidor en el momento del ping.
#
# El protocolo (seccion 2) es explicito: "Some requests carry
# courier_state_overrides ... Your system must apply these rather than
# ignoring them". Ignorarlos es una falla directa en las categorias de
# Continuous-riding safeguards y End-of-shift feasibility.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InFlightOrder:
    """Pedido ya aceptado y todavia no entregado."""

    order_id: str
    weight_kg: float = 0.0
    volume_liters: float = 0.0
    zone_dropoff: int | None = None
    eta_dropoff: datetime | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> InFlightOrder:
        if isinstance(payload, str):
            return cls(order_id=payload)
        payload = payload or {}
        return cls(
            order_id=str(payload.get("order_id", "")),
            weight_kg=_as_float(payload.get("weight_kg")),
            volume_liters=_as_float(payload.get("volume_liters")),
            zone_dropoff=_as_int(payload.get("zone_dropoff")),
            eta_dropoff=parse_sim_time(payload.get("eta_dropoff")),
        )


@dataclass(frozen=True)
class CourierRuntimeState:
    """Estado del repartidor tal como lo describe `courier_state_overrides`."""

    continuous_riding_min: float = 0.0
    shift_elapsed_hours: float = 0.0
    last_break_end_time: datetime | None = None
    shift_end_time: datetime | None = None
    in_flight_orders: tuple[InFlightOrder, ...] = ()
    #: Zona donde esta el repartidor ahora. No es parte del contrato oficial
    #: pero si del hallazgo 6 de la auditoria (no existia la posicion actual);
    #: se llena desde Bloque 2 cuando corre el turno completo.
    current_zone: int | None = None

    @classmethod
    def from_overrides(
        cls,
        overrides: dict[str, Any] | None,
        *,
        base: CourierRuntimeState | None = None,
    ) -> CourierRuntimeState:
        """Aplica `courier_state_overrides` sobre `base` (el estado real).

        Ausente un campo, gana el de `base`; ausente `base`, gana el default
        seguro. Esto permite que el PROBE de validate_format.py (que no manda
        overrides) reciba un estado limpio y valido.
        """
        base = base or cls()
        overrides = overrides or {}

        def pick(key: str, current: Any) -> Any:
            return overrides[key] if key in overrides else current

        raw_in_flight = pick("in_flight_orders", None)
        if raw_in_flight is None:
            in_flight = base.in_flight_orders
        else:
            in_flight = tuple(InFlightOrder.from_payload(o) for o in raw_in_flight)

        return cls(
            continuous_riding_min=_as_float(
                pick("continuous_riding_min", base.continuous_riding_min),
                base.continuous_riding_min,
            ),
            shift_elapsed_hours=_as_float(
                pick("shift_elapsed_hours", base.shift_elapsed_hours),
                base.shift_elapsed_hours,
            ),
            last_break_end_time=parse_sim_time(pick("last_break_end_time", base.last_break_end_time)),
            shift_end_time=parse_sim_time(pick("shift_end_time", base.shift_end_time)),
            in_flight_orders=in_flight,
            current_zone=_as_int(pick("current_zone", base.current_zone), base.current_zone),
        )

    @property
    def in_flight_weight_kg(self) -> float:
        return sum(o.weight_kg for o in self.in_flight_orders)

    @property
    def in_flight_volume_liters(self) -> float:
        return sum(o.volume_liters for o in self.in_flight_orders)
