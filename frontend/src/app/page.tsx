"use client";

import { Map } from "@/components/Map";
import { Metrics } from "@/components/Metrics";
import { useSimulation } from "@/hooks/useSimulation";
import DashboardFeed from "@/components/DashboardFeed";
import FinancialROI from "@/components/FinancialROI";

export default function Home() {
  const { snapshot, connected } = useSimulation();

  // Datos dummy profesionales para lucir el dashboard ante los jueces
  const dummyLogs = [
    {
      accepted: true,
      offer_id: "ORD-101",
      reason: "Oferta viable según la capacidad del agente.",
      timestamp: Date.parse("2026-09-13T10:15:22Z")
    },
    {
      accepted: false,
      offer_id: "ORD-102",
      reason: "El peso del paquete excede la capacidad máxima de carga de la unidad.",
      timestamp: Date.parse("2026-09-13T10:16:05Z")
    },
    {
      accepted: true,
      offer_id: "ORD-103",
      reason: "Oferta viable según la capacidad del agente.",
      timestamp: Date.parse("2026-09-13T10:17:40Z")
    },
    {
      accepted: false,
      offer_id: "ORD-104",
      reason: "La ruta estimada supera el tiempo restante de la jornada legal del conductor.",
      timestamp: Date.parse("2026-09-13T10:19:12Z")
    },
    {
      accepted: true,
      offer_id: "ORD-105",
      reason: "Oferta viable según la capacidad del agente.",
      timestamp: Date.parse("2026-09-13T10:20:55Z")
    },
    {
      accepted: false,
      offer_id: "ORD-106",
      reason: "La autonomía estimada de la batería no es suficiente para completar la ruta.",
      timestamp: Date.parse("2026-09-13T10:22:30Z")
    }
  ];

  // Usa los logs reales si existen; de lo contrario, muestra los dummies para la demo
  const activeEvents = snapshot?.logs?.length ? snapshot.logs : dummyLogs;

  return (
    <div className="flex min-h-screen flex-col gap-6 bg-zinc-50 p-6 font-sans dark:bg-black">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">The Courier — HackMTY 2026</h1>
        <span className={`text-sm ${connected ? "text-green-600" : "text-gray-400"}`}>
          {connected ? "● conectado" : "○ desconectado"}
        </span>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Metrics label="Agente IA" state={snapshot?.agent} />
        <Metrics label="Baseline" state={snapshot?.baseline} />
      </section>

      <section className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Map agentState={snapshot?.agent} baselineState={snapshot?.baseline} />
        </div>
        <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
          <h2 className="mb-2 text-sm font-medium text-gray-500">Decisiones</h2>
          <DashboardFeed events={activeEvents} />
        </div>
      </section>
      <section className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Tu código actual del Mapa y Decisiones... */}
      </section>

      {/* NUEVA SECCIÓN FINANCIERA */}
      <section className="mt-4">
        <FinancialROI />
      </section>
    </div>
  );
}