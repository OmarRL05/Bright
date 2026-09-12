import type { CourierState } from "@/lib/types";

interface MetricsProps {
  label: string;
  state?: CourierState;
}

function earningsPerHour(state?: CourierState, elapsedMinutes = 1): number {
  if (!state || elapsedMinutes <= 0) return 0;
  return (state.earnings / elapsedMinutes) * 60;
}

/** Métricas en vivo ($/hora, ganancias, tiempo restante) — Bloque 6. */
export function Metrics({ label, state }: MetricsProps) {
  return (
    <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
      <h3 className="text-sm font-medium text-gray-500">{label}</h3>
      <dl className="mt-2 grid grid-cols-3 gap-2 text-center">
        <div>
          <dt className="text-xs text-gray-500">Ganancias</dt>
          <dd className="text-lg font-semibold">${state?.earnings.toFixed(2) ?? "0.00"}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">$/hora</dt>
          <dd className="text-lg font-semibold">${earningsPerHour(state).toFixed(2)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">Tiempo restante</dt>
          <dd className="text-lg font-semibold">{state?.time_remaining.toFixed(0) ?? "--"} min</dd>
        </div>
      </dl>
    </div>
  );
}
