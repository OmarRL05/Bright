"use client";

import { Feed } from "@/components/Feed";
import { Map } from "@/components/Map";
import { Metrics } from "@/components/Metrics";
import { useSimulation } from "@/hooks/useSimulation";

export default function Home() {
  const { snapshot, connected } = useSimulation();

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
          <Feed logs={snapshot?.logs ?? []} />
        </div>
      </section>
    </div>
  );
}
