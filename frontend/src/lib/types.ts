// Espejo en TypeScript de los contratos de datos del backend.
// Mantener sincronizado a mano: si cambia uno, cambia el otro.
//
// Hay DOS familias de tipos aqui, y conviene no mezclarlas:
//
//  1. El contrato OFICIAL (student-materials/courier/) -- zonas enteras,
//     tiempo absoluto ISO, `binding_constraint`. Es lo que el endpoint
//     /decide devuelve y lo que los jueces leen. Todo lo nuevo va aqui.
//  2. El contrato interno del motor VRPTW (coordenadas, minutos relativos).
//     Lo usan el mapa y las metricas heredadas.

// ---------------------------------------------------------------------------
// Contrato oficial
// ---------------------------------------------------------------------------

/** Los seis ids que `validate_format.py` acepta, o null si decidio la paga. */
export type BindingConstraint =
  | "flagged_zone_night"
  | "mandatory_break"
  | "heat_rule"
  | "shift_end_infeasible"
  | "vehicle_capacity"
  | "reservation_wage";

/**
 * Evento `decision` del event log. Es lo que sirve GET /decisions.
 *
 * `zone_pickup`/`zone_dropoff` NO son parte del evento `decision` oficial
 * (event_log_schema.json los deja en `order_offered`, aparte) -- GET
 * /decisions los agrega solo para el dashboard, ver
 * core.agent.journal.to_dashboard_decision_event en el backend. Optional
 * porque un replay muy viejo o una fuente distinta podria no traerlos.
 */
export interface DecisionEvent {
  event: "decision";
  order_id: string;
  sim_time: string | null;
  decision: "ACCEPT" | "SKIP";
  reason: string;
  binding_constraint: BindingConstraint | null;
  latency_ms: number;
  zone_pickup?: number | null;
  zone_dropoff?: number | null;
  tier: "tier1" | "tier2";
  degraded: boolean;
}

/** Respuesta de GET /status. */
export interface AgentStatus {
  degraded: boolean;
  tier: string;
  reservation_wage_mxn_hr: number;
  strategy_revision: number;
  strategy_source: string;
  strategy_reasoning: string;
  consecutive_model_failures: number;
  last_model_error: string | null;
  advisor: string;
  decisions_recorded: number;
  /**
   * Como mide distancias el sistema, en sus propias palabras. Corre a dos
   * velocidades a proposito: las DECISIONES usan haversine por un factor de
   * rodeo (barato y sin dependencias) y el MAPA dibuja calle real cuando el
   * grafo vial esta descargado. Se lee de aqui y no se escribe a mano para
   * que el rotulo no envejezca el dia que eso cambie.
   */
  distance_model: string;
  road_detour_factor: number;
  route_geometry_available: boolean;
}

/** Una entrada de GET /replays. */
export interface ReplaySummary {
  seed: number;
  bytes: number;
  url: string;
}

/**
 * Una zona de GET /zones. `coord` es [lat, lon] -- viene tal cual de
 * `core.models.DEFAULT_ZONE_MAP`, la unica fuente de verdad compartida con
 * el motor de decision y el simulador. `flagged` es la constraint real de
 * `flagged_zone_night` (core.agent.safety.FLAGGED_ZONES), no un color
 * elegido a mano aqui.
 */
export interface Zone {
  zone_id: number;
  name: string;
  coord: [number, number];
  demand_score: number;
  flagged: boolean;
}

export type ShockType = "surge" | "closure" | "rain" | "delay";

/**
 * Una entrada de `active`/`history` en GET /shocks -- espejo de
 * `core.agent.shocks.Shock.to_event()`. Los campos opcionales solo vienen
 * si aplican al tipo (un shock de lluvia no trae `zone`).
 */
export interface ShockInfo {
  event: "shock";
  shock_type: ShockType;
  sim_time: string | null;
  duration_min: number;
  zone?: number;
  multiplier?: number;
  road?: string;
  order_id?: string;
  slip_min?: number;
}

export const SHOCK_COLORS: Record<ShockType, string> = {
  surge: "#f59e0b", // amber-500 -- sube el pago
  closure: "#f43f5e", // rose-500 -- bloquea tramo
  delay: "#a1a1aa", // zinc-400 -- retraso puntual
  rain: "#38bdf8", // sky-400 -- ralentiza, no bloquea
};

/**
 * Las constraints de seguridad, para pintarlas distinto del rechazo por paga.
 * `reservation_wage` NO esta aqui a proposito: es economia, no seguridad, y
 * distinguir las dos cosas de un vistazo es el punto del feed.
 */
export const SAFETY_CONSTRAINTS: readonly BindingConstraint[] = [
  "flagged_zone_night",
  "mandatory_break",
  "heat_rule",
  "shift_end_infeasible",
  "vehicle_capacity",
] as const;

/** Etiqueta legible de cada constraint. El id crudo tambien se muestra. */
export const CONSTRAINT_LABELS: Record<BindingConstraint, string> = {
  flagged_zone_night: "Zona marcada de noche",
  mandatory_break: "Descanso obligatorio",
  heat_rule: "Regla de calor",
  shift_end_infeasible: "No alcanza el turno",
  vehicle_capacity: "Capacidad del vehículo",
  reservation_wage: "Paga insuficiente",
};

export function isSafetyConstraint(
  constraint: BindingConstraint | null,
): boolean {
  return constraint !== null && SAFETY_CONSTRAINTS.includes(constraint);
}

// ---------------------------------------------------------------------------
// Contrato interno del motor VRPTW (mapa y metricas heredadas)
// ---------------------------------------------------------------------------

export interface Offer {
  id: string;
  pickup: [number, number];
  dropoff: [number, number];
  pay: number;
  time_window: [number, number];
  received_at: number;
  demand_percentile: number;
}

export type RoadEventType = "closure" | "traffic" | "surge";

export interface RoadEvent {
  type: RoadEventType;
  location: [number, number] | [number, number][];
  multiplier: number | null;
  timestamp: number;
}

export interface RouteStop {
  offer_id: string;
  kind: "pickup" | "dropoff";
  eta: number;
}

export interface CourierState {
  version: number;
  time_remaining: number;
  earnings: number;
  backpack: Offer[];
  route: RouteStop[];
}

export interface SimulationSnapshot {
  agent: CourierState;
  baseline: CourierState;
}
