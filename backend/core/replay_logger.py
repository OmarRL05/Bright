import os

class ReplayLogger:
    """
    Gestiona la escritura continua de eventos en un archivo .jsonl 
    para poder reproducir el turno offline (P2.2).
    """
    def __init__(self, seed: int, output_dir: str = "logs"):
        self.seed = seed
        self.output_dir = output_dir
        # El archivo se nombrará usando la semilla, ej: replay_seed_42.jsonl
        self.filepath = os.path.join(self.output_dir, f"replay_seed_{self.seed}.jsonl")
        
        # Crea la carpeta de logs si no existe
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Modo 'w' inicial para limpiar cualquier archivo viejo con la misma semilla
        with open(self.filepath, 'w', encoding='utf-8') as f:
            pass

    def log_event(self, serialized_event: str):
        """
        Escribe el string JSON en una nueva línea y fuerza el guardado 
        inmediato en el disco duro.
        """
        # Usamos modo 'a' (append) para agregar sin borrar lo anterior
        with open(self.filepath, 'a', encoding='utf-8') as f:
            f.write(serialized_event + "\n")
            
            # Estas dos líneas son la clave para evitar pérdida de datos si hay crasheo
            f.flush()
            os.fsync(f.fileno())

# === Ejemplo de cómo se conectan las Tareas 1 y 2 ===
# from serializer import EventSerializer
#
# # 1. Al iniciar el simulador, creas el logger con la semilla del turno
# logger = ReplayLogger(seed=1)
# 
# # 2. Cuando ocurre un evento (ej. decisión del agente) lo formateas
# evento_texto = EventSerializer.format_decision(
#     order_id="ORD-0003", decision="ACCEPT", reason="Rentable", 
#     latency_ms=15, sim_time="2026-09-12T19:10:00"
# )
#
# # 3. Lo guardas inmediatamente en el archivo
# logger.log_event(evento_texto)