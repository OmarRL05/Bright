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
 * El libro de decisiones (P2.3).
 *
 * El requisito del bloque es literal: `binding_constraint` visible en el feed.
 * Pero el requisito de fondo es más exigente — el protocolo dice que un juez
 * pregunta "¿por qué saltaste ese pedido?" y espera la respuesta en menos de
 * diez segundos. Diez segundos no alcanzan para leer prosa, así que cada fila
 * lleva una **regla de color a la izquierda** que dice, antes que ninguna
 * palabra, si bloqueó la seguridad o si no salieron las cuentas.
 *
 * Filas planas y sin tarjetas: esto es un registro y un registro se escanea en
 * vertical. Cajas redondeadas separadas romperían ese barrido.
 */
export default function DashboardFeed({
  decisions = [],
  emptyMessage = "Sin decisiones todavía. Manda un ping a POST /decide o corre scripts/demo.py.",
}: DashboardFeedProps) {
  if (!Array.isArray(decisions) || decisions.length === 0) {
    return (
      <div className="flex h-full min-h-64 items-center justify-center border border-dashed border-line px-8 text-center text-sm leading-relaxed text-muted">
        {emptyMessage}
      </div>
    );
  }

  return (
    <ol className="h-full overflow-y-auto border border-line bg-panel">
      {decisions.map((decision, index) => (
        <FeedRow
          key={`${decision.order_id}-${decision.sim_time ?? index}`}
          decision={decision}
        />
      ))}
    </ol>
  );
}

function FeedRow({ decision }: { decision: DecisionEvent }) {
  const accepted = decision.decision === "ACCEPT";
  const constraint = decision.binding_constraint;
  const safety = isSafetyConstraint(constraint);

  // La regla de color: verde si entró, ámbar si la paró la seguridad, pizarra
  // si la pararon las cuentas. Es lo único que hace falta ver para saber de
  // qué clase de decisión se trata.
  const regla = accepted
    ? "border-l-go"
    : safety
      ? "border-l-safety"
      : "border-l-pay";
  const tono = accepted ? "text-go" : safety ? "text-safety" : "text-pay";

  return (
    <li className={`border-b border-l-2 border-line px-4 py-3 ${regla}`}>
      <div className="flex items-baseline gap-3">
        <time className="tabular font-mono text-xs text-muted">
          {formatTime(decision.sim_time)}
        </time>
        <span className="font-mono text-[13px]">{decision.order_id}</span>
        <span className={`ml-auto font-mono text-xs ${tono}`}>{decision.decision}</span>
      </div>

      {constraint && (
        <p className="mt-1.5 text-xs">
          <span className={tono}>{safety ? "Seguridad" : "Economía"}</span>
          <span className="text-muted"> — {CONSTRAINT_LABELS[constraint]}</span>
          <code className="ml-2 font-mono text-[11px] opacity-60">{constraint}</code>
        </p>
      )}

      <p className="mt-1 max-w-[62ch] text-[13px] leading-relaxed text-muted">
        {decision.reason || "Sin motivo registrado"}
      </p>

      <p className="tabular mt-1.5 font-mono text-[11px] text-muted opacity-60">
        {decision.latency_ms.toFixed(2)} ms · {decision.tier}
        {decision.degraded && <span className="text-safety"> · degradado</span>}
      </p>
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
