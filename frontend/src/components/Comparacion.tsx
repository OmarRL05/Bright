"use client";

import type { FilaResultado, Resultados } from "@/lib/types";

/*
 * Cuánto gana el agente contra las alternativas.
 *
 * Es la única parte de la consola que NO habla del turno que se está viendo:
 * son medias sobre 12 turnos held-out, medidas de antemano. La pantalla ya
 * tuvo tres rótulos que describían una cosa distinta de la que enseñaban, así
 * que aquí la procedencia va escrita en el propio panel, no en la cabeza de
 * quien presenta.
 *
 * El código de color de la consola NO se usa para las barras. Verde, ámbar y
 * pizarra significan aceptado / lo paró la seguridad / no salieron las
 * cuentas, y una barra de ganancias no es ninguna de las tres. Las barras se
 * distinguen por BRILLO — igual que la demanda en el mapa se codifica con
 * tamaño y opacidad — y el ámbar se reserva para lo que sí es seguridad: las
 * violaciones que cometió cada baseline para llegar a su cifra.
 *
 * Las tres columnas numéricas van a ancho fijo. Es la razón de que esto sea
 * una rejilla y no una fila flexible: con el riel de la barra compartiendo
 * espacio con las cifras, cada barra se mediría contra un riel de largo
 * distinto y dos barras de igual longitud dejarían de significar lo mismo.
 */

interface ComparacionProps {
  resultados: Resultados | null;
  /** El backend no contestó. Distinto de "contestó y no hay tabla". */
  error?: string | null;
}

/** Rejilla compartida por la cabecera y todas las filas. */
const REJILLA = "grid grid-cols-[6.8rem_minmax(0,1fr)_3.4rem_3.6rem] items-center gap-x-2";

/** Miles con espacio fino: `2 083` se escanea mejor en columna que `2,083`. */
function pesos(valor: number): string {
  return Math.round(valor).toLocaleString("es-MX").replace(/,/g, " ");
}

function conSigno(pct: number): string {
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

export default function Comparacion({ resultados, error }: ComparacionProps) {
  if (error) {
    return (
      <Vacio>
        No se pudo leer la comparación: {error}.
      </Vacio>
    );
  }

  if (resultados === null) {
    return <Vacio>Leyendo la tabla de resultados…</Vacio>;
  }

  if (!resultados.disponible || resultados.filas.length === 0) {
    return (
      <Vacio>
        Sin tabla de resultados todavía. Generarla con{" "}
        <code className="font-mono text-text">python scripts/run_evaluation.py</code>
      </Vacio>
    );
  }

  const { filas, comparaciones, captura_del_techo_pct: captura, meta } = resultados;

  const techo = filas.find((f) => f.techo) ?? null;
  const propia = filas.find((f) => f.propia) ?? null;
  // Ascendente: nuestra fila queda abajo, pegada a la línea del techo, y la
  // columna se lee como una escalera en vez de como una lista.
  const barras = [...filas]
    .filter((f) => !f.techo)
    .sort((a, b) => a.mean_earnings_mxn - b.mean_earnings_mxn);

  // La escala la fija el techo, no nuestra barra. Con la nuestra al 100% el
  // panel afirmaría visualmente que no queda nada por ganar, y queda un 7.2%.
  const escala = Math.max(
    techo?.mean_earnings_mxn ?? 0,
    ...filas.map((f) => f.mean_earnings_mxn),
  );

  const titular = comparaciones.find((c) => c.titular) ?? comparaciones[0] ?? null;
  // El baseline más fuerte que además viola: es el remate del panel — mismo
  // dinero, y la diferencia entera está en la columna ámbar.
  const empate = comparaciones.find((c) => !c.titular && c.violaciones_contra > 0);

  return (
    <section className="flex min-h-0 flex-col">
      <SectionLabel>
        Cuánto maximiza{" "}
        <span className="text-muted/70">
          · {meta.shifts ?? filas.length} turnos held-out
          {meta.vehicle ? ` · ${meta.vehicle}` : ""}
          {meta.shift_hours ? ` · ${meta.shift_hours} h` : ""}
        </span>
      </SectionLabel>

      <div className="scroll-consola flex min-h-0 flex-1 flex-col justify-between overflow-y-auto border border-line bg-panel px-3 py-2">
        <div>
          {/* Las unidades se dicen una vez, arriba, y no se repiten en cada
              fila: "440" en ámbar no significa nada sin esta cabecera, y
              "440 viol." doce veces es ruido. */}
          <div className={`${REJILLA} pb-1 text-[10px] text-muted/70`}>
            <span />
            <span />
            <span className="text-right">viol.</span>
            <span className="text-right">MXN/turno</span>
          </div>

          <ol className="flex flex-col gap-[3px]">
            {barras.map((fila) => (
              <Barra
                key={fila.policy}
                fila={fila}
                escala={escala}
                delta={fila.propia ? titular?.delta_pct : undefined}
              />
            ))}
          </ol>

          {techo && (
            // El techo no es un rival: es hasta dónde llega esta familia de
            // políticas con el turno ya conocido. Línea, no barra.
            <div className={`${REJILLA} pt-1 text-[10px] text-muted`}>
              <span className="truncate">{techo.etiqueta}</span>
              <span className="flex items-center gap-1.5">
                <span className="flex-1 border-t border-dashed border-line" />
                {/* Cuánto del techo capturamos, puesto sobre el techo: la
                    cifra vive pegada a lo que describe en vez de en una
                    frase aparte al pie. */}
                {captura !== null && (
                  <span className="shrink-0 font-mono">{captura.toFixed(1)}% capturado</span>
                )}
              </span>
              <span />
              <span className="tabular text-right font-mono">
                {pesos(techo.mean_earnings_mxn)}
              </span>
            </div>
          )}
        </div>

        {/* Dos líneas, no cinco. Las barras ya cuentan el grueso; esto sólo
            dice qué aísla el porcentaje de la barra y por qué el empate de
            arriba no es un empate. */}
        <p className="mt-1.5 border-t border-line-soft pt-1.5 text-[10px] leading-[1.5] text-muted">
          {titular && <>El {conSigno(titular.delta_pct)} es {titular.pregunta}. </>}
          {empate && propia && (
            <>
              {empate.etiqueta_contra} empata en dinero con{" "}
              <span className="text-safety">{empate.violaciones_contra}</span> violaciones;
              nosotros {propia.safety_violations}.
            </>
          )}
        </p>
      </div>
    </section>
  );
}

function Barra({
  fila,
  escala,
  delta,
}: {
  fila: FilaResultado;
  escala: number;
  /** Sólo en nuestra fila: el porcentaje que encabeza el panel. */
  delta?: number;
}) {
  const ancho = escala > 0 ? (fila.mean_earnings_mxn / escala) * 100 : 0;

  return (
    <li className={`${REJILLA} text-[11px]`}>
      <span className={`truncate ${fila.propia ? "text-text" : "text-muted"}`}>
        {fila.etiqueta}
      </span>

      {/* Riel de ancho idéntico en todas las filas: es lo que hace que dos
          barras iguales signifiquen lo mismo. */}
      <span className="flex h-3.5 items-center">
        <span
          // Brillo y no color: ver la nota de la cabecera del archivo.
          // El delta va DENTRO de la barra: nuestra barra ocupa el 93% del
          // riel, asi que un chip a su derecha se saldria a la columna de al
          // lado y rompería la alineación de las cifras.
          className={`flex h-full items-center justify-end overflow-hidden ${
            fila.propia ? "bg-text" : "bg-muted/25"
          }`}
          style={{ width: `${ancho}%` }}
        >
          {delta !== undefined && (
            <span className="px-1 font-mono text-[10px] font-medium text-ink">
              {conSigno(delta)}
            </span>
          )}
        </span>
      </span>

      <span className="tabular text-right font-mono text-[10px] text-safety">
        {fila.safety_violations > 0 ? fila.safety_violations.toLocaleString("es-MX") : ""}
      </span>

      <span className={`tabular text-right font-mono ${fila.propia ? "text-text" : "text-muted"}`}>
        {pesos(fila.mean_earnings_mxn)}
      </span>
    </li>
  );
}

/**
 * Los tres estados en que este panel no tiene barras que dibujar. Alineado
 * arriba y no centrado: centrado en una caja alta deja el texto flotando en
 * medio de la nada y parece un fallo de maquetación.
 */
function Vacio({ children }: { children: React.ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col">
      <SectionLabel>Cuánto maximiza</SectionLabel>
      <div className="min-h-0 flex-1 border border-line bg-panel px-3 py-2.5 text-[11px] leading-[1.6] text-muted">
        {children}
      </div>
    </section>
  );
}

/** Idéntico al de page.tsx: los cuatro paneles del pie rotulan igual. */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-1 shrink-0 text-xs text-muted">{children}</h2>;
}
