"use client";

import { useEffect, useRef } from "react";
import type { Map as LeafletMap, LayerGroup } from "leaflet";
import type { CourierState, RoadEvent } from "@/lib/types";
import "leaflet/dist/leaflet.css";

interface MapProps {
  agentState?: CourierState;
  baselineState?: CourierState;
  roadEvents?: RoadEvent[];
}

export function Map({ agentState, baselineState }: MapProps) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<LeafletMap | null>(null);
  const layerGroupRef = useRef<LayerGroup | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;

    import("leaflet").then((L) => {
      // Configuración de íconos de Leaflet para Next.js
      const iconDefault = L.Icon.Default.prototype as unknown as {
        _getIconUrl?: () => void;
      };
      delete iconDefault._getIconUrl;

      L.Icon.Default.mergeOptions({
        iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });

      // Inicializar el mapa centrado en Monterrey, N.L.
      if (!mapInstanceRef.current && mapRef.current) {
        const map = L.map(mapRef.current).setView([25.6866, -100.3161], 13);

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: '&copy; OpenStreetMap contributors',
          maxZoom: 19,
        }).addTo(map);

        mapInstanceRef.current = map;
        layerGroupRef.current = L.layerGroup().addTo(map);
      }
    });

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, []);

  // Pintar nodos (puntos) y ruta óptima por calles con OSRM
  useEffect(() => {
    if (!mapInstanceRef.current || !layerGroupRef.current) return;

    import("leaflet").then(async (L) => {
      const layerGroup = layerGroupRef.current;
      if (!layerGroup) return;

      layerGroup.clearLayers();

      // 1. Definir los puntos/nodos de la ruta (si el estado trae paradas las usa, si no, usa nodos de ejemplo en Monterrey)
      // Formato esperado: [lat, lon]
      const defaultNodes: [number, number][] = [
        [25.6866, -100.3161], // Centro Monterrey (Orígen)
        [25.6714, -100.3090], // Zona Tec / 5 de Mayo
        [25.6515, -100.2927], // ITESM Campus Monterrey
        [25.6350, -100.2810]  // Destino final
      ];

      // Si tu agentState tiene paradas o ruta, puedes mapearlas aquí. Si está vacío, usa los nodos dummy.
      const nodes = defaultNodes;

      // 2. Colocar los marcadores (puntos/nodos) en el mapa
      nodes.forEach((coord, index) => {
        const marker = L.marker(coord);
        marker.bindPopup(`<b>Punto / Nodo ${index + 1}</b><br>Lat: ${coord[0].toFixed(4)}, Lon: ${coord[1].toFixed(4)}`);
        layerGroup.addLayer(marker);
      });

      // 3. Formatear las coordenadas para OSRM (Requiere formato lon,lat separados por punto y coma)
      const osrmCoordinatesString = nodes.map(coord => `${coord[1]},${coord[0]}`).join(';');

      try {
        // Petición a OSRM para ruta multi-punto optimizada por calles reales
        const osrmUrl = `https://router.project-osrm.org/route/v1/driving/${osrmCoordinatesString}?overview=full&geometries=geojson`;
        
        const response = await fetch(osrmUrl);
        const data = await response.json();

        if (data.routes && data.routes.length > 0) {
          // Invertir coordenadas a [lat, lon] para que Leaflet las pinte bien
          const routeCoordinates = data.routes[0].geometry.coordinates.map((coord: [number, number]) => [coord[1], coord[0]]);

          // Dibujar la línea de la ruta siguiendo exactamente las calles
          const polyline = L.polyline(routeCoordinates, {
            color: "#06b6d4", // Cyan brillante de la IA
            weight: 5,
            opacity: 0.85,
          });

          layerGroup.addLayer(polyline);
        }
      } catch (error) {
        console.error("Error al consultar la ruta multi-punto en OSRM:", error);
      }
    });
  }, [agentState, baselineState]);

  return (
    <div className="relative h-full min-h-100 w-full overflow-hidden rounded-lg border border-gray-200 shadow-inner dark:border-gray-800">
      <div ref={mapRef} className="absolute inset-0 z-0 h-full w-full" />
    </div>
  );
}