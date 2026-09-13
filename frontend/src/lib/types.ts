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
  // Posición en vivo del repartidor (lat, lon). La calcula el motor de
  // simulación (Bloque 1/3) cada tick, interpolando a lo largo del tramo
  // que está recorriendo — el Bloque 4 (GlobalOptimizer) NUNCA la escribe,
  // solo la lee vía snapshot() para fijar el depot del VRPTW/PDPTW.
  position: [number, number];
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