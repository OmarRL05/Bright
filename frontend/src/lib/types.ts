// Espejo en TypeScript de los contratos de datos del backend.
// Ver docs/01_Arquitectura.md seccion 8 y backend/core/models.py.
// Mantener sincronizado a mano: si cambia uno, cambia el otro.

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

export interface DecisionLog {
  accepted: boolean;
  offer_id: string;
  reason: string;
  timestamp: number;
}

export interface SimulationSnapshot {
  agent: CourierState;
  baseline: CourierState;
  logs: DecisionLog[];
}
