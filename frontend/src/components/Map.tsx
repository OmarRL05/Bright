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
  const routeLayerRef = useRef<LayerGroup | null>(null);

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

      // Inicializar el mapa solo una vez (Centrado en Monterrey, N.L.)
      if (!mapInstanceRef.current && mapRef.current) {
        const map = L.map(mapRef.current).setView([25.6866, -100.3161], 13);

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: '&copy; OpenStreetMap contributors',
          maxZoom: 19,
        }).addTo(map);

        mapInstanceRef.current = map;
        routeLayerRef.current = L.layerGroup().addTo(map);
      }
    });

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, []);

  // Efecto para actualizar las rutas viales usando OSRM cuando cambie el estado
  useEffect(() => {
    if (!mapInstanceRef.current || !routeLayerRef.current) return;

    import("leaflet").then(async (L) => {
      const routeLayer = routeLayerRef.current;
      if (!routeLayer) return;

      routeLayer.clearLayers();

      // Coordenadas de ejemplo o tomadas del estado (Ejemplo: Centro de Monterrey a Tecnológico)
      // Si el backend envía paradas reales, se pueden mapear aquí.
      const defaultStart = [-100.3161, 25.6866]; // [lon, lat] para OSRM
      const defaultEnd = [-100.2927, 25.6515];

      try {
        // Petición a OSRM para obtener la ruta exacta por las calles
        const osrmUrl = `https://router.project-osrm.org/route/v1/driving/${defaultStart[0]},${defaultStart[1]};${defaultEnd[0]},${defaultEnd[1]}?overview=full&geometries=geojson`;
        
        const response = await fetch(osrmUrl);
        const data = await response.json();

        if (data.routes && data.routes.length > 0) {
          const coordinates = data.routes[0].geometry.coordinates.map((coord: [number, number]) => [coord[1], coord[0]]); // Invertir a [lat, lon] para Leaflet

          // Dibujar la línea de la ruta ajustada a las calles (Color azul para la IA)
          const polyline = L.polyline(coordinates, {
            color: "#06b6d4", // Cyan brillante
            weight: 5,
            opacity: 0.8,
          });

          routeLayer.addLayer(polyline);
        }
      } catch (error) {
        console.error("Error al consultar la ruta vial en OSRM:", error);
      }
    });
  }, [agentState, baselineState]);

  return (
    <div className="relative h-full min-h-100 w-full overflow-hidden rounded-lg border border-gray-200 shadow-inner dark:border-gray-800">
      <div ref={mapRef} className="absolute inset-0 z-0 h-full w-full" />
    </div>
  );
}