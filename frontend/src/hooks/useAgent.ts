"use client";

import { useCallback, useEffect, useState } from "react";
import type {
  AgentStatus,
  DecisionEvent,
  ReplaySummary,
  Resultados,
  ShockInfo,
} from "@/lib/types";

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
 * Shocks vigentes (GET /shocks, sin `at`: todos los registrados en la
 * corrida). Es lo que hace que el mapa reaccione cuando un juez inyecta un
 * surge en vivo con POST /shock -- el requisito de "al menos un shock en
 * vivo durante la demo" no demuestra nada si no se ve en pantalla.
 */
export function useShocks(pollMs = POLL_MS) {
  const [shocks, setShocks] = useState<ShockInfo[]>([]);

  useEffect(() => {
    let cancelado = false;
    const poll = () => {
      fetch(`${API_URL}/shocks`, { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : { active: [] }))
        .then((data: { active: ShockInfo[] }) => {
          if (!cancelado) setShocks(data.active ?? []);
        })
        .catch(() => {
          if (!cancelado) setShocks([]);
        });
    };
    poll();
    const id = setInterval(poll, pollMs);
    return () => {
      cancelado = true;
      clearInterval(id);
    };
  }, [pollMs]);

  return shocks;
}

/**
 * Geometría de calle real por par de zonas, de GET /route.
 *
 * Hay caché en memoria del lado del cliente además de la del backend: no hay
 * razón para volver a pedir un par ya resuelto, ni aunque cambie qué
 * decisiones se muestran. Ausente del objeto = todavía no se pidió.
 *
 * `null` significa "se preguntó y no hay" (503: ni caché ni OSRM), y el mapa
 * lo rotula como línea recta. Es una distinción que vale la pena mantener en
 * el tipo: la versión anterior de este mapa dibujaba una ruta sobre un grafo
 * que no cubría 9 de las 16 zonas y no tenía forma de decir que la línea que
 * enseñaba terminaba a kilómetros del destino.
 */
export interface RutaVial {
  coords: [number, number][];
  /** `cache` (disco, sirve sin red) u `osrm` (recién pedida). */
  source: string;
  distance_km: number;
}

export function useRoutes(pairs: string[]) {
  const [routes, setRoutes] = useState<Record<string, RutaVial | null>>({});

  // `routes` se lee adentro a propósito para no volver a pedir un par ya
  // resuelto; meterlo en las deps del efecto reintroduciría el loop que
  // esto evita, por eso el disable en el arreglo de deps de abajo.
  useEffect(() => {
    const pendientes = pairs.filter((par) => !(par in routes));
    if (pendientes.length === 0) return;

    let cancelado = false;
    for (const par of pendientes) {
      const [fromZone, toZone] = par.split("-");
      fetch(`${API_URL}/route?from_zone=${fromZone}&to_zone=${toZone}`, { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : null))
        .then((data: RutaVial | null) => {
          if (cancelado) return;
          const ruta = data && data.coords?.length >= 2 ? data : null;
          setRoutes((prev) => (par in prev ? prev : { ...prev, [par]: ruta }));
        })
        .catch(() => {
          if (!cancelado) setRoutes((prev) => (par in prev ? prev : { ...prev, [par]: null }));
        });
    }
    return () => {
      cancelado = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairs]);

  return routes;
}

/**
 * Carga un turno grabado y devuelve sus eventos `decision`, de la más nueva
 * a la más vieja (mismo orden que GET /decisions), enriquecidos con
 * `zone_pickup`/`zone_dropoff` -- para que el mapa pueda dibujar la ruta con
 * la MISMA lista que ya consume el feed, sin un segundo tipo de dato.
 *
 * El archivo llega en JSONL crudo -- el mismo que pasa
 * `validate_format.py --event-log` -- asi que reproducir un turno no necesita
 * ni motor ni red, que es justo lo que el protocolo describe como modo
 * replay. `zone_pickup`/`zone_dropoff` no viven en el evento `decision`
 * oficial (event_log_schema.json los deja en `order_offered`, aparte), asi
 * que se unen aqui por `order_id` -- el mismo join que hace
 * `to_dashboard_decision_event` del lado del backend para GET /decisions en
 * vivo.
 */
export async function loadReplay(seed: number): Promise<DecisionEvent[]> {
  const res = await fetch(`${API_URL}/replay/${seed}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`no hay replay para seed ${seed}`);
  const texto = await res.text();

  type LineaCruda = { event?: string; order_id?: string; [campo: string]: unknown };

  const lineas: LineaCruda[] = texto
    .split("\n")
    .filter((linea) => linea.trim().length > 0)
    .map((linea) => JSON.parse(linea) as LineaCruda);

  const zonasPorOrden = new Map<string, { zone_pickup: number; zone_dropoff: number }>();
  for (const evento of lineas) {
    if (evento.event === "order_offered" && typeof evento.order_id === "string") {
      zonasPorOrden.set(evento.order_id, {
        zone_pickup: evento.zone_pickup as number,
        zone_dropoff: evento.zone_dropoff as number,
      });
    }
  }

  return lineas
    .filter((evento) => evento.event === "decision")
    .map((evento) => ({
      ...(evento as unknown as DecisionEvent),
      ...zonasPorOrden.get(evento.order_id as string),
    }))
    .reverse();
}

/**
 * La tabla de resultados medida (GET /results).
 *
 * Se pide UNA vez, no en el poll: son medias sobre 12 turnos held-out
 * guardadas en disco, no algo que cambie mientras la consola está abierta.
 * Meterla en el ciclo de 1.5 s daría la impresión contraria — y esa
 * impresión es justo la que este panel tiene que evitar.
 */
export function useResults() {
  const [resultados, setResultados] = useState<Resultados | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelado = false;
    fetch(`${API_URL}/results`, { cache: "no-store" })
      .then(async (res) => {
        if (cancelado) return;
        // Un 404 y una tabla sin generar NO son lo mismo, y el panel tiene
        // que decir cuál de las dos es. Un 404 aquí significa casi siempre
        // un backend arrancado antes de que /results existiera, y un panel
        // que en ese caso diga "corre la evaluación" manda a reejecutar algo
        // que ya está hecho.
        if (!res.ok) {
          setError(
            res.status === 404
              ? "este backend no expone /results — reinicia uvicorn"
              : `el backend respondió ${res.status}`,
          );
          return;
        }
        setResultados((await res.json()) as Resultados);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelado) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelado = true;
    };
  }, []);

  return { resultados, error };
}
