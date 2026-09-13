"use client";

import "leaflet/dist/leaflet.css";

import { useEffect, useMemo } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";
import { useRoutes, useShocks } from "@/hooks/useAgent";
import { useZones } from "@/hooks/useZones";
import {
  isSafetyConstraint,
  SHOCK_COLORS,
  type DecisionEvent,
  type Zone,
} from "@/lib/types";

interface MapProps {
  /** De la más nueva a la más vieja, igual que GET /decisions (mismo dato que el feed). */
  decisions: DecisionEvent[];
  /** Solo hay shocks vigentes para consultar en vivo; un replay grabado no los expone por HTTP. */
  live: boolean;
}

const MONTERREY_CENTER: [number, number] = [25.68, -100.31];

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

/** Color de una zona por demanda: frío (poca) a cálido (mucha) — mismo dato que usa la economía en /decide. */
function demandColor(score: number): string {
  const hue = 210 - 210 * Math.min(1, Math.max(0, score)); // 210=azul frío, 0=rojo cálido
  return `hsl(${hue}, 75%, 55%)`;
}

/** Ajusta el encuadre una vez que las 16 zonas llegan, sin pelearse con el zoom del usuario después. */
function FitToZones({ zones }: { zones: Zone[] }) {
  const leafletMap = useMap();
  useEffect(() => {
    if (zones.length === 0) return;
    const bounds: [number, number][] = zones.map((z) => z.coord);
    leafletMap.fitBounds(bounds, { padding: [24, 24] });
  }, [zones, leafletMap]);
  return null;
}

/**
 * Mapa con la ruta activa, la demanda por zona y los shocks en vivo (Bloque 6).
 *
 * Reconstruye la ruta a partir de `decisions` en vez de un `position_update`
 * en vivo: ese evento existe en el contrato oficial pero nada lo produce
 * todavía (ver docs/03_Integracion_API_Decide.md) -- la aproximación
 * honesta es "la última entrega aceptada", no una posición GPS.
 */
export function Map({ decisions, live }: MapProps) {
  const { zones, error: zonesError } = useZones();
  const shocks = useShocks();

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

  // Geometria real sobre calles (Bloque 5, grafo de Monterrey) para cada
  // par zona->zona que aparece en la ruta. Si el .graphml no esta
  // descargado o el par no tiene camino, el hook devuelve null y se cae a
  // la linea recta de siempre -- ver el fallback abajo, en el render.
  const paresDeRuta = useMemo(
    () => [...new Set(aceptadas.map((d) => `${d.zone_pickup}-${d.zone_dropoff}`))],
    [aceptadas],
  );
  const rutasReales = useRoutes(paresDeRuta);

  return (
    <div className="relative h-full min-h-96 w-full overflow-hidden rounded-lg border border-gray-200 dark:border-gray-800">
      <MapContainer
        center={MONTERREY_CENTER}
        zoom={11}
        scrollWheelZoom
        className="h-full w-full"
        style={{ background: "#0a0a0a" }}
      >
        <TileLayer url={BASE_TILE_URL} attribution={TILE_ATTRIBUTION} />
        <TileLayer url={LABELS_TILE_URL} />
        <FitToZones zones={zones} />

        {zones.map((zone) => (
          <CircleMarker
            key={zone.zone_id}
            center={zone.coord}
            radius={5 + zone.demand_score * 9}
            pathOptions={{
              color: zone.flagged ? "#f43f5e" : demandColor(zone.demand_score),
              weight: zone.flagged ? 2 : 1,
              fillColor: demandColor(zone.demand_score),
              fillOpacity: 0.45,
              dashArray: zone.flagged ? "3 2" : undefined,
            }}
          >
            <Tooltip direction="top" offset={[0, -4]}>
              <span className="font-medium">{zone.name}</span>
              <br />
              zone_id {zone.zone_id} · demanda {zone.demand_score.toFixed(2)}
              {zone.flagged && (
                <>
                  <br />
                  <span className="text-rose-500">zona marcada (toque de queda 22:00)</span>
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
              // Linea recta mientras se resuelve o si no hay grafo vial
              // disponible (real === null) -- ver useRoutes.
              positions={real && real.length > 0 ? real : [pickup.coord, dropoff.coord]}
              pathOptions={{
                color: "#10b981",
                weight: esLaUltima ? 3 : 1.5,
                opacity: esLaUltima ? 0.95 : 0.35,
              }}
            >
              <Tooltip sticky>
                {decision.order_id}: {pickup.name} → {dropoff.name}
                {!real && <><br /><span className="text-gray-400">línea recta — sin grafo vial</span></>}
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
                color: safety ? "#f59e0b" : "#9ca3af",
                weight: 1.5,
                fillOpacity: 0,
                dashArray: "2 2",
              }}
            >
              <Tooltip sticky>
                SKIP {decision.order_id} · {decision.binding_constraint ?? "paga insuficiente"}
              </Tooltip>
            </CircleMarker>
          );
        })}

        {live &&
          shocks.map((shock, i) => {
            const zone = shock.zone !== undefined ? zoneById[shock.zone] : undefined;
            if (!zone) return null;
            return (
              <CircleMarker
                key={`shock-${i}-${shock.sim_time}`}
                center={zone.coord}
                radius={18}
                pathOptions={{
                  color: SHOCK_COLORS[shock.shock_type],
                  weight: 2,
                  fillColor: SHOCK_COLORS[shock.shock_type],
                  fillOpacity: 0.15,
                }}
              >
                <Tooltip direction="top">
                  {shock.shock_type}
                  {shock.multiplier ? ` ${shock.multiplier.toFixed(1)}x` : ""} · {zone.name}
                </Tooltip>
              </CircleMarker>
            );
          })}

        {posicionActual && (
          <CircleMarker
            center={posicionActual.coord}
            radius={7}
            pathOptions={{ color: "#ffffff", weight: 2, fillColor: "#10b981", fillOpacity: 1 }}
          >
            <Tooltip permanent direction="right" offset={[8, 0]} className="!bg-transparent !border-0 !shadow-none">
              <span className="text-xs font-medium text-emerald-400">
                última entrega · {posicionActual.name}
              </span>
            </Tooltip>
          </CircleMarker>
        )}
      </MapContainer>

      <MapLegend />

      {zonesError && (
        <div className="absolute inset-x-0 top-0 bg-rose-950/90 px-3 py-1.5 text-center text-xs text-rose-200">
          No se pudieron cargar las zonas: {zonesError}
        </div>
      )}
    </div>
  );
}

function MapLegend() {
  return (
    <div className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-black/70 px-3 py-2 text-[11px] leading-relaxed text-gray-200 backdrop-blur-sm">
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-4 rounded-sm bg-emerald-500" /> ruta aceptada
      </div>
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-2 rounded-full border border-amber-500" /> rechazo de seguridad
      </div>
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-2 rounded-full border border-gray-400" /> rechazo por paga
      </div>
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-2 rounded-full border border-dashed border-rose-500" /> zona marcada
      </div>
    </div>
  );
}
