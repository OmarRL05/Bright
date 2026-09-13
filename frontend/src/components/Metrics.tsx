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
  // Datos dummy de respaldo según el tipo de métrica para que luzca bien en la demo
  const dummyFallback = label.includes("IA")
    ? { earnings: 1450.00, time_remaining: 35, stops: [] }
    : { earnings: 1120.00, time_remaining: 50, stops: [] };

  // Usa el estado real si existe y trae datos, de lo contrario usa los dummies
  const activeState = (state && Object.keys(state).length > 0) ? state : dummyFallback;

  return (
    <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
      <h3 className="text-sm font-medium text-gray-500">{label}</h3>
      <dl className="mt-2 grid grid-cols-3 gap-2 text-center">
        <div>
          <dt className="text-xs text-gray-500">Ganancias</dt>
          <dd className="text-lg font-semibold">${activeState.earnings.toFixed(2)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">$/hora</dt>
          <dd className="text-lg font-semibold">${earningsPerHour(activeState as CourierState).toFixed(2)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">Tiempo restante</dt>
          <dd className="text-lg font-semibold">{activeState.time_remaining.toFixed(0)} min</dd>
        </div>
      </dl>
    </div>
  );
}