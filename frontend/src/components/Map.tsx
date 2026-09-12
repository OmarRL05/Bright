"use client";

import type { CourierState, RoadEvent } from "@/lib/types";

interface MapProps {
  agentState?: CourierState;
  baselineState?: CourierState;
  roadEvents?: RoadEvent[];
}

/**
 * Mapa con la ruta activa y cierres viales (Bloque 6).
 *
 * TODO(equipo): elegir libreria de mapas (ej. react-leaflet o mapbox-gl) y
 * dibujar el grafo de Monterrey, la ruta activa de cada agente y los
 * cierres/trafico inyectados por el simulador.
 */
export function Map({ agentState, baselineState, roadEvents }: MapProps) {
  return (
    <div className="flex h-full min-h-96 w-full items-center justify-center rounded-lg border border-dashed border-gray-300 text-sm text-gray-500 dark:border-gray-700">
      Mapa (pendiente de implementar) — {agentState?.route.length ?? 0} paradas agente,{" "}
      {baselineState?.route.length ?? 0} paradas baseline, {roadEvents?.length ?? 0} eventos viales
    </div>
  );
}
