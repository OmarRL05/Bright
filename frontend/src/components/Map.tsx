"use client";

import "leaflet/dist/leaflet.css";

import { useCallback, useEffect, useMemo, useRef } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";
import { useRoutes, useShocks } from "@/hooks/useAgent";
import { useZones } from "@/hooks/useZones";
import { isSafetyConstraint, type DecisionEvent, type Zone } from "@/lib/types";

interface MapProps {
  /** De la más nueva a la más vieja, igual que GET /decisions (mismo dato que el feed). */
  decisions: DecisionEvent[];
  /** Solo hay shocks vigentes para consultar en vivo; un replay grabado no los expone por HTTP. */
  live: boolean;
}

const MONTERREY_CENTER: [number, number] = [25.68, -100.31];

/** Con más halos que esto a la vez, las etiquetas fijas estorban más que informan. */
const MAX_SHOCKS_ROTULADOS = 4;

// Esri "Dark Gray Canvas", gratis y sin llave -- server.arcgisonline.com es
// el servicio REST clasico de Esri, de acceso anonimo para este nivel de
// uso. CartoDB (lo que usaba antes) dejo de servir sus tiles anonimas hace
// poco: ahora responden 200 con una imagen real, pero esa imagen es una
// marca de agua repetida que dice "API KEY REQUIRED" -- por eso el mapa se
// veia con ese texto encima aunque el codigo nunca pedia ninguna llave.
// Verificado con curl antes de este cambio: Esri sigue devolviendo tiles de
// verdad, no un placeholder.
//
// Dos capas porque el estilo "Dark Gray" de Esri separa el fondo (relieve/
// bloques de ciudad) de las calles y etiquetas -- sin la segunda, el mapa
// queda casi en blanco.
const BASE_TILE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const LABELS_TILE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}";
const TILE_ATTRIBUTION =
  'Tiles &copy; <a href="https://www.esri.com">Esri</a> — Esri, HERE, Garmin, © OpenStreetMap contributors';

/**
 * Las señales de la consola, leídas de los tokens CSS.
 *
 * Leaflet pinta con atributos SVG (`stroke="..."`), y un atributo no resuelve
 * `var(--go)`: necesita el hex literal. Copiarlos aquí a mano es justo cómo
 * este mapa acabó pintado con la paleta de Tailwind (emerald/rose/gray)
 * mientras el resto de la pantalla usaba otra — y con una leyenda que, por
 * tanto, describía colores que el libro de decisiones no usaba. Así que se
 * leen de `:root` en tiempo de ejecución: una sola fuente, en el sitio donde
 * viven los tokens.
 *
 * Los valores de respaldo son sólo para que nada quede invisible si el CSS aún
 * no aplicó; el componente se monta con `ssr: false`, así que en la práctica
 * siempre hay hoja de estilos.
 */
const RESPALDO = {
  ink: "#0e1418",
  go: "#55be8e",
  safety: "#e8a93c",
  pay: "#8fa9ba",
  muted: "#8da0ac",
  line: "#22303a",
  text: "#dce6eb",
  shock: "#4fc3e8",
  alert: "#e77268",
} as const;

type Senales = typeof RESPALDO;

function leerSenales(): Senales {
  if (typeof window === "undefined") return RESPALDO;
  const raiz = getComputedStyle(document.documentElement);
  const salida = { ...RESPALDO } as Record<keyof Senales, string>;
  for (const nombre of Object.keys(RESPALDO) as (keyof Senales)[]) {
    salida[nombre] = raiz.getPropertyValue(`--${nombre}`).trim() || RESPALDO[nombre];
  }
  return salida as Senales;
}

/**
 * Encuadra las 16 zonas, y vuelve a hacerlo cuando el panel cambia de tamaño.
 *
 * Antes sólo encuadraba al llegar las zonas. El mapa ahora crece con la
 * ventana, así que ese único cálculo salía con el tamaño equivocado y en
 * pantallas bajas arrancaba con zonas fuera de vista. No se pelea con el zoom
 * del usuario: sólo reacciona a que cambie el contenedor, no al scroll ni al
 * arrastre.
 */
function FitToZones({ zones }: { zones: Zone[] }) {
  const leafletMap = useMap();
  // El observador de tamaño se suscribe una vez y vive mas que cualquier
  // render, asi que lee las zonas de un ref en vez de capturarlas. Este efecto
  // va PRIMERO: los efectos corren en orden de declaracion, de modo que el de
  // abajo ya encuentra el ref al dia.
  const ultimas = useRef<Zone[]>(zones);
  useEffect(() => {
    ultimas.current = zones;
  }, [zones]);

  const encuadrar = useCallback(() => {
    const actuales = ultimas.current;
    if (actuales.length === 0) return;
    leafletMap.fitBounds(
      actuales.map((z) => z.coord),
      { padding: [24, 24] },
    );
  }, [leafletMap]);

  useEffect(() => {
    encuadrar();
  }, [zones, encuadrar]);

  useEffect(() => {
    const observador = new ResizeObserver(() => {
      leafletMap.invalidateSize();
      encuadrar();
    });
    observador.observe(leafletMap.getContainer());
    return () => observador.disconnect();
  }, [leafletMap, encuadrar]);

  return null;
}

/**
 * Mapa con la ruta activa, la demanda por zona y los shocks en vivo (Bloque 6).
 *
 * Reconstruye la ruta a partir de `decisions` en vez de un `position_update`
 * en vivo: ese evento existe en el contrato oficial pero nada lo produce
 * todavía (ver docs/03_Integracion_API_Decide.md) -- la aproximación
 * honesta es "la última entrega aceptada", no una posición GPS.
 *
 * **El color aquí significa exactamente lo mismo que en el resto de la
 * consola**: verde entró, ámbar lo paró la seguridad, pizarra no salieron las
 * cuentas. Por eso las zonas se quedaron sin color propio — llevaban una rampa
 * azul→rojo que metía un eje cromático de más y cuyo extremo rojo chocaba con
 * "sin backend". La demanda es contexto, no veredicto, así que se codifica con
 * tamaño y opacidad y deja el color entero para las decisiones.
 */
export function Map({ decisions, live }: MapProps) {
  const { zones, error: zonesError } = useZones();
  const shocks = useShocks();
  // Se monta con ssr:false, asi que aqui ya hay hoja de estilos aplicada.
  const senales = useMemo(() => leerSenales(), []);

  // Objeto y no `new Map()` a proposito: el nombre `Map` ya lo ocupa este
  // mismo componente (ver el mismo comentario en app/page.tsx), y la
  // colision compila a `any` en silencio en vez de fallar en build.
  const zoneById = useMemo(() => {
    const byId: Record<number, Zone> = {};
    for (const zone of zones) byId[zone.zone_id] = zone;
    return byId;
  }, [zones]);

  // De la más vieja a la más nueva: así se dibuja la ruta en el orden en
  // que de verdad ocurrió, no al revés.
  const cronologico = useMemo(() => [...decisions].reverse(), [decisions]);

  const aceptadas = useMemo(
    () =>
      cronologico.filter(
        (d) =>
          d.decision === "ACCEPT" &&
          d.zone_pickup !== undefined && d.zone_pickup !== null && d.zone_pickup in zoneById &&
          d.zone_dropoff !== undefined && d.zone_dropoff !== null && d.zone_dropoff in zoneById,
      ),
    [cronologico, zoneById],
  );

  const rechazadas = useMemo(
    () =>
      cronologico.filter(
        (d) => d.decision === "SKIP" && d.zone_pickup !== undefined && d.zone_pickup !== null && d.zone_pickup in zoneById,
      ),
    [cronologico, zoneById],
  );

  const ultimaEntrega = aceptadas.length > 0 ? aceptadas[aceptadas.length - 1] : null;
  const posicionActual = ultimaEntrega ? zoneById[ultimaEntrega.zone_dropoff!] : null;
  const ultimaRecoleccion = ultimaEntrega ? zoneById[ultimaEntrega.zone_pickup!] : null;

  // Geometría de calle real (OSRM, servida desde la caché en disco) para cada
  // par zona->zona de la ruta. Si el par no está cacheado y además no hay red,
  // el hook devuelve null y se cae a la línea recta -- rotulada como tal en el
  // tooltip, ver el fallback abajo en el render.
  const paresDeRuta = useMemo(
    () => [...new Set(aceptadas.map((d) => `${d.zone_pickup}-${d.zone_dropoff}`))],
    [aceptadas],
  );
  const rutasReales = useRoutes(paresDeRuta);

  const shocksEnZona = live ? shocks.filter((s) => s.zone !== undefined && zoneById[s.zone]) : [];
  const rotularShocks = shocksEnZona.length <= MAX_SHOCKS_ROTULADOS;

  return (
    // Sin borde ni radio propios: el contenedor de page.tsx ya pone el filete,
    // y los dos juntos daban una caja redondeada dentro de otra cuadrada.
    <div className="relative h-full w-full overflow-hidden">
      <MapContainer
        center={MONTERREY_CENTER}
        zoom={11}
        scrollWheelZoom
        className="h-full w-full"
      >
        <TileLayer url={BASE_TILE_URL} attribution={TILE_ATTRIBUTION} />
        <TileLayer url={LABELS_TILE_URL} />
        <FitToZones zones={zones} />

        {zones.map((zone) => (
          <CircleMarker
            key={zone.zone_id}
            center={zone.coord}
            // Tamaño y opacidad llevan la demanda; el tono no cambia nunca.
            radius={4 + zone.demand_score * 10}
            pathOptions={{
              color: zone.flagged ? senales.safety : senales.line,
              weight: zone.flagged ? 1.5 : 1,
              fillColor: senales.muted,
              fillOpacity: 0.1 + zone.demand_score * 0.28,
              dashArray: zone.flagged ? "3 2" : undefined,
            }}
          >
            <Tooltip direction="top" offset={[0, -4]}>
              <span className="font-medium">{zone.name}</span>
              <br />
              <span className="font-mono">zone_id {zone.zone_id}</span> · demanda{" "}
              {zone.demand_score.toFixed(2)}
              {zone.flagged && (
                <>
                  <br />
                  <span className="text-safety">zona marcada · toque de queda 22:00</span>
                </>
              )}
            </Tooltip>
          </CircleMarker>
        ))}

        {aceptadas.map((decision, i) => {
          const pickup = zoneById[decision.zone_pickup!];
          const dropoff = zoneById[decision.zone_dropoff!];
          const par = `${decision.zone_pickup}-${decision.zone_dropoff}`;
          const real = rutasReales[par];
          const esLaUltima = i === aceptadas.length - 1;
          return (
            <Polyline
              key={`${decision.order_id}-ruta`}
              // Línea recta mientras se resuelve, o si el par no está en la
              // caché y tampoco hay red (real === null) -- ver useRoutes.
              positions={real ? real.coords : [pickup.coord, dropoff.coord]}
              pathOptions={{
                color: senales.go,
                weight: esLaUltima ? 3 : 1.5,
                opacity: esLaUltima ? 0.95 : 0.35,
              }}
            >
              <Tooltip sticky>
                <span className="font-mono">{decision.order_id}</span>
                <br />
                {pickup.name} → {dropoff.name}
                <br />
                {/* La línea dice siempre qué es. Un trazo que sigue calles y
                    un trazo que las ignora se parecen demasiado como para
                    dejar que el juez adivine cuál está viendo. */}
                {real ? (
                  <span className="text-muted">
                    calle real · {real.distance_km.toFixed(1)} km
                  </span>
                ) : (
                  <span className="text-muted">línea recta — sin geometría vial</span>
                )}
              </Tooltip>
            </Polyline>
          );
        })}

        {rechazadas.map((decision) => {
          const pickup = zoneById[decision.zone_pickup!];
          const safety = isSafetyConstraint(decision.binding_constraint);
          return (
            <CircleMarker
              key={`${decision.order_id}-skip`}
              center={pickup.coord}
              radius={4}
              pathOptions={{
                color: safety ? senales.safety : senales.pay,
                weight: 1.5,
                fillOpacity: 0,
                dashArray: "2 2",
              }}
            >
              <Tooltip sticky>
                <span className={safety ? "text-safety" : "text-pay"}>
                  {safety ? "Seguridad" : "Economía"}
                </span>{" "}
                · <span className="font-mono">{decision.order_id}</span>
                <br />
                <span className="font-mono">
                  {decision.binding_constraint ?? "reservation_wage"}
                </span>
              </Tooltip>
            </CircleMarker>
          );
        })}

        {shocksEnZona.map((shock, i) => {
          const zone = zoneById[shock.zone!];
          return (
            <CircleMarker
              key={`shock-${i}-${shock.sim_time}`}
              center={zone.coord}
              radius={18}
              pathOptions={{
                color: senales.shock,
                weight: 2,
                fillColor: senales.shock,
                fillOpacity: 0.12,
              }}
            >
              {/* El tipo va escrito, no codificado en el color: un juez inyecta
                  un shock en vivo y tiene que verlo sin consultar leyenda. */}
              <Tooltip
                permanent={rotularShocks}
                direction="top"
                offset={[0, -18]}
                className={rotularShocks ? "!border-shock" : undefined}
              >
                <span className="text-shock">{shock.shock_type}</span>
                {shock.multiplier ? ` ${shock.multiplier.toFixed(1)}×` : ""} · {zone.name}
              </Tooltip>
            </CircleMarker>
          );
        })}

        {/* El viaje activo se lee entero: de dónde salió, por qué calles y
            dónde terminó. Antes sólo estaba marcado el final, así que la
            línea gruesa no tenía principio y había que deducirlo del trazo.
            Sólo el último — treinta pares de marcadores sobre un turno
            completo tapan el mapa que intentan explicar. */}
        {ultimaRecoleccion && (
          <CircleMarker
            center={ultimaRecoleccion.coord}
            radius={6}
            pathOptions={{
              color: senales.go,
              weight: 2,
              fillColor: senales.ink,
              fillOpacity: 1,
            }}
          >
            <Tooltip direction="left" offset={[-8, 0]}>
              <span className="text-go">recolección · {ultimaRecoleccion.name}</span>
            </Tooltip>
          </CircleMarker>
        )}

        {posicionActual && (
          <CircleMarker
            center={posicionActual.coord}
            radius={7}
            pathOptions={{
              color: senales.text,
              weight: 2,
              fillColor: senales.go,
              fillOpacity: 1,
            }}
          >
            <Tooltip permanent direction="right" offset={[8, 0]}>
              <span className="text-go">última entrega · {posicionActual.name}</span>
            </Tooltip>
          </CircleMarker>
        )}
      </MapContainer>

      <MapLegend />

      {zonesError && (
        <div className="sobre-mapa absolute inset-x-0 top-0 border-b border-alert bg-ink/90 px-3 py-1.5 text-center text-xs text-alert">
          No se pudieron cargar las zonas: {zonesError}
        </div>
      )}
    </div>
  );
}

/**
 * Leyenda de lo que SÓLO existe en el mapa.
 *
 * No repite el código de color de la consola (verde / ámbar / pizarra): eso ya
 * lo enseña la banda de arriba con sus cuentas, y repetirlo aquí sería pedir
 * que se aprenda dos veces. Quedan las tres codificaciones que son propias del
 * mapa y no aparecen en ninguna otra parte.
 *
 * Antes esto no se veía en absoluto: iba sin `z-index` y los paneles de
 * Leaflet (hasta 700) la tapaban entera. `.sobre-mapa` la pone encima.
 */
function MapLegend() {
  return (
    <div className="sobre-mapa pointer-events-none absolute bottom-2 left-2 border border-line bg-ink/85 px-2.5 py-1.5 text-[11px] leading-relaxed text-muted backdrop-blur-sm">
      <div className="flex items-center gap-2">
        <span className="inline-flex w-4 items-center justify-center gap-0.5">
          <span className="h-1.5 w-1.5 rounded-full bg-muted/25" />
          <span className="h-2.5 w-2.5 rounded-full bg-muted/50" />
        </span>
        círculo más grande, más demanda
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-flex w-4 items-center justify-center">
          <span className="h-2.5 w-2.5 rounded-full border border-dashed border-safety" />
        </span>
        zona marcada de noche
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-flex w-4 items-center justify-center">
          <span className="h-3 w-3 rounded-full border border-shock bg-shock/15" />
        </span>
        shock activo
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-flex w-4 items-center justify-center gap-0.5">
          <span className="h-2 w-2 rounded-full border border-go bg-ink" />
          <span className="h-px w-1.5 bg-go" />
          <span className="h-2 w-2 rounded-full bg-go" />
        </span>
        recolección → entrega
      </div>
    </div>
  );
}
