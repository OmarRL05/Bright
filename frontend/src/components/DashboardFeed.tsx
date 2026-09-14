"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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

/** Las tres clases de decisión, que son las tres del código de color. */
type Filtro = "todas" | "aceptadas" | "seguridad" | "paga";

const FILTROS: { id: Filtro; etiqueta: string }[] = [
  { id: "todas", etiqueta: "Todas" },
  { id: "aceptadas", etiqueta: "Aceptadas" },
  { id: "seguridad", etiqueta: "Seguridad" },
  { id: "paga", etiqueta: "Paga" },
];

/** En qué cubeta cae una decisión. Misma regla que decide su color. */
function claseDe(decision: DecisionEvent): Exclude<Filtro, "todas"> {
  if (decision.decision === "ACCEPT") return "aceptadas";
  return isSafetyConstraint(decision.binding_constraint) ? "seguridad" : "paga";
}

/*
 * Todas las clases van LITERALES. Tailwind v4 escanea el texto del fuente en
 * busca de nombres de clase: cualquier cosa construida en tiempo de ejecucion
 * (`"text-" + x`, o un `.replace()` sobre otra clase) no se genera nunca y el
 * elemento sale sin color, sin fallar en build.
 */
const TONO: Record<
  Exclude<Filtro, "todas">,
  { texto: string; regla: string; chip: string; wash: string }
> = {
  aceptadas: { texto: "text-go", regla: "border-l-go", chip: "bg-go", wash: "var(--go-wash)" },
  seguridad: { texto: "text-safety", regla: "border-l-safety", chip: "bg-safety", wash: "var(--safety-wash)" },
  paga: { texto: "text-pay", regla: "border-l-pay", chip: "bg-pay", wash: "var(--pay-wash)" },
};

/** Clave estable de una fila; también sirve para detectar cuáles son nuevas. */
function claveDe(decision: DecisionEvent, index: number): string {
  return `${decision.order_id}-${decision.sim_time ?? index}`;
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
 * Y porque diez segundos tampoco alcanzan para recorrer cincuenta filas a
 * mano, el libro **se filtra por esas mismas tres clases**. La pregunta del
 * juez es casi siempre "enséñame un rechazo de seguridad": eso ahora es un
 * clic en vez de un barrido.
 *
 * Filas planas y sin tarjetas: esto es un registro y un registro se escanea en
 * vertical. Cajas redondeadas separadas romperían ese barrido.
 */
export default function DashboardFeed({
  decisions = [],
  emptyMessage = "Sin decisiones todavía. Manda un ping a POST /decide o corre scripts/demo.py.",
}: DashboardFeedProps) {
  const [filtro, setFiltro] = useState<Filtro>("todas");
  // Memoizado para que el `[]` del caso degenerado no sea un array distinto en
  // cada render y dispare todo lo que depende de `lista`.
  const lista = useMemo(
    () => (Array.isArray(decisions) ? decisions : []),
    [decisions],
  );

  const conteos = useMemo(() => {
    const c = { todas: lista.length, aceptadas: 0, seguridad: 0, paga: 0 };
    for (const d of lista) c[claseDe(d)] += 1;
    return c;
  }, [lista]);

  const visibles = useMemo(
    () => (filtro === "todas" ? lista : lista.filter((d) => claseDe(d) === filtro)),
    [lista, filtro],
  );

  // Una sola tabla de claves, por identidad del objeto: `visibles` contiene
  // las mismas referencias que `lista`, asi que pintar y detectar-novedad leen
  // exactamente la misma clave.
  const claves = useMemo(
    () => new Map(lista.map((d, i) => [d, claveDe(d, i)] as const)),
    [lista],
  );

  const nuevas = useNuevas(lista, claves);

  if (lista.length === 0) {
    return (
      <div className="flex h-full min-h-64 items-center justify-center border border-dashed border-line px-8 text-center text-[13px] leading-relaxed text-muted">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col border border-line bg-panel">
      {/* Fija arriba: al hacer scroll por el registro, el filtro activo tiene
          que seguir a la vista o se pierde de qué subconjunto se está viendo. */}
      <div className="flex flex-wrap items-center gap-1 border-b border-line bg-panel-2 px-2 py-1.5">
        {FILTROS.map(({ id, etiqueta }) => {
          const activo = filtro === id;
          const tono = id === "todas" ? null : TONO[id];
          return (
            <button
              key={id}
              type="button"
              onClick={() => setFiltro(id)}
              aria-pressed={activo}
              className={`flex items-baseline gap-1.5 px-2 py-1 text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-go ${
                activo
                  ? "bg-line text-text"
                  : "text-muted hover:bg-line/50 hover:text-text"
              }`}
            >
              {tono && (
                <span
                  aria-hidden
                  className={`h-2 w-0.5 self-center ${tono.chip}`}
                />
              )}
              {etiqueta}
              <span className="tabular font-mono text-[11px] opacity-70">{conteos[id]}</span>
            </button>
          );
        })}
      </div>

      {visibles.length === 0 ? (
        <p className="flex flex-1 items-center justify-center px-8 text-center text-[13px] text-muted">
          Ninguna de las {conteos.todas} decisiones cayó en esta clase.
        </p>
      ) : (
        <ol className="scroll-consola min-h-0 flex-1 overflow-y-auto">
          {visibles.map((decision) => {
            const clave = claves.get(decision)!;
            return (
              <FeedRow key={clave} decision={decision} nueva={nuevas.has(clave)} />
            );
          })}
        </ol>
      )}
    </div>
  );
}

/**
 * Qué filas acaban de llegar del servidor, para destellarlas una vez.
 *
 * El feed se repuebla cada 1.5 s y hasta ahora las decisiones nuevas aparecían
 * sin avisar: en una demo en vivo nadie nota que el agente acaba de contestar.
 * Es el único movimiento de la consola y responde a un cambio real de estado.
 *
 * Un cambio masivo (cargar un turno grabado) NO destella: cincuenta filas
 * parpadeando a la vez no informan de nada, sólo hacen ruido.
 */
function useNuevas(
  decisions: DecisionEvent[],
  tabla: Map<DecisionEvent, string>,
): Set<string> {
  const [nuevas, setNuevas] = useState<Set<string>>(() => new Set());
  const vistas = useRef<Set<string> | null>(null);

  useEffect(() => {
    const claves = new Set(decisions.map((d) => tabla.get(d)!));
    const previas = vistas.current;
    vistas.current = claves;
    // Primer render: nada es "nuevo", todo lo es.
    if (previas === null) return;

    const entrantes = [...claves].filter((clave) => !previas.has(clave));
    if (entrantes.length === 0 || entrantes.length > 8) return;

    setNuevas(new Set(entrantes));
    const id = setTimeout(() => setNuevas(new Set()), 1200);
    return () => clearTimeout(id);
    // `tabla` se deriva de `decisions`, no hace falta en las deps y meterla
    // solo añadiria una identidad mas que cambia en cada poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decisions]);

  return nuevas;
}

function FeedRow({ decision, nueva }: { decision: DecisionEvent; nueva: boolean }) {
  const clase = claseDe(decision);
  const tono = TONO[clase];
  const constraint = decision.binding_constraint;

  return (
    <li
      // La regla de color: verde si entró, ámbar si la paró la seguridad,
      // pizarra si la pararon las cuentas. Es lo único que hace falta ver para
      // saber de qué clase de decisión se trata.
      className={`border-b border-l-[3px] border-b-line-soft px-3.5 py-2.5 transition-colors hover:bg-panel-2 ${tono.regla} ${
        nueva ? "fila-nueva" : ""
      }`}
      style={nueva ? ({ "--tono-wash": tono.wash } as React.CSSProperties) : undefined}
    >
      <div className="flex items-baseline gap-2.5">
        <time className="tabular font-mono text-[11px] text-muted">
          {formatTime(decision.sim_time)}
        </time>
        <span className="truncate font-mono text-[13px]">{decision.order_id}</span>
        <span className={`ml-auto font-mono text-xs font-medium ${tono.texto}`}>
          {decision.decision}
        </span>
      </div>

      {constraint ? (
        <p className="mt-1.5 flex flex-wrap items-baseline gap-x-1.5 text-xs">
          <span className={tono.texto}>
            {clase === "seguridad" ? "Seguridad" : "Economía"}
          </span>
          <span className="text-muted">— {CONSTRAINT_LABELS[constraint]}</span>
          <code className="border border-line-soft px-1 font-mono text-[11px] text-muted">
            {constraint}
          </code>
        </p>
      ) : (
        <p className={`mt-1.5 text-xs ${tono.texto}`}>
          {clase === "aceptadas" ? "Aceptada" : "Economía"}
        </p>
      )}

      <p className="mt-1 max-w-[62ch] text-[13px] leading-[1.5] text-muted">
        {decision.reason || "Sin motivo registrado"}
      </p>

      <p className="tabular mt-1.5 font-mono text-[11px] text-muted">
        {/* El arnes que graba un turno NO mide latencia: la escribe como 0.0
            porque quien la mide es /decide. Pintar "0.00 ms" seria un numero
            inventado con otro nombre, justo lo que este dashboard evita. */}
        {decision.latency_ms > 0
          ? `${decision.latency_ms.toFixed(2)} ms`
          : "latencia no medida"}{" "}
        · {decision.tier}
        {decision.degraded && <span className="text-safety"> · degradado</span>}
      </p>
    </li>
  );
}

/**
 * Hora de 24 h. El registro venía en 12 h con "p. m." pegado: en un libro de
 * operaciones eso ocupa más y se ordena peor de un vistazo que `15:59:00`.
 */
function formatTime(simTime: string | null): string {
  if (!simTime) return "--:--:--";
  const parsed = new Date(simTime);
  if (Number.isNaN(parsed.getTime())) return simTime;
  return parsed.toLocaleTimeString("es-MX", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}
