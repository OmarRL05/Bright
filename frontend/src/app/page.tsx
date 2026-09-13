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
    <div className="flex h-full items-center justify-center text-sm text-muted">
      Cargando mapa…
    </div>
  ),
});

/** Tope de latencia que fija el protocolo oficial, en ms. */
const TOPE_LATENCIA_MS = 50;

/**
 * Consola de despacho del agente (Bloque 6, P2.3).
 *
 * Todo lo que se pinta sale de `GET /decisions`, `/status`, `/zones` y
 * `/shocks`. No hay datos de relleno: si el backend no contesta, la pantalla
 * lo dice en vez de enseñar números que no son de nadie. Un dashboard con
 * cifras inventadas es peor que uno vacío en una evaluación donde "¿por qué
 * debería confiar en ese número?" es una de las preguntas escritas.
 *
 * Dos decisiones de forma que no son cosméticas:
 *
 *  - **Cabe en una pantalla.** A partir de `lg` la consola mide exactamente el
 *    alto de la ventana y lo que hace scroll son los paneles, no la página. La
 *    capa de estrategia y los turnos grabados estaban por debajo del pliegue:
 *    en una demo de diez minutos, lo que hay que bajar a buscar no existe.
 *  - **Lo que importa son los rechazos y la regla que los causó**, así que el
 *    libro de decisiones ocupa una columna entera a altura completa y la banda
 *    superior abre con el reparto seguridad / paga / aceptadas en vez de con
 *    cuatro cifras del mismo tamaño.
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
    <div className="flex min-h-dvh flex-col gap-3 bg-ink px-4 py-3 text-text lg:h-dvh lg:overflow-hidden">
      <header className="flex shrink-0 flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-line pb-2.5">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[17px] font-semibold tracking-[-0.015em]">The Courier</h1>
          <span className="text-xs text-muted">consola de despacho</span>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          {/* "En vivo" hacia pensar que hay un turno corriendo de fondo. No lo
              hay: el feed solo se mueve cuando algo postea a /decide. */}
          {enVivo ? (
            <span className="text-muted">Últimas decisiones del servidor</span>
          ) : (
            <span className="border border-line bg-panel px-2 py-0.5 text-muted">
              Turno grabado · <span className="font-mono text-text">seed {replaySeed}</span>
            </span>
          )}
          {status?.degraded && (
            <span className="border border-safety px-2 py-0.5 font-medium text-safety">
              Modo degradado — decidiendo con la última estrategia conocida
            </span>
          )}
          <span className={`flex items-center gap-1.5 ${connected ? "text-go" : "text-alert"}`}>
            <span
              aria-hidden
              className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-go" : "bg-alert"}`}
            />
            {connected ? "Backend conectado" : "Sin backend"}
          </span>
        </div>
      </header>

      {error && (
        <p className="shrink-0 border border-alert px-3 py-2 text-[13px] text-alert">
          No se pudo leer el backend: {error}. Arráncalo con{" "}
          <code className="font-mono">uvicorn main:app --port 8000</code>.
        </p>
      )}

      <Banda decisions={visibles} status={status} enVivo={enVivo} />

      <main className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_28rem]">
        <section className="flex min-h-[22rem] flex-col lg:min-h-0">
          <SectionLabel>
            {enVivo ? "Mapa de la operación" : "Mapa del turno grabado"}
          </SectionLabel>
          <div className="min-h-0 flex-1 border border-line">
            <Map decisions={visibles} live={enVivo} />
          </div>
        </section>

        <section className="flex min-h-[26rem] flex-col lg:min-h-0">
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

      {/* Los tres resúmenes del turno, a una altura fija: nada de esto crece
          tanto como para empujar al mapa, y nada queda por debajo del pliegue. */}
      <footer className="grid shrink-0 gap-3 md:grid-cols-2 lg:h-40 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)_13rem]">
        <Bloqueos decisions={visibles} />
        <Estrategia status={status} enVivo={enVivo} />
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
  return <h2 className="mb-1 shrink-0 text-xs text-muted">{children}</h2>;
}

/**
 * La banda superior: el reparto del turno, y las dos magnitudes que se miden
 * contra algo.
 *
 * No son cuatro cifras iguales en cuatro cajas iguales. Cuatro cajas idénticas
 * dicen "estas cosas son del mismo tipo y ninguna importa más", y aquí no es
 * verdad: lo característico de este agente es **que rechaza trabajo para no
 * romper una regla de seguridad**, así que eso abre la pantalla como una sola
 * barra repartida. Esa barra es además la leyenda de color de toda la consola
 * — el mapa y el libro usan exactamente estos tres tonos, así que ninguno de
 * los dos necesita explicarse aparte.
 */
function Banda({
  decisions,
  status,
  enVivo,
}: {
  decisions: DecisionEvent[];
  status: AgentStatus | null;
  enVivo: boolean;
}) {
  const stats = useMemo(() => {
    let aceptadas = 0;
    let seguridad = 0;
    let paga = 0;
    for (const d of decisions) {
      if (d.decision === "ACCEPT") aceptadas += 1;
      else if (isSafetyConstraint(d.binding_constraint)) seguridad += 1;
      else paga += 1;
    }
    // Solo cuentan las latencias MEDIDAS. Un turno grabado las trae en 0
    // porque el arnés no las mide; promediarlas daría "0.00 ms", que no es una
    // marca excelente sino un dato que no existe.
    const medidas = decisions.map((d) => d.latency_ms).filter((ms) => ms > 0);
    return {
      total: decisions.length,
      aceptadas,
      seguridad,
      paga,
      latenciaMax: medidas.length ? Math.max(...medidas) : null,
    };
  }, [decisions]);

  const tramos = [
    {
      clase: "aceptadas",
      n: stats.aceptadas,
      color: "bg-go",
      texto: "text-go",
      palabra: stats.aceptadas === 1 ? "aceptada" : "aceptadas",
    },
    {
      clase: "seguridad",
      n: stats.seguridad,
      color: "bg-safety",
      texto: "text-safety",
      palabra: stats.seguridad === 1 ? "parada por seguridad" : "paradas por seguridad",
    },
    {
      clase: "paga",
      n: stats.paga,
      color: "bg-pay",
      texto: "text-pay",
      palabra: stats.paga === 1 ? "rechazada por paga" : "rechazadas por paga",
    },
  ].filter((t) => t.n > 0);

  const { latenciaMax } = stats;
  const dentro = latenciaMax !== null && latenciaMax < TOPE_LATENCIA_MS;

  return (
    <section className="grid shrink-0 border border-line bg-panel md:grid-cols-[minmax(0,1fr)_13rem_13rem]">
      <div className="border-line px-4 py-3 md:border-r">
        <p className="text-[13px]">
          <span className="tabular text-2xl font-semibold tracking-[-0.02em]">
            {stats.total}
          </span>{" "}
          <span className="text-muted">
            {stats.total === 1 ? "oferta evaluada" : "ofertas evaluadas"}
            {!enVivo && " en el turno grabado"}
          </span>
        </p>

        {stats.total === 0 ? (
          <div className="mt-2.5 h-2 bg-line" />
        ) : (
          <>
            <div
              className="mt-2.5 flex h-2 gap-px overflow-hidden"
              role="img"
              aria-label={tramos
                .map((t) => `${t.n} ${t.palabra}`)
                .join("; ")}
            >
              {tramos.map((t) => (
                <div
                  key={t.clase}
                  className={t.color}
                  style={{ width: `${(t.n / stats.total) * 100}%` }}
                />
              ))}
            </div>

            <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
              {tramos.map((t) => (
                <li key={t.clase} className="flex items-baseline gap-1.5">
                  <span aria-hidden className={`h-2 w-2 self-center ${t.color}`} />
                  <span className="tabular font-mono font-medium">{t.n}</span>
                  <span className={t.texto}>{t.palabra}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <Escalar
        valor={latenciaMax === null ? "—" : `${latenciaMax.toFixed(2)} ms`}
        etiqueta="Peor latencia"
        tono={latenciaMax === null ? "" : dentro ? "text-go" : "text-alert"}
        nota={
          latenciaMax === null
            ? enVivo
              ? "sin decisiones medidas todavía"
              : "no se mide al grabar un turno"
            : dentro
              ? `${Math.round(TOPE_LATENCIA_MS / latenciaMax)}× por debajo del tope de ${TOPE_LATENCIA_MS} ms`
              : `por encima del tope de ${TOPE_LATENCIA_MS} ms`
        }
        borde
      />

      <Escalar
        valor={status ? `$${status.reservation_wage_mxn_hr.toFixed(0)}` : "—"}
        etiqueta="Salario de reserva"
        nota={status ? "por hora; bajo esto se rechaza" : "sin leer /status"}
      />
    </section>
  );
}

function Escalar({
  valor,
  etiqueta,
  nota,
  tono = "",
  borde = false,
}: {
  valor: string;
  etiqueta: string;
  nota: string;
  tono?: string;
  borde?: boolean;
}) {
  return (
    <div
      className={`border-t border-line px-4 py-3 md:border-t-0 ${borde ? "md:border-r" : ""}`}
    >
      <p className={`tabular text-2xl font-semibold tracking-[-0.02em] ${tono}`}>{valor}</p>
      <p className="mt-0.5 text-[13px]">{etiqueta}</p>
      <p className="text-[11px] leading-snug text-muted">{nota}</p>
    </div>
  );
}

/**
 * Qué constraints están mordiendo, como distribución y no como lista.
 *
 * Es una distribución: tiene forma, y una barra la enseña de un vistazo
 * mientras que una lista con viñetas obliga a comparar cifras a mano. Cada
 * fila es etiqueta · barra · cuenta en la misma línea; antes la cuenta vivía
 * pegada al borde derecho del panel, a casi mil píxeles de su etiqueta, y
 * emparejarlas con la vista costaba más que leer la lista.
 *
 * El color es el mismo de la banda y del libro — ámbar si paró la seguridad,
 * pizarra si pararon las cuentas — así que no necesita leyenda.
 */
function Bloqueos({ decisions }: { decisions: DecisionEvent[] }) {
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
      seguridad: isSafetyConstraint(constraint),
    }));
  }, [decisions]);

  return (
    <section className="flex min-h-0 flex-col">
      <SectionLabel>Qué está bloqueando</SectionLabel>
      <div className="scroll-consola min-h-0 flex-1 overflow-y-auto border border-line bg-panel px-4 py-2.5">
        {filas.length === 0 ? (
          <p className="text-xs text-muted">Ninguna constraint ha mordido todavía.</p>
        ) : (
          <ul className="space-y-1">
            {filas.map(({ constraint, veces, ancho, seguridad }) => (
              <li
                key={constraint}
                className="grid grid-cols-[9.5rem_minmax(0,1fr)_2rem] items-center gap-2.5 text-[11px]"
              >
                <span className="truncate">{CONSTRAINT_LABELS[constraint]}</span>
                {/* El riel deja ver la proporción; una barra suelta sobre el
                    fondo sólo deja comparar barras entre sí. */}
                <span className="h-1.5 bg-line-soft">
                  <span
                    className={`block h-full ${seguridad ? "bg-safety" : "bg-pay"}`}
                    style={{ width: `${Math.max(ancho, 2)}%` }}
                  />
                </span>
                <span className="tabular text-right font-mono text-muted">{veces}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Estrategia({
  status,
  enVivo,
}: {
  status: AgentStatus | null;
  enVivo: boolean;
}) {
  if (!status) return null;

  // Este panel describe el SERVIDOR, siempre. Cuando se mira un turno grabado,
  // el mapa y el libro hablan del turno y este no: son dos sujetos distintos
  // uno al lado del otro, y sin decirlo se leen como uno solo.
  return (
    <section className={`flex min-h-0 flex-col ${enVivo ? "" : "opacity-60"}`}>
      <SectionLabel>
        Capa de estrategia
        {!enVivo && " — estado del servidor, no del turno que estás viendo"}
      </SectionLabel>
      <div className="scroll-consola min-h-0 flex-1 overflow-y-auto border border-line bg-panel px-4 py-2.5 text-xs">
        <dl className="flex flex-wrap gap-x-6 gap-y-1">
          <Dato etiqueta="Modelo" valor={status.advisor} />
          <Dato etiqueta="Origen" valor={status.strategy_source} />
          <Dato etiqueta="Revisión" valor={String(status.strategy_revision)} />
          <Dato etiqueta="Decisiones" valor={String(status.decisions_recorded)} />
        </dl>
        <p className="mt-2 max-w-[70ch] leading-relaxed text-muted">
          {status.strategy_reasoning}
        </p>
        {status.last_model_error && (
          <p className="mt-1.5 border-l-2 border-safety pl-2 font-mono text-[11px] text-safety">
            {status.last_model_error}
          </p>
        )}
        <p className="mt-2 max-w-[70ch] text-[11px] leading-relaxed text-muted">
          Distancias — {status.distance_model}
        </p>
      </div>
    </section>
  );
}

function Dato({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
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
    <section className="flex min-h-0 flex-col">
      <SectionLabel>Turnos grabados</SectionLabel>
      <div className="scroll-consola min-h-0 flex-1 overflow-y-auto border border-line bg-panel px-4 py-2.5">
        {replays.length === 0 ? (
          <p className="text-xs text-muted">
            Ninguno todavía. Graba uno con{" "}
            <code className="font-mono">scripts/replay.py --seed N --record</code>.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {replays.map((replay) => (
              <button
                key={replay.seed}
                type="button"
                onClick={() => onVer(replay.seed)}
                aria-pressed={activo === replay.seed}
                className={`border px-2.5 py-1 font-mono text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-go ${
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
                type="button"
                onClick={onVivo}
                className="border border-line px-2.5 py-1 text-xs text-muted transition-colors hover:border-muted hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-go"
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
