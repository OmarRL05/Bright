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

type LatLng = [number, number];

const ROUTE_STYLE = {
  agent: { color: "#10b981", label: "Agente IA" }, // emerald-500
  baseline: { color: "#f59e0b", label: "Baseline" }, // amber-500
} as const;

const ROAD_EVENT_STYLE: Record<RoadEventType, { color: string; label: string }> = {
  closure: { color: "#ef4444", label: "Cierre" },
  traffic: { color: "#f97316", label: "Tráfico" },
  surge: { color: "#8b5cf6", label: "Demanda alta" },
};

const OFFER_COLOR_PALETTE = [
  "#3b82f6",
  "#ec4899",
  "#eab308",
  "#06b6d4",
  "#84cc16",
  "#f43f5e",
  "#a855f7",
  "#14b8a6",
];

function colorForOffer(offerId: string): string {
  let hash = 0;
  for (let i = 0; i < offerId.length; i += 1) {
    hash = (hash << 5) - hash + offerId.charCodeAt(i);
    hash |= 0;
  }
  return OFFER_COLOR_PALETTE[Math.abs(hash) % OFFER_COLOR_PALETTE.length];
}

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
  if (!state || !Array.isArray(state.route) || !Array.isArray(state.backpack)) return [];
  return state.route
    .map((stop) => {
      const coord = resolveStopCoord(stop, state.backpack);
      return coord ? { coord, stop } : null;
    })
    .filter((entry): entry is ResolvedStop => entry !== null);
}

function hasValidPosition(state?: CourierState): state is CourierState {
  return !!state && Array.isArray(state.position) && state.position.length === 2;
}

function isMultiLocation(loc: RoadEvent["location"]): loc is LatLng[] {
  return Array.isArray(loc[0]);
}

async function fetchOsrmRoute(coords: LatLng[]): Promise<LatLng[] | null> {
  if (coords.length < 2) return null;
  const coordsString = coords.map(([lat, lon]) => `${lon},${lat}`).join(";");

  try {
    const response = await fetch(
      `https://router.project-osrm.org/route/v1/driving/${coordsString}?overview=full&geometries=geojson`
    );
    const data = await response.json();
    if (!data.routes || data.routes.length === 0) return null;
    return data.routes[0].geometry.coordinates.map(
      (coord: [number, number]) => [coord[1], coord[0]]
    );
  } catch (error) {
    console.error("Error al consultar OSRM:", error);
    return null;
  }
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

        const fallbackPosition: LatLng = key === "agent" ? [25.6866, -100.3161] : [25.6714, -100.3090];
        const validPosition = hasValidPosition(state) ? state.position : fallbackPosition;
        let resolvedStops = resolveRoute(state);

        if (resolvedStops.length === 0) {
          const mockOffers: Offer[] = [
            {
              id: "ORD-101", pickup: [25.6866, -100.3161], dropoff: [25.6515, -100.2927],
              pay: 180, time_window: [0, 1800], received_at: Date.now() - 600000,
            },
            {
              id: "ORD-103", pickup: [25.6515, -100.2927], dropoff: [25.6326, -100.3088],
              pay: 220, time_window: [0, 2400], received_at: Date.now() - 300000,
            },
          ];
          const mockStops: RouteStop[] = [
            { offer_id: "ORD-101", kind: "pickup", eta: 120 },
            { offer_id: "ORD-101", kind: "dropoff", eta: 300 },
            { offer_id: "ORD-103", kind: "pickup", eta: 450 },
            { offer_id: "ORD-103", kind: "dropoff", eta: 600 },
          ];
          const normalizedState: CourierState = {
            version: state?.version ?? 1, earnings: state?.earnings ?? 1000,
            time_remaining: state?.time_remaining ?? 40, position: validPosition,
            backpack: mockOffers, route: mockStops,
          };
          resolvedStops = resolveRoute(normalizedState);
        }

        // 1. ESTILO DE REPARTIDOR (Vehículo) - Icono de camión estilizado usando HTML/CSS puro
        const courierIcon = L.divIcon({
          className: "courier-marker",
          html: `<div style="background-color: ${style.color}; width: 32px; height: 32px; border-radius: 50%; border: 3px solid white; box-shadow: 0 4px 6px rgba(0,0,0,0.4); display: flex; justify-content: center; align-items: center; font-size: 16px; color: white; z-index: 999; position: relative;">🚚</div>`,
          iconSize: [32, 32],
          iconAnchor: [16, 16],
        });

        L.marker(validPosition, { icon: courierIcon })
          .bindPopup(`<b>${style.label}</b><br>🚚 Posición actual en ruta`)
          .addTo(layerGroup);
        bounds.push(validPosition);

        // 2. ESTILO DE PEDIDOS (Paradas) - Cuadros limpios con "P" (Recolección) y "E" (Entrega)
        resolvedStops.forEach(({ coord, stop }, index) => {
          const offerColor = colorForOffer(stop.offer_id);
          const isPickup = stop.kind === "pickup";

          // Lógica visual: Pickup es un bloque sólido con letra 'P'. Dropoff es fondo blanco con borde de color y letra 'E'.
          const stopIcon = L.divIcon({
            className: "order-marker",
            html: `<div style="background-color: ${isPickup ? offerColor : 'white'}; color: ${isPickup ? 'white' : offerColor}; border: 3px solid ${offerColor}; width: 24px; height: 24px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.3); display: flex; justify-content: center; align-items: center; font-weight: bold; font-family: monospace; font-size: 14px;">${isPickup ? 'P' : 'E'}</div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          });

          L.marker(coord, { icon: stopIcon })
            .bindPopup(
              `<div style="font-family: sans-serif;">
                 <b>${style.label} · Parada ${index + 1}</b><br>
                 <span style="color: ${offerColor}; font-weight: bold;">
                   ${isPickup ? "📦 Recolección (P)" : "📍 Entrega (E)"}
                 </span> — Pedido: ${stop.offer_id}<br>
                 ETA: ${stop.eta}s
               </div>`
            )
            .addTo(layerGroup);

          bounds.push(coord);
        });

        // Trazado de ruta
        let lineCoords: LatLng[] = [validPosition, ...resolvedStops.map((s) => s.coord)];
        const osrmCoords = await fetchOsrmRoute(lineCoords);
        if (osrmCoords) {
          lineCoords = osrmCoords;
        }

        L.polyline(lineCoords, {
          color: style.color,
          weight: 5,
          opacity: 0.85,
          dashArray: key === "baseline" ? "10, 8" : undefined,
        }).addTo(layerGroup);
      }

      // Eventos viales
      roadEvents?.forEach((event) => {
        const style = ROAD_EVENT_STYLE[event.type];
        const tooltip = `${style.label}${event.multiplier ? ` ×${event.multiplier}` : ""}`;

        if (isMultiLocation(event.location)) {
          L.polyline(event.location, {
            color: style.color, weight: 6, opacity: 0.6,
            dashArray: event.type === "closure" ? "4, 6" : undefined,
          }).bindTooltip(tooltip).addTo(layerGroup);
          bounds.push(...event.location);
        } else {
          L.circle(event.location, {
            radius: 250, color: style.color, fillColor: style.color,
            fillOpacity: 0.2, weight: 2,
          }).bindTooltip(tooltip).addTo(layerGroup);
          bounds.push(event.location);
        }
      });

      if (bounds.length > 0) {
        mapInstance.fitBounds(bounds, { padding: [40, 40] });
      }
    });
  }, [isMapReady, agentState, baselineState, roadEvents]);

  return (
    <div className="relative h-full min-h-[400px] w-full overflow-hidden rounded-lg border border-gray-200 shadow-inner dark:border-gray-800">
      <div ref={mapRef} className="absolute inset-0 z-0 h-full w-full" />

      {/* LEYENDA MEJORADA */}
      <div className="absolute bottom-3 left-3 z-[1000] flex gap-4 rounded-md border border-gray-200 bg-white/95 px-4 py-2 text-xs shadow-sm dark:bg-zinc-900/95 dark:border-zinc-700">
        
        {/* Leyenda de Rutas */}
        <div className="flex gap-3">
          {Object.values(ROUTE_STYLE).map((style) => (
            <div key={style.label} className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-4 rounded-full" style={{ backgroundColor: style.color }} />
              <span className="text-gray-600 dark:text-gray-300 font-medium">{style.label}</span>
            </div>
          ))}
        </div>

        {/* Separador */}
        <div className="w-px bg-gray-300 dark:bg-gray-600"></div>

        {/* Leyenda de Paradas */}
        <div className="flex gap-3">
          <div className="flex items-center gap-1.5">
            <span className="flex h-4 w-4 items-center justify-center rounded bg-gray-500 text-[10px] text-white font-bold">P</span>
            <span className="text-gray-600 dark:text-gray-300">Recolección</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="flex h-4 w-4 items-center justify-center rounded border-2 border-gray-500 bg-white dark:bg-transparent text-[10px] text-gray-500 font-bold">E</span>
            <span className="text-gray-600 dark:text-gray-300">Entrega</span>
          </div>
        </div>

      </div>
    </div>
  );
}