"""WebSocket nativo de FastAPI: streaming de estado en vivo (Bloque 6).

Elegido sobre Flask-SocketIO para evitar monkey-patching (eventlet/gevent)
que puede chocar con el thread de background del Bloque 4. Ver
docs/02_Documentacion_Tecnica.md seccion 2.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/state")
async def state_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            # TODO(equipo): leer snapshot() de CourierStateManager (IA + baseline)
            # y hacer await websocket.send_json(...) en cada tick, mas los logs
            # explicables del Bloque 3.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
