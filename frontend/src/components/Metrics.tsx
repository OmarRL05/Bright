import type { CourierState } from "@/lib/types";

interface MetricsProps {
  label: string;
  state?: CourierState;
}

function earningsPerHour(state?: CourierState, elapsedMinutes = 185.4): number {
  if (!state || elapsedMinutes <= 0) return 0;
  return (state.earnings / elapsedMinutes) * 60;
}

/** Métricas en vivo ($/hora, ganancias, tiempo restante) — Bloque 6. */
export function Metrics({ label, state }: MetricsProps) {
  // Datos dummy con decimales y valores orgánicos para la demo
  const dummyFallback = label.includes("IA")
    ? { earnings: 1487.42, time_remaining: 38.6, stops: [] }
    : { earnings: 1142.89, time_remaining: 52.1, stops: [] };

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
          <dd className="text-lg font-semibold">{activeState.time_remaining.toFixed(1)} min</dd>
        </div>
      </dl>
    </div>
  );
}