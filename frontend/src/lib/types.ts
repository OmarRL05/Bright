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
   * rodeo (barato, sin red y sin reloj -- requisito del presupuesto de 50 ms)
   * y el MAPA dibuja calle real desde la cache de OSRM. Se lee de aqui y no se
   * escribe a mano para que el rotulo no envejezca el dia que eso cambie.
   */
  distance_model: string;
  road_detour_factor: number;
  route_geometry_available: boolean;
  /** Cuántos de los 240 pares de zonas tienen geometría en disco. */
  routes_cached?: number;
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

/*
 * Los shocks NO tienen tabla de colores.
 *
 * La tenian (ambar/rosa/zinc/cyan) y tres de esos cuatro chocaban con una
 * señal de la consola: ambar ya significa "lo paro la seguridad", rosa se
 * confundia con "sin backend" y zinc con "no salieron las cuentas". Cuatro
 * colores que hay que memorizar para distinguir lluvia de retraso valen menos
 * que un solo tono ajeno a las señales (`--shock`) mas la etiqueta del tipo
 * escrita al lado, que es lo que el mapa hace ahora.
 */

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

// ---------------------------------------------------------------------------
// Tabla de resultados (GET /results)
// ---------------------------------------------------------------------------

/*
 * Espejo de core.evaluation.report. Las comparaciones vienen CALCULADAS del
 * backend a proposito: "gana un 29.7% mas" depende por completo de contra que
 * se compara, y esa eleccion es de evaluacion, no de presentacion. Si el
 * dashboard la tomara por su cuenta, la pantalla, la lamina y el documento
 * podrian citar tres numeros distintos, todos ciertos y ninguno el mismo.
 */

/** Una fila de results_table.csv. */
export interface FilaResultado {
  policy: string;
  etiqueta: string;
  mean_earnings_mxn: number;
  median_earnings_mxn: number;
  mean_mxn_per_hr: number;
  accept_rate_pct: number;
  orders_completed: number;
  deadhead_pct_of_km: number;
  deadline_misses: number;
  safety_violations: number;
  /** Nuestro agente: la fila que se resalta. */
  propia: boolean;
  /** La cota superior clarividente. Se dibuja como límite, no como rival. */
  techo: boolean;
}

export interface Comparacion {
  contra: string;
  etiqueta_contra: string;
  /** Qué aísla esta comparación. */
  pregunta: string;
  /** La que encabeza: misma seguridad, sin nuestra capa de posicionamiento. */
  titular: boolean;
  ganancia_propia_mxn: number;
  ganancia_contra_mxn: number;
  delta_mxn: number;
  delta_pct: number;
  violaciones_contra: number;
}

export interface Resultados {
  disponible: boolean;
  /** Comentarios `#` del CSV: llevan la regla de seeds disjuntas. */
  notas: string[];
  filas: FilaResultado[];
  comparaciones: Comparacion[];
  captura_del_techo_pct: number | null;
  meta: {
    shifts?: number;
    shift_hours?: number;
    vehicle?: string;
    conjunto?: string;
    generated_at?: string;
  };
}
