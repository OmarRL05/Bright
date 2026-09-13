import {
  CONSTRAINT_LABELS,
  isSafetyConstraint,
  type DecisionEvent,
} from "@/lib/types";

interface DashboardFeedProps {
  decisions?: DecisionEvent[];
  /** Mensaje del estado vacío. Distinto si es "aún no hay" o "no conecta". */
  emptyMessage?: string;
}

/**
 * Feed de decisiones (P2.3).
 *
 * El requisito del bloque es literal: `binding_constraint` visible en el
 * feed. Sin él, un rechazo se lee como "no quiso" y no se distingue una
 * refusal de seguridad de una de dinero -- que es justo lo que el contrato
 * oficial pide poder distinguir sin leer la prosa. Por eso el id crudo se
 * muestra tal cual además de la etiqueta legible: es el valor que un juez
 * compara contra el enum del schema.
 *
 * Las refusals de seguridad se pintan en ámbar y las de paga en gris: son
 * dos cosas distintas y el color lo dice antes que el texto.
 */
export default function DashboardFeed({
  decisions = [],
  emptyMessage = "Aún no hay decisiones. Manda un ping a POST /decide o corre el ensayo.",
}: DashboardFeedProps) {
  if (!Array.isArray(decisions) || decisions.length === 0) {
    return (
      <div className="flex h-96 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-400 dark:border-gray-700 dark:bg-gray-950">
        {emptyMessage}
      </div>
    );
  }

  return (
    <ul className="h-96 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-sm dark:divide-gray-800 dark:border-gray-800 dark:bg-gray-950">
      {decisions.map((decision, index) => (
        <FeedRow
          key={`${decision.order_id}-${decision.sim_time ?? index}`}
          decision={decision}
        />
      ))}
    </ul>
  );
}

function FeedRow({ decision }: { decision: DecisionEvent }) {
  const accepted = decision.decision === "ACCEPT";
  const constraint = decision.binding_constraint;
  const safety = isSafetyConstraint(constraint);

  return (
    <li className="px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-xs text-gray-400">
            {formatTime(decision.sim_time)}
          </span>
          <span className="font-mono text-sm font-semibold text-gray-900 dark:text-gray-100">
            {decision.order_id}
          </span>
        </div>

        <span
          className={
            accepted
              ? "rounded px-1.5 py-0.5 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200 dark:text-emerald-400 dark:ring-emerald-900"
              : "rounded px-1.5 py-0.5 text-xs font-semibold text-rose-700 ring-1 ring-rose-200 dark:text-rose-400 dark:ring-rose-900"
          }
        >
          {decision.decision}
        </span>
      </div>

      {constraint && (
        <div className="mt-1.5 flex items-center gap-2">
          <span
            className={
              safety
                ? "rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                : "rounded bg-gray-100 px-1.5 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-300"
            }
          >
            {safety ? "seguridad" : "economía"} · {CONSTRAINT_LABELS[constraint]}
          </span>
          <code className="text-xs text-gray-400">{constraint}</code>
        </div>
      )}

      <p className="mt-1.5 text-xs leading-relaxed text-gray-600 dark:text-gray-400">
        {decision.reason || "Sin motivo registrado"}
      </p>

      <div className="mt-1 flex gap-3 text-[11px] text-gray-400">
        <span>{decision.latency_ms.toFixed(2)} ms</span>
        <span>{decision.tier}</span>
        {decision.degraded && (
          <span className="font-medium text-amber-600">degradado</span>
        )}
      </div>
    </li>
  );
}

function formatTime(simTime: string | null): string {
  if (!simTime) return "--:--:--";
  const parsed = new Date(simTime);
  if (Number.isNaN(parsed.getTime())) return simTime;
  return parsed.toLocaleTimeString("es-MX", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
