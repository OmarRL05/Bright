"use client";

import { useEffect, useState } from "react";
import type { Zone } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Catálogo de zonas para el mapa (GET /zones). Se pide una sola vez: las 16
 * zonas de `core.models.DEFAULT_ZONE_MAP` no cambian durante una corrida, a
 * diferencia de las decisiones o los shocks.
 */
export function useZones() {
  const [zones, setZones] = useState<Zone[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelado = false;
    fetch(`${API_URL}/zones`, { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`backend respondió ${res.status}`);
        return res.json() as Promise<{ zones: Zone[] }>;
      })
      .then((data) => {
        if (!cancelado) setZones(data.zones ?? []);
      })
      .catch((err) => {
        if (!cancelado) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelado = true;
    };
  }, []);

  return { zones, error };
}
