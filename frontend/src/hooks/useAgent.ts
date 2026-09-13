"use client";

import { useCallback, useEffect, useState } from "react";
import type { AgentStatus, DecisionEvent, ReplaySummary } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Cada cuanto se refresca el feed. */
const POLL_MS = 1500;

/**
 * Lee el estado real del agente por REST.
 *
 * Reemplaza al WebSocket `/ws/state`, que sigue siendo un stub: acepta la
 * conexion y no envia nada nunca, asi que `snapshot` se quedaba en null para
 * siempre. Esa es la razon por la que el dashboard habia acabado con datos
 * inventados dentro del componente.
 *
 * Estas dos rutas si contestan de verdad hoy:
 *   GET /decisions?limit=N   feed con binding_constraint
 *   GET /status              modo degradado y parametros de estrategia
 *
 * `error` no es cosmetico: un dashboard que no distingue "no hay datos" de
 * "no puedo hablar con el backend" es exactamente como se acaba enseñando
 * una pantalla bonita con numeros que no son de nadie.
 */
export function useAgent(limit = 25) {
  const [decisions, setDecisions] = useState<DecisionEvent[]>([]);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [replays, setReplays] = useState<ReplaySummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [feedRes, statusRes] = await Promise.all([
        fetch(`${API_URL}/decisions?limit=${limit}`, { cache: "no-store" }),
        fetch(`${API_URL}/status`, { cache: "no-store" }),
      ]);
      if (!feedRes.ok || !statusRes.ok) {
        throw new Error(`backend respondio ${feedRes.status}/${statusRes.status}`);
      }
      const feed = (await feedRes.json()) as { decisions: DecisionEvent[] };
      setDecisions(feed.decisions ?? []);
      setStatus((await statusRes.json()) as AgentStatus);
      setConnected(true);
      setError(null);
    } catch (err) {
      setConnected(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [limit]);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    fetch(`${API_URL}/replays`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : { replays: [] }))
      .then((data: { replays: ReplaySummary[] }) => setReplays(data.replays ?? []))
      .catch(() => setReplays([]));
  }, []);

  return { decisions, status, replays, connected, error, refresh };
}

/**
 * Carga un turno grabado y devuelve sus eventos `decision`.
 *
 * El archivo llega en JSONL crudo -- el mismo que pasa
 * `validate_format.py --event-log` -- asi que reproducir un turno no necesita
 * ni motor ni red, que es justo lo que el protocolo describe como modo
 * replay.
 */
export async function loadReplay(seed: number): Promise<DecisionEvent[]> {
  const res = await fetch(`${API_URL}/replay/${seed}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`no hay replay para seed ${seed}`);
  const texto = await res.text();

  return texto
    .split("\n")
    .filter((linea) => linea.trim().length > 0)
    .map((linea) => JSON.parse(linea) as { event?: string })
    .filter((evento): evento is DecisionEvent => evento.event === "decision")
    .reverse();
}
