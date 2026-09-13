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

/** Evento `decision` del event log. Es lo que sirve GET /decisions. */
export interface DecisionEvent {
  event: "decision";
  order_id: string;
  sim_time: string | null;
  decision: "ACCEPT" | "SKIP";
  reason: string;
  binding_constraint: BindingConstraint | null;
  latency_ms: number;
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
}

/** Una entrada de GET /replays. */
export interface ReplaySummary {
  seed: number;
  bytes: number;
  url: string;
}

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
