from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import os

app = FastAPI()

# ... (tus otras rutas) ...

@app.get("/api/replay/{seed}")
async def get_replay_file(seed: int):
    """
    Endpoint de emergencia offline (P2.2).
    El frontend llama a esta ruta para descargar el historial completo
    de un turno y reproducirlo sin necesidad de correr el motor ni usar red.
    """
    # Construimos la ruta donde el ReplayLogger guarda los archivos
    filepath = f"logs/replay_seed_{seed}.jsonl"
    
    # Verificamos si el archivo del turno realmente existe
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"No se encontró el replay para la semilla {seed}")
        
    # FileResponse envía el archivo físico directamente al frontend de forma muy eficiente
    return FileResponse(
        path=filepath, 
        media_type="application/jsonlines", 
        filename=f"replay_seed_{seed}.jsonl"
    )