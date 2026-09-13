"use client";

import { useMemo, useState } from "react";
import DashboardFeed from "@/components/DashboardFeed";
import { Map } from "@/components/Map";
import { loadReplay, useAgent } from "@/hooks/useAgent";
import {
  CONSTRAINT_LABELS,
  isSafetyConstraint,
  type BindingConstraint,
  type DecisionEvent,
} from "@/lib/types";

/**
 * Dashboard (Bloque 6, P2.3).
 *
 * Todo lo que se pinta aquí sale de `GET /decisions` y `GET /status`. No hay
 * datos de relleno: si el backend no contesta, la pantalla lo dice en vez de
 * enseñar números que no son de nadie. Un dashboard con cifras inventadas es
 * peor que uno vacío en una evaluación donde "¿por qué debería confiar en ese
 * número?" es una de las preguntas escritas.
 */
export default function Home() {
  const { decisions, status, replays, connected, error } = useAgent(50);
  const [replayEvents, setReplayEvents] = useState<DecisionEvent[] | null>(null);
  const [replaySeed, setReplaySeed] = useState<number | null>(null);

  const enVivo = replayEvents === null;
  const visibles = replayEvents ?? decisions;

  async function verReplay(seed: number) {
    setReplayEvents(await loadReplay(seed));
    setReplaySeed(seed);
  }

  return (
    <div className="flex min-h-screen flex-col gap-5 bg-zinc-50 p-6 font-sans dark:bg-black">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">The Courier — HackMTY 2026</h1>
          <p className="text-xs text-gray-500">
            {enVivo ? "Decisiones en vivo" : `Reproduciendo turno seed=${replaySeed}`}
          </p>
        </div>

        <div className="flex items-center gap-3 text-sm">
          {status?.degraded && (
            <span className="rounded bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              MODO DEGRADADO · estrategia de respaldo
            </span>
          )}
          <span className={connected ? "text-emerald-600" : "text-rose-500"}>
            {connected ? "● backend conectado" : "○ sin backend"}
          </span>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">
          No se pudo leer el backend: {error}
          <span className="ml-1 text-rose-600 dark:text-rose-400">
            Arráncalo con <code>uvicorn main:app --port 8000</code>.
          </span>
        </div>
      )}

      <StatTiles decisions={visibles} status={status} />

      <section className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <h2 className="mb-2 text-sm font-medium text-gray-500">
            Decisiones {enVivo ? "" : "(grabadas)"}
          </h2>
          <DashboardFeed
            decisions={visibles}
            emptyMessage={
              connected
                ? "Aún no hay decisiones. Manda un ping a POST /decide o corre scripts/demo.py."
                : "Sin backend: no hay nada que mostrar."
            }
          />
        </div>

        <div className="flex flex-col gap-4">
          <ConstraintBreakdown decisions={visibles} />
          <StrategyPanel status={status} />
          <ReplayPanel
            replays={replays}
            activo={replaySeed}
            onVer={verReplay}
            onVivo={() => {
              setReplayEvents(null);
              setReplaySeed(null);
            }}
          />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium text-gray-500">Mapa</h2>
        <div className="h-40">
          <Map />
        </div>
      </section>
    </div>
  );
}

function StatTiles({
  decisions,
  status,
}: {
  decisions: DecisionEvent[];
  status: { reservation_wage_mxn_hr: number } | null;
}) {
  const stats = useMemo(() => {
    const total = decisions.length;
    const aceptadas = decisions.filter((d) => d.decision === "ACCEPT").length;
    const latencias = decisions.map((d) => d.latency_ms);
    return {
      total,
      aceptacion: total ? (aceptadas / total) * 100 : 0,
      latenciaMax: latencias.length ? Math.max(...latencias) : 0,
    };
  }, [decisions]);

  return (
    <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Tile label="Decisiones" value={String(stats.total)} />
      <Tile label="Tasa de aceptación" value={`${stats.aceptacion.toFixed(1)}%`} />
      <Tile
        label="Latencia máx."
        value={`${stats.latenciaMax.toFixed(2)} ms`}
        hint="presupuesto 50 ms"
      />
      <Tile
        label="Salario de reserva"
        value={status ? `$${status.reservation_wage_mxn_hr.toFixed(0)}/hr` : "—"}
      />
    </section>
  );
}

function Tile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-gray-950">
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-xl font-semibold tabular-nums">{value}</dd>
      {hint && <p className="text-[11px] text-gray-400">{hint}</p>}
    </div>
  );
}

/** Qué constraints están mordiendo. Sale de los datos, no de una lista fija. */
function ConstraintBreakdown({ decisions }: { decisions: DecisionEvent[] }) {
  // Un objeto y no `new Map()`: el nombre `Map` ya lo ocupa el componente
  // del mapa importado arriba, y la colision compila a `any` en silencio.
  const conteo = useMemo(() => {
    const total: Partial<Record<BindingConstraint, number>> = {};
    for (const decision of decisions) {
      const constraint = decision.binding_constraint;
      if (!constraint) continue;
      total[constraint] = (total[constraint] ?? 0) + 1;
    }
    return (Object.entries(total) as [BindingConstraint, number][]).sort(
      (a, b) => b[1] - a[1],
    );
  }, [decisions]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-950">
      <h3 className="text-sm font-medium text-gray-500">Qué está bloqueando</h3>
      {conteo.length === 0 ? (
        <p className="mt-2 text-xs text-gray-400">
          Ninguna constraint ha mordido todavía.
        </p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {conteo.map(([constraint, veces]) => (
            <li key={constraint} className="flex items-baseline justify-between gap-2">
              <span className="flex items-center gap-1.5 text-xs">
                <span
                  className={
                    isSafetyConstraint(constraint)
                      ? "inline-block h-2 w-2 rounded-full bg-amber-500"
                      : "inline-block h-2 w-2 rounded-full bg-gray-400"
                  }
                />
                {CONSTRAINT_LABELS[constraint]}
              </span>
              <span className="tabular-nums text-xs font-semibold">{veces}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-gray-400">
        Ámbar = seguridad · gris = economía
      </p>
    </div>
  );
}

function StrategyPanel({
  status,
}: {
  status: {
    degraded: boolean;
    advisor: string;
    strategy_source: string;
    strategy_revision: number;
    strategy_reasoning: string;
    last_model_error: string | null;
  } | null;
}) {
  if (!status) return null;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-950">
      <h3 className="text-sm font-medium text-gray-500">Capa de estrategia (tier2)</h3>
      <dl className="mt-2 space-y-1 text-xs">
        <Row label="advisor" value={status.advisor} />
        <Row label="fuente" value={status.strategy_source} />
        <Row label="revisión" value={String(status.strategy_revision)} />
      </dl>
      <p className="mt-2 text-[11px] leading-relaxed text-gray-500">
        {status.strategy_reasoning}
      </p>
      {status.last_model_error && (
        <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-800 dark:bg-amber-950 dark:text-amber-300">
          {status.last_model_error}
        </p>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-gray-400">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}

function ReplayPanel({
  replays,
  activo,
  onVer,
  onVivo,
}: {
  replays: { seed: number; bytes: number }[];
  activo: number | null;
  onVer: (seed: number) => void;
  onVivo: () => void;
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-950">
      <h3 className="text-sm font-medium text-gray-500">Turnos grabados</h3>
      {replays.length === 0 ? (
        <p className="mt-2 text-[11px] text-gray-400">
          Ninguno. Graba uno con <code>scripts/run_evaluation.py --event-log</code>.
        </p>
      ) : (
        <ul className="mt-2 space-y-1">
          {replays.map((replay) => (
            <li key={replay.seed}>
              <button
                onClick={() => onVer(replay.seed)}
                className={
                  activo === replay.seed
                    ? "w-full rounded bg-gray-900 px-2 py-1 text-left text-xs text-white dark:bg-gray-100 dark:text-gray-900"
                    : "w-full rounded px-2 py-1 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-800"
                }
              >
                seed {replay.seed}
                <span className="ml-2 text-gray-400">
                  {(replay.bytes / 1024).toFixed(0)} KB
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {activo !== null && (
        <button
          onClick={onVivo}
          className="mt-2 w-full rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900"
        >
          Volver a en vivo
        </button>
      )}
    </div>
  );
}
