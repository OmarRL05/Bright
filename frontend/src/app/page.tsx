"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import DashboardFeed from "@/components/DashboardFeed";
import { loadReplay, useAgent } from "@/hooks/useAgent";
import {
  CONSTRAINT_LABELS,
  isSafetyConstraint,
  type AgentStatus,
  type BindingConstraint,
  type DecisionEvent,
} from "@/lib/types";

// Leaflet toca `window` al importarse -- sin ssr:false, el primer render en
// el servidor revienta. Ver frontend/AGENTS.md sobre no asumir el Next.js
// que ya conoces: en el App Router esto sigue resolviendose con
// next/dynamic, no con una condicion de `typeof window`.
const Map = dynamic(() => import("@/components/Map").then((m) => m.Map), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center border border-line text-sm text-muted">
      Cargando mapa…
    </div>
  ),
});

/**
 * Consola de operaciones del agente (Bloque 6, P2.3).
 *
 * Todo lo que se pinta sale de `GET /decisions`, `/status`, `/zones` y
 * `/shocks`. No hay datos de relleno: si el backend no contesta, la pantalla
 * lo dice en vez de enseñar números que no son de nadie. Un dashboard con
 * cifras inventadas es peor que uno vacío en una evaluación donde "¿por qué
 * debería confiar en ese número?" es una de las preguntas escritas.
 *
 * La jerarquía no es casual. Un dashboard normal pone arriba lo que crece;
 * aquí lo que importa son los **rechazos** y la regla que los causó, así que
 * el libro de decisiones ocupa una columna entera a altura completa y todo lo
 * demás le hace sitio.
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
    <div className="min-h-screen bg-ink px-5 py-4 text-text">
      <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-line pb-3">
        <h1 className="text-lg font-medium tracking-tight">The Courier</h1>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <span className="text-muted">
            {/* "En vivo" hacia pensar que hay un turno corriendo de fondo. No
                lo hay: el feed solo se mueve cuando algo postea a /decide. */}
            {enVivo
              ? "Últimas decisiones del servidor"
              : `Turno grabado, seed ${replaySeed}`}
          </span>
          {status?.degraded && (
            <span className="border border-safety px-2 py-0.5 font-medium text-safety">
              Modo degradado — decidiendo con la última estrategia conocida
            </span>
          )}
          <span className={connected ? "text-go" : "text-alert"}>
            {connected ? "Backend conectado" : "Sin backend"}
          </span>
        </div>
      </header>

      {error && (
        <p className="mt-3 border border-alert px-4 py-2.5 text-sm text-alert">
          No se pudo leer el backend: {error}. Arráncalo con{" "}
          <code className="font-mono">uvicorn main:app --port 8000</code>.
        </p>
      )}

      <InstrumentStrip decisions={visibles} status={status} enVivo={enVivo} />

      <main className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="flex flex-col gap-4">
          <section>
            <SectionLabel>
              {enVivo ? "Mapa de la operación" : "Mapa del turno grabado"}
            </SectionLabel>
            <div className="h-[30rem] border border-line">
              <Map decisions={visibles} live={enVivo} />
            </div>
          </section>

          <Blocking decisions={visibles} />
        </div>

        <section className="flex min-h-[30rem] flex-col lg:h-[calc(100vh-13rem)]">
          <SectionLabel>Libro de decisiones</SectionLabel>
          <div className="min-h-0 flex-1">
            <DashboardFeed
              decisions={visibles}
              emptyMessage={
                connected
                  ? "Sin decisiones todavía. Manda un ping a POST /decide o corre scripts/demo.py."
                  : "Sin backend: no hay nada que mostrar."
              }
            />
          </div>
        </section>
      </main>

      <footer className="mt-4 grid gap-4 md:grid-cols-2">
        <Strategy status={status} enVivo={enVivo} />
        <Replays
          replays={replays}
          activo={replaySeed}
          onVer={verReplay}
          onVivo={() => {
            setReplayEvents(null);
            setReplaySeed(null);
          }}
        />
      </footer>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-1.5 text-xs text-muted">{children}</h2>;
}

/**
 * Los cuatro números que describen el estado del agente, en una banda única
 * separada por filetes.
 *
 * No son cuatro tarjetas: cuatro cajas idénticas dicen "estas cosas son del
 * mismo tipo y ninguna importa más", y aquí la peor latencia y el salario de
 * reserva son magnitudes distintas que se leen de un vistazo, no elementos de
 * una colección.
 */
function InstrumentStrip({
  decisions,
  status,
  enVivo,
}: {
  decisions: DecisionEvent[];
  status: AgentStatus | null;
  enVivo: boolean;
}) {
  const stats = useMemo(() => {
    const total = decisions.length;
    const aceptadas = decisions.filter((d) => d.decision === "ACCEPT").length;
    const latencias = decisions.map((d) => d.latency_ms);
    const medidas = latencias.filter((ms) => ms > 0);
    return {
      total,
      aceptadas,
      aceptacion: total ? (aceptadas / total) * 100 : 0,
      // Solo cuentan las latencias MEDIDAS. Un turno grabado las trae en 0
      // porque el arnés no las mide; promediarlas daría "0.00 ms", que no es
      // una marca excelente sino un dato que no existe.
      latenciaMax: medidas.length ? Math.max(...medidas) : null,
    };
  }, [decisions]);

  return (
    <dl className="mt-4 grid grid-cols-2 border border-line bg-panel md:grid-cols-4">
      <Reading
        valor={String(stats.total)}
        etiqueta="Ofertas evaluadas"
        nota={`${stats.aceptadas} aceptadas`}
      />
      <Reading
        valor={`${stats.aceptacion.toFixed(1)}%`}
        etiqueta="Se acepta"
        nota="de las ofertas que llegaron"
      />
      <Reading
        valor={stats.latenciaMax === null ? "—" : `${stats.latenciaMax.toFixed(2)} ms`}
        etiqueta="Peor latencia"
        nota={
          stats.latenciaMax === null
            ? enVivo
              ? "sin decisiones medidas todavía"
              : "no se mide al grabar un turno"
            : "tope del protocolo: 50 ms"
        }
        bien={stats.latenciaMax === null ? undefined : stats.latenciaMax < 50}
      />
      <Reading
        valor={status ? `$${status.reservation_wage_mxn_hr.toFixed(0)}` : "—"}
        etiqueta="Salario de reserva"
        nota="por hora; bajo esto se rechaza"
      />
    </dl>
  );
}

function Reading({
  valor,
  etiqueta,
  nota,
  bien,
}: {
  valor: string;
  etiqueta: string;
  nota: string;
  bien?: boolean;
}) {
  const tono = bien === undefined ? "" : bien ? "text-go" : "text-alert";
  return (
    <div className="border-line px-4 py-3 [&:not(:last-child)]:border-r">
      <dd className={`tabular text-2xl font-medium tracking-tight ${tono}`}>{valor}</dd>
      <dt className="mt-0.5 text-[13px]">{etiqueta}</dt>
      <p className="text-[11px] text-muted">{nota}</p>
    </div>
  );
}

/**
 * Qué constraints están mordiendo, como distribución y no como lista.
 *
 * Es una distribución: tiene forma, y una barra la enseña de un vistazo
 * mientras que una lista con viñetas obliga a comparar cifras a mano. El color
 * es el mismo del libro de decisiones — ámbar si paró la seguridad, pizarra si
 * pararon las cuentas — así que no necesita leyenda.
 */
function Blocking({ decisions }: { decisions: DecisionEvent[] }) {
  const filas = useMemo(() => {
    const total: Partial<Record<BindingConstraint, number>> = {};
    for (const decision of decisions) {
      const constraint = decision.binding_constraint;
      if (!constraint) continue;
      total[constraint] = (total[constraint] ?? 0) + 1;
    }
    const entradas = (Object.entries(total) as [BindingConstraint, number][]).sort(
      (a, b) => b[1] - a[1],
    );
    const tope = entradas[0]?.[1] ?? 1;
    return entradas.map(([constraint, veces]) => ({
      constraint,
      veces,
      ancho: (veces / tope) * 100,
    }));
  }, [decisions]);

  return (
    <section>
      <SectionLabel>Qué está bloqueando</SectionLabel>
      <div className="border border-line bg-panel px-4 py-3">
        {filas.length === 0 ? (
          <p className="text-xs text-muted">Ninguna constraint ha mordido todavía.</p>
        ) : (
          <ul className="space-y-2">
            {filas.map(({ constraint, veces, ancho }) => (
              <li key={constraint}>
                <div className="flex items-baseline justify-between gap-3 text-xs">
                  <span>{CONSTRAINT_LABELS[constraint]}</span>
                  <span className="tabular font-mono text-muted">{veces}</span>
                </div>
                <div
                  className={`mt-1 h-1 ${isSafetyConstraint(constraint) ? "bg-safety" : "bg-pay"}`}
                  style={{ width: `${Math.max(ancho, 3)}%` }}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Strategy({
  status,
  enVivo,
}: {
  status: AgentStatus | null;
  enVivo: boolean;
}) {
  if (!status) return null;

  // Este panel describe el SERVIDOR, siempre. Cuando se mira un turno
  // grabado, el mapa y el libro hablan del turno y este no: son dos sujetos
  // distintos uno al lado del otro, y sin decirlo se leen como uno solo.
  return (
    <section className={enVivo ? "" : "opacity-60"}>
      <SectionLabel>
        Capa de estrategia
        {!enVivo && " — estado del servidor, no del turno que estás viendo"}
      </SectionLabel>
      <div className="border border-line bg-panel px-4 py-3 text-xs">
        <dl className="grid grid-cols-3 gap-x-4 gap-y-1">
          <Dato etiqueta="Modelo" valor={status.advisor} />
          <Dato etiqueta="Origen" valor={status.strategy_source} />
          <Dato etiqueta="Revisión" valor={String(status.strategy_revision)} />
        </dl>
        <p className="mt-2.5 max-w-[62ch] leading-relaxed text-muted">
          {status.strategy_reasoning}
        </p>
        {status.last_model_error && (
          <p className="mt-2 border-l-2 border-safety pl-2 font-mono text-[11px] text-safety">
            {status.last_model_error}
          </p>
        )}
        <p className="mt-2.5 max-w-[62ch] text-[11px] leading-relaxed text-muted">
          Distancias — {status.distance_model}
        </p>
      </div>
    </section>
  );
}

function Dato({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div>
      <dt className="text-[11px] text-muted">{etiqueta}</dt>
      <dd className="font-mono">{valor}</dd>
    </div>
  );
}

function Replays({
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
    <section>
      <SectionLabel>Turnos grabados</SectionLabel>
      <div className="border border-line bg-panel px-4 py-3">
        {replays.length === 0 ? (
          <p className="text-xs text-muted">
            Ninguno todavía. Graba uno con{" "}
            <code className="font-mono">scripts/replay.py --seed N --record</code>.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {replays.map((replay) => (
              <button
                key={replay.seed}
                onClick={() => onVer(replay.seed)}
                aria-pressed={activo === replay.seed}
                className={`border px-2.5 py-1 font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-go ${
                  activo === replay.seed
                    ? "border-go text-go"
                    : "border-line text-muted hover:border-muted hover:text-text"
                }`}
              >
                seed {replay.seed}
              </button>
            ))}
            {activo !== null && (
              <button
                onClick={onVivo}
                className="border border-line px-2.5 py-1 text-xs text-muted hover:border-muted hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-go"
              >
                Volver a en vivo
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
