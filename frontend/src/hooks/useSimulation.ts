"use client";

import { useEffect, useRef, useState } from "react";
import type { SimulationSnapshot } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/state";

/**
 * Se conecta al WebSocket de estado en vivo del backend (Bloque 6) y
 * mantiene el ultimo snapshot recibido (agente IA + baseline + logs).
 *
 * TODO(equipo): manejar reconexion y el mensaje inicial de "simulacion no
 * iniciada" una vez el backend defina el formato exacto de los mensajes.
 */
export function useSimulation() {
  const [snapshot, setSnapshot] = useState<SimulationSnapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const socket = new WebSocket(WS_URL);
    socketRef.current = socket;

    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data) as SimulationSnapshot;
      setSnapshot(data);
    };

    return () => socket.close();
  }, []);

  return { snapshot, connected };
}
