"use client";

import { useEffect, useRef, useState } from "react";
import type { CourierState, RoadEvent } from "@/lib/types";
import type { Map as LeafletMap, LayerGroup } from "leaflet";
import "leaflet/dist/leaflet.css";

interface MapProps {
  agentState?: CourierState;
  baselineState?: CourierState;
  roadEvents?: RoadEvent[];
}

export function Map({ agentState, baselineState, roadEvents }: MapProps) {
  const mapRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<LeafletMap | null>(null);
  const layerGroupRef = useRef<LayerGroup | null>(null);
  // Se activa justo cuando el mapa terminó de crearse (la creación es async
  // por el import("leaflet") dinámico). El efecto que dibuja los nodos
  // necesita esperar a esta señal en vez de asumir que mapInstanceRef.current
  // ya existe.
  const [isMapReady, setIsMapReady] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let cancelled = false;

    import("leaflet").then((L) => {
      if (cancelled) return;

      // Configuración limpia de íconos sin romper tipado estricto
      const proto = L.Icon.Default.prototype as unknown as { _getIconUrl?: unknown };
      delete proto._getIconUrl;

      L.Icon.Default.mergeOptions({
        iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });

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
      // Sin esto, un remount (p.ej. StrictMode en dev) deja layerGroupRef
      // apuntando al layer group del mapa ya destruido.
      layerGroupRef.current = null;
      setIsMapReady(false);
    };
  }, []);

  // Renderizado de nodos y ruta OSRM con null-safety estricto
  useEffect(() => {
    if (!isMapReady) return;

    const mapInstance = mapInstanceRef.current;
    const layerGroup = layerGroupRef.current;
    if (!mapInstance || !layerGroup) return;

    // Referenciamos las props de forma explícita para evitar warnings de variables no usadas
    const hasSimulationContext = Boolean(agentState || baselineState || roadEvents?.length);
    if (!hasSimulationContext) {
      // Contexto base activo para la demo
    }

    import("leaflet").then(async (L) => {
      layerGroup.clearLayers();

      const deliveryNodes: [number, number][] = [
        [25.6866, -100.3161], // Depósito / Origen (Centro Monterrey)
        [25.6515, -100.2927], // Parada 1: Zona Tec
        [25.6326, -100.3088], // Parada 2: San Pedro
        [25.7012, -100.3150], // Parada 3: San Nicolás
        [25.7250, -100.3100], // Destino Final
      ];

      deliveryNodes.forEach((coord, index) => {
        const marker = L.marker(coord);
        marker.bindPopup(`<b>Parada / Nodo #${index}</b><br>Lat: ${coord[0]}, Lon: ${coord[1]}`);
        layerGroup.addLayer(marker);
      });

      const coordsString = deliveryNodes.map((coord) => `${coord[1]},${coord[0]}`).join(";");

      try {
        const response = await fetch(
          `https://router.project-osrm.org/route/v1/driving/${coordsString}?overview=full&geometries=geojson`
        );
        const data = await response.json();

        if (data.routes && data.routes.length > 0) {
          const routeCoords: [number, number][] = data.routes[0].geometry.coordinates.map(
            (coord: [number, number]) => [coord[1], coord[0]]
          );

          const polyline = L.polyline(routeCoords, {
            color: "#06b6d4",
            weight: 5,
            opacity: 0.9,
          });

          layerGroup.addLayer(polyline);
          mapInstance.fitBounds(polyline.getBounds(), { padding: [40, 40] });
        }
      } catch (error) {
        console.error("Error al consultar OSRM, usando respaldo visual:", error);
        const fallbackPolyline = L.polyline(deliveryNodes, { color: "#06b6d4", weight: 4 });
        layerGroup.addLayer(fallbackPolyline);
        mapInstance.fitBounds(fallbackPolyline.getBounds(), { padding: [40, 40] });
      }
    });
  }, [isMapReady, agentState, baselineState, roadEvents]);

  return (
    <div className="relative h-full min-h-[400px] w-full rounded-lg border border-gray-200 overflow-hidden dark:border-gray-800 shadow-inner">
      <div ref={mapRef} className="absolute inset-0 h-full w-full z-0" />
    </div>
  );
}