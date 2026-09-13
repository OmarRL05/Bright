"use client";

import { useEffect, useRef, useState } from "react";
import type { CourierState, Offer, RoadEvent, RoadEventType, RouteStop } from "@/lib/types";
import type { Map as LeafletMap, LayerGroup } from "leaflet";
import "leaflet/dist/leaflet.css";

interface MapProps {
  agentState?: CourierState;
  baselineState?: CourierState;
  roadEvents?: RoadEvent[];
}

// NOTA: pickup/dropoff/location no traen unidad explícita en lib/types.ts.
// Asumo [lat, lon] (consistente con cómo ya se usaba en este componente
// antes). Si Bloque 3/5 en realidad emiten [lon, lat] (convención GeoJSON),
// hay que invertir aquí.
type LatLng = [number, number];

// Estilo de la LÍNEA de ruta: identifica de quién es la ruta (agente vs.
// baseline), no el pedido.
const ROUTE_STYLE = {
  agent: { color: "#10b981", label: "Agente IA" }, // emerald-500
  baseline: { color: "#f59e0b", label: "Baseline" }, // amber-500
} as const;

const ROAD_EVENT_STYLE: Record<RoadEventType, { color: string; label: string }> = {
  closure: { color: "#ef4444", label: "Cierre" }, // red-500
  traffic: { color: "#f97316", label: "Tráfico" }, // orange-500
  surge: { color: "#8b5cf6", label: "Demanda alta" }, // violet-500
};

// Paleta para colorear cada PARADA según el pedido (offer_id) al que
// pertenece — independiente de a qué ruta (agente/baseline) pertenezca.
const OFFER_COLOR_PALETTE = [
  "#3b82f6", // blue-500
  "#ec4899", // pink-500
  "#eab308", // yellow-500
  "#06b6d4", // cyan-500
  "#84cc16", // lime-500
  "#f43f5e", // rose-500
  "#a855f7", // purple-500
  "#14b8a6", // teal-500
];

function colorForOffer(offerId: string): string {
  let hash = 0;
  for (let i = 0; i < offerId.length; i += 1) {
    hash = (hash << 5) - hash + offerId.charCodeAt(i);
    hash |= 0;
  }
  return OFFER_COLOR_PALETTE[Math.abs(hash) % OFFER_COLOR_PALETTE.length];
}

/** Resuelve la coordenada de un RouteStop buscando su Offer en backpack. */
function resolveStopCoord(stop: RouteStop, backpack: Offer[]): LatLng | null {
  const offer = backpack.find((o) => o.id === stop.offer_id);
  if (!offer) return null;
  return stop.kind === "pickup" ? offer.pickup : offer.dropoff;
}

interface ResolvedStop {
  coord: LatLng;
  stop: RouteStop;
}

function resolveRoute(state?: CourierState): ResolvedStop[] {
  if (!state) return [];
  return state.route
    .map((stop) => {
      const coord = resolveStopCoord(stop, state.backpack);
      return coord ? { coord, stop } : null;
    })
    .filter((entry): entry is ResolvedStop => entry !== null);
}

/** true si `position` tiene coordenadas usables (evita [0,0] por defecto sin inicializar). */
function hasValidPosition(state?: CourierState): state is CourierState {
  return !!state && Array.isArray(state.position) && state.position.length === 2;
}

function isMultiLocation(loc: RoadEvent["location"]): loc is LatLng[] {
  return Array.isArray(loc[0]);
}

async function fetchOsrmRoute(coords: LatLng[]): Promise<LatLng[] | null> {
  if (coords.length < 2) return null;

  const coordsString = coords.map(([lat, lon]) => `${lon},${lat}`).join(";");

  const response = await fetch(
    `https://router.project-osrm.org/route/v1/driving/${coordsString}?overview=full&geometries=geojson`
  );
  const data = await response.json();

  if (!data.routes || data.routes.length === 0) return null;

  return data.routes[0].geometry.coordinates.map(
    (coord: [number, number]) => [coord[1], coord[0]]
  );
}

export function Map({ agentState, baselineState, roadEvents }: MapProps) {
  const mapRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<LeafletMap | null>(null);
  const layerGroupRef = useRef<LayerGroup | null>(null);
  const [isMapReady, setIsMapReady] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let cancelled = false;

    import("leaflet").then((L) => {
      if (cancelled) return;

      if (!mapInstanceRef.current && mapRef.current) {
        const map = L.map(mapRef.current).setView([25.6866, -100.3161], 12);

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: "&copy; OpenStreetMap contributors",
          maxZoom: 19,
        }).addTo(map);

        mapInstanceRef.current = map;
        layerGroupRef.current = L.layerGroup().addTo(map);
        setIsMapReady(true);
      }
    });

    return () => {
      cancelled = true;
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
      layerGroupRef.current = null;
      setIsMapReady(false);
    };
  }, []);

  useEffect(() => {
    if (!isMapReady) return;

    const mapInstance = mapInstanceRef.current;
    const layerGroup = layerGroupRef.current;
    if (!mapInstance || !layerGroup) return;

    import("leaflet").then(async (L) => {
      layerGroup.clearLayers();

      const bounds: LatLng[] = [];

      const routes: { key: keyof typeof ROUTE_STYLE; state?: CourierState }[] = [
        { key: "agent", state: agentState },
        { key: "baseline", state: baselineState },
      ];

      for (const { key, state } of routes) {
        const style = ROUTE_STYLE[key];
        const resolvedStops = resolveRoute(state);
        const hasPosition = hasValidPosition(state);
        if (resolvedStops.length === 0 && !hasPosition) continue;

        if (hasPosition) {
          L.circleMarker(state.position, {
            radius: 10,
            color: style.color,
            weight: 3,
            fillColor: "#111827", // gray-900: distingue "vehículo" de las paradas de color por pedido
            fillOpacity: 1,
          })
            .bindPopup(`<b>${style.label}</b><br>Posición actual`)
            .addTo(layerGroup);

          bounds.push(state.position);
        }

        resolvedStops.forEach(({ coord, stop }, index) => {
          const offerColor = colorForOffer(stop.offer_id);
          const isPickup = stop.kind === "pickup";

          L.circleMarker(coord, {
            radius: 8,
            color: offerColor,
            weight: isPickup ? 3 : 2,
            fillColor: offerColor,
            // Relleno sólido = pickup, relleno tenue (anillo) = dropoff.
            fillOpacity: isPickup ? 1 : 0.15,
          })
            .bindPopup(
              `<b>${style.label} · parada ${index + 1}</b><br>` +
                `${isPickup ? "Recolección" : "Entrega"} — ${stop.offer_id}<br>` +
                `ETA: ${stop.eta}s`
            )
            .addTo(layerGroup);

          bounds.push(coord);
        });

        let lineCoords: LatLng[] = hasPosition
          ? [state.position, ...resolvedStops.map((s) => s.coord)]
          : resolvedStops.map((s) => s.coord);
        try {
          const osrmCoords = await fetchOsrmRoute(lineCoords);
          if (osrmCoords) lineCoords = osrmCoords;
        } catch (error) {
          console.error(`Error consultando OSRM para ${style.label}, usando línea recta:`, error);
        }

        L.polyline(lineCoords, {
          color: style.color,
          weight: 5,
          opacity: 0.85,
          dashArray: key === "baseline" ? "10, 8" : undefined,
        }).addTo(layerGroup);
      }

      roadEvents?.forEach((event) => {
        const style = ROAD_EVENT_STYLE[event.type];
        const tooltip = `${style.label}${event.multiplier ? ` ×${event.multiplier}` : ""}`;

        if (isMultiLocation(event.location)) {
          L.polyline(event.location, {
            color: style.color,
            weight: 6,
            opacity: 0.6,
            dashArray: event.type === "closure" ? "4, 6" : undefined,
          })
            .bindTooltip(tooltip)
            .addTo(layerGroup);
          bounds.push(...event.location);
        } else {
          L.circle(event.location, {
            radius: 250,
            color: style.color,
            fillColor: style.color,
            fillOpacity: 0.2,
            weight: 2,
          })
            .bindTooltip(tooltip)
            .addTo(layerGroup);
          bounds.push(event.location);
        }
      });

      if (bounds.length > 0) {
        mapInstance.fitBounds(bounds, { padding: [40, 40] });
      }
    });
  }, [isMapReady, agentState, baselineState, roadEvents]);

  return (
    <div className="relative h-full min-h-[400px] w-full rounded-lg border border-gray-200 overflow-hidden dark:border-gray-800 shadow-inner">
      <div ref={mapRef} className="absolute inset-0 h-full w-full z-0" />

      <div className="absolute bottom-3 left-3 z-[1000] flex gap-3 rounded-md border border-gray-200 bg-white/95 px-3 py-2 text-xs shadow-sm">
        {Object.values(ROUTE_STYLE).map((style) => (
          <div key={style.label} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-4 rounded-full"
              style={{ backgroundColor: style.color }}
            />
            <span className="text-gray-600">{style.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}