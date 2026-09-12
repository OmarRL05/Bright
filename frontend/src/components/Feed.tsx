import type { DecisionLog } from "@/lib/types";

interface FeedProps {
  logs: DecisionLog[];
}

/** Feed de logs explicables de decisiones del agente (Bloque 3 → Bloque 6). */
export function Feed({ logs }: FeedProps) {
  if (logs.length === 0) {
    return <p className="text-sm text-gray-500">Sin decisiones todavía.</p>;
  }

  return (
    <ul className="flex flex-col gap-1 overflow-y-auto text-sm">
      {logs.map((log, i) => (
        <li
          key={`${log.offer_id}-${log.timestamp}-${i}`}
          className={log.accepted ? "text-green-600" : "text-red-600"}
        >
          {log.accepted ? "✓" : "✗"} [{log.offer_id}] {log.reason}
        </li>
      ))}
    </ul>
  );
}
