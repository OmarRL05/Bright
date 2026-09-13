import json
from typing import Any, Dict, Optional

class EventSerializer:
    """
    Formatea los eventos del simulador para cumplir estrictamente con el
    event_log_schema.json (formato plano, llave 'event', 'sim_time').
    """

    @staticmethod
    def format_event(event_name: str, sim_time: str, event_data: Dict[str, Any]) -> str:
        """
        Crea un JSON string de una sola línea válido para el archivo .jsonl.
        El schema exige que los datos vayan "planos" (al mismo nivel).
        """
        base_event = {
            "event": event_name,
            "sim_time": sim_time
        }
        
        # Hacemos un merge (unión) de las llaves base y los datos específicos del evento
        full_event = {**base_event, **event_data}

        # json.dumps asegura que se convierta en una sola línea de texto sin saltos
        return json.dumps(full_event)

    @staticmethod
    def format_decision(
        order_id: str, 
        decision: str, 
        reason: str, 
        latency_ms: int,
        sim_time: str,
        binding_constraint: Optional[str] = None
    ) -> str:
        """
        Formateador para las decisiones del agente (Bloque 3).
        Asegura que el 'binding_constraint' siempre vaya para el dashboard (P2.3).
        Nota: 'decision' típicamente debe ser "ACCEPT" o "SKIP".
        """
        event_data = {
            "order_id": order_id,
            "decision": decision,
            "reason": reason,
            "latency_ms": latency_ms
        }
        
        # Solo agregamos el constraint si existe (ej. para rechazos)
        if binding_constraint:
            event_data["binding_constraint"] = binding_constraint

        return EventSerializer.format_event(
            event_name="decision", 
            sim_time=sim_time, 
            event_data=event_data
        )

# === Ejemplo de cómo lo llamará el Bloque 3 ===
# serializado = EventSerializer.format_decision(
#     order_id="ORD-0002",
#     decision="SKIP",
#     reason="Costo marginal excede pago por $20",
#     latency_ms=12,
#     sim_time="2026-09-12T19:07:00",
#     binding_constraint="reservation_wage"
# )
# print(serializado)