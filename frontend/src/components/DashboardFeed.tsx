import React from 'react';

// 1. Le agregamos "any[]" para que TypeScript no marque error
// 2. Lo renombramos a DashboardFeed para que coincida con tu importación
export default function DashboardFeed({ events = [] }) {
  
  // 3. BLINDAJE: Si por alguna razón events no es un arreglo, mostramos un mensaje en lugar de crashear
  if (!Array.isArray(events)) {
    return (
      <div className="bg-gray-900 text-gray-500 p-4 rounded-md h-96 border border-gray-700 text-sm font-mono flex items-center justify-center">
        Esperando eventos de la simulación...
      </div>
    );
  }

  return (
    <div className="bg-gray-900 text-white p-4 rounded-md h-96 overflow-y-auto font-mono text-sm border border-gray-700 shadow-lg">
      <h3 className="text-lg font-bold mb-4 text-cyan-400 uppercase tracking-wider">
        Logix-Router AI Feed
      </h3>
      <ul>
        {events.map((ev, index) => {
          if (ev.event !== 'decision') return null;
          
          const isAccepted = ev.decision === 'ACCEPT';
          
          return (
            <li key={index} className="mb-3 pb-3 border-b border-gray-800">
              <span className="text-gray-500">[{ev.sim_time?.split('T')[1] || "00:00:00"}] </span>
              <span className="font-bold text-gray-200">{ev.order_id} </span>
              
              {isAccepted ? (
                <span className="text-green-400 font-bold ml-2">ACEPTADO</span>
              ) : (
                <div className="mt-1 pl-4 border-l-2 border-red-500">
                  <span className="text-red-500 font-bold">RECHAZADO</span>
                  <span className="text-orange-400 text-xs block mt-1">
                    [!] binding_constraint: {ev.binding_constraint || "N/A"}
                  </span>
                  <span className="text-gray-400 text-xs block">
                    Motivo: {ev.reason}
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