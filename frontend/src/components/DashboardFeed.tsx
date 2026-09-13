import type { DecisionLog } from "@/lib/types";

interface DashboardFeedProps {
  events?: DecisionLog[];
}

export default function DashboardFeed({ events = [] }: DashboardFeedProps) {
  if (!Array.isArray(events) || events.length === 0) {
    return (
      <div className="flex h-96 items-center justify-center rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-400">
        Esperando eventos de la simulación...
      </div>
    );
  }

  return (
    <div className="h-96 overflow-y-auto rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
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
              className="mb-3 border-b border-gray-100 pb-3 last:mb-0 last:border-b-0 last:pb-0"
            >
              <span className="text-xs text-gray-400">[{time}] </span>
              <span className="text-sm font-semibold text-gray-900">
                {log.offer_id}{" "}
              </span>

              {log.accepted ? (
                <span className="ml-1 text-sm font-semibold text-green-600">
                  ACEPTADO
                </span>
              ) : (
                <div className="mt-1 border-l-2 border-red-300 pl-3">
                  <span className="text-sm font-semibold text-red-500">
                    RECHAZADO
                  </span>
                  <span className="mt-1 block text-xs text-gray-500">
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