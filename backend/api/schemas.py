"""Contrato HTTP oficial de POST /decide y GET /explain_decision.

Ver student-materials/courier/decision_response_schema.json (fuente de
verdad) y evaluation_protocol.md. Los nombres de campo estan fijados por el
material del reto -- no renombrar sin actualizar tambien el validador propio
del equipo (validate_format.py se corre tal cual viene, sin tocar).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Vehicle = Literal["moto", "car", "bike"]
Decision = Literal["ACCEPT", "SKIP"]
#: Los cuatro del contrato oficial (event_log_schema.json, evento `shock`).
ShockType = Literal["surge", "closure", "rain", "delay"]
BindingConstraint = Literal[
    "flagged_zone_night",
    "mandatory_break",
    "heat_rule",
    "shift_end_infeasible",
    "vehicle_capacity",
    "reservation_wage",
]


class CourierStateOverrides(BaseModel):
    model_config = ConfigDict(extra="ignore")

    continuous_riding_min: float = 0.0
    shift_elapsed_hours: float = 0.0
    last_break_end_time: datetime | None = None
    shift_end_time: datetime | None = None
    # Forma exacta no especificada en el material (el ejemplo solo muestra
    # `[]`); se deja sin tipar para no rechazar payloads reales de jueces con
    # una forma distinta a la que adivinemos.
    in_flight_orders: list[dict[str, Any]] = Field(default_factory=list)


class DecideRequest(BaseModel):
    """Espejo de `order_offered` (event_log_schema.json) + overrides de estado."""

    model_config = ConfigDict(extra="ignore")

    order_id: str
    platform: Literal["rappi", "didi", "uber"] | None = None
    sim_time: datetime
    zone_pickup: int
    zone_dropoff: int
    distance_pickup_km: float
    distance_delivery_km: float
    base_pay_mxn: float
    est_tip_mxn: float = 0.0
    surge_multiplier: float = 1.0
    restaurant_prep_min: float = 0.0
    weight_kg: float | None = None
    volume_liters: float | None = None
    vehicle: Vehicle
    estimated_pickup_min: float | None = None
    estimated_delivery_min: float | None = None
    courier_state_overrides: CourierStateOverrides = Field(default_factory=CourierStateOverrides)


class EconomicsBreakdown(BaseModel):
    """Aritmetica de la decision. Los 6 primeros campos son los que nombra
    `decision_response_schema.json`; los 4 ultimos son el desglose que hace
    auditable el neto (combustible y valor de la zona de dropoff) cuando un
    juez pausa una decision y pide las cuentas."""

    net_pay_mxn: float
    total_time_min: float
    raw_rate_mxn_hr: float
    adjusted_rate_mxn_hr: float
    reservation_wage_mxn_hr: float
    deadhead_km: float
    gross_pay_mxn: float = 0.0
    fuel_cost_mxn: float = 0.0
    total_km: float = 0.0
    dropoff_demand_score: float = 0.5


class StatusResponse(BaseModel):
    """Estado de salud del servicio. El protocolo (seccion 7) acepta que el
    modo degradado se señale "through its response, logs or a status
    endpoint" -- esto es el tercero de los tres."""

    degraded: bool
    tier: str
    reservation_wage_mxn_hr: float
    strategy_revision: int
    strategy_source: str
    strategy_reasoning: str
    consecutive_model_failures: int
    last_model_error: str | None
    advisor: str
    decisions_recorded: int


class ShockRequest(BaseModel):
    """Espejo del evento `shock` (event_log_schema.json).

    Los jueces pueden inyectar shocks en vivo (evaluation_protocol.md seccion
    5), asi que el body que se acepta es el MISMO objeto que el log emite: si
    lo copian de un log grabado y lo pegan aqui, tiene que entrar sin
    traducirlo.

    `extra="ignore"` por la misma razon que en DecideRequest: un `event:
    "shock"` de mas -- que es justo lo que trae una linea copiada del log --
    no puede ser un 422.
    """

    model_config = ConfigDict(extra="ignore")

    shock_type: ShockType
    sim_time: datetime | None = None
    zone: int | None = None
    multiplier: float | None = None
    road: str | None = None
    order_id: str | None = None
    slip_min: float | None = None
    duration_min: float | None = None


class ShockResponse(BaseModel):
    """Acuse de recibo de una inyeccion.

    Devuelve el evento tal como quedo REGISTRADO, no como llego: si el
    multiplicador se recorto o la duracion se completo con el default, el juez
    lo ve en la respuesta en vez de descubrirlo despues en el log.
    """

    accepted: bool
    shock: dict[str, Any]
    active_shocks: int
    expires_at: datetime | None = None
    note: str


class ActiveShocksResponse(BaseModel):
    """Que shocks estan mordiendo ahora mismo.

    Contesta "what would it do if a surge hit right now?" sin re-correr nada:
    los eventos vigentes, en el formato oficial, listos para leerse en voz
    alta o para pegarse en un log de replay.
    """

    active: list[dict[str, Any]]
    history: list[dict[str, Any]]
    evaluated_at: datetime | None = None


class DecideResponse(BaseModel):
    order_id: str
    decision: Decision
    reason: str
    binding_constraint: BindingConstraint | None = None
    latency_ms: float
    tier: Literal["tier1", "tier2"] = "tier1"
    degraded: bool = False
    economics: EconomicsBreakdown | None = None
    #: Shocks vigentes que movieron los numeros de ESTA decision, en corto.
    #: Campo extra al contrato oficial (que permite extras): es lo que hace
    #: visible en la respuesta que el shock inyectado en vivo entro, sin
    #: tener que abrir el log a media demo.
    shocks_applied: list[str] = Field(default_factory=list)


class AlternativeConsidered(BaseModel):
    option: str
    rejected_because: str


class ExplainDecisionResponse(BaseModel):
    order_id: str
    decision: Decision
    reason: str
    inputs: dict[str, Any]
    alternatives_considered: list[AlternativeConsidered]
