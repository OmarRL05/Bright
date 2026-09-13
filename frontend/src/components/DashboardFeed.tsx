import type { DecisionLog } from "@/lib/types";

interface DashboardFeedProps {
  events?: DecisionLog[];
}

export default function DashboardFeed({ events = [] }: DashboardFeedProps) {
  if (!Array.isArray(events) || events.length === 0) {
    return (
      <div className="flex h-96 items-center justify-center rounded-md border border-gray-700 bg-gray-900 p-4 text-sm font-mono text-gray-500">
        Esperando eventos de la simulación...
      </div>
    );
  }

  return (
    <div className="h-96 overflow-y-auto rounded-md border border-gray-700 bg-gray-900 p-4 font-mono text-sm text-white shadow-lg">
      <h3 className="mb-4 text-lg font-bold uppercase tracking-wider text-cyan-400">
        Logix-Router AI Feed
      </h3>

      <ul>
        {events.map((log, index) => {
          const time = new Date(log.timestamp).toLocaleTimeString("es-MX", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          });

          return (
            <li
              key={`${log.offer_id}-${log.timestamp}-${index}`}
              className="mb-3 border-b border-gray-800 pb-3"
            >
              <span className="text-gray-500">[{time}] </span>
              <span className="font-bold text-gray-200">{log.offer_id} </span>

              {log.accepted ? (
                <span className="ml-2 font-bold text-green-400">ACEPTADO</span>
              ) : (
                <div className="mt-1 border-l-2 border-red-500 pl-4">
                  <span className="font-bold text-red-500">RECHAZADO</span>
                  <span className="mt-1 block text-xs text-gray-400">
                    Motivo: {log.reason || "Sin motivo especificado"}
                  </span>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}