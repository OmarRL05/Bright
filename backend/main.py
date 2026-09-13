"""Punto de entrada del backend (Bloque 6).

Correr con:
    uvicorn main:app --reload --port 8000
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.decide import router as decide_router
from api.routes import router as rest_router
from api.sockets import router as ws_router
from core.agent.strategy import STRATEGY, ClaudeAdvisor

app = FastAPI(title="The Courier - HackMTY 2026")


@app.on_event("startup")
async def _connect_strategy_layer() -> None:
    """Conecta la capa de estrategia (tier2) si hay credencial en el entorno.

    Sin esto, STRATEGY se queda con NullAdvisor: el sistema decide igual de
    bien, pero `degraded` nunca es verdad porque no hay modelo que se caiga, y
    el requisito 7 del protocolo queda sin demostrar.

    Se conecta el advisor aunque la key no este puesta ahora mismo: la key se
    lee en CADA llamada, no aqui, asi que exportarla despues de arrancar el
    proceso tambien funciona -- y quitarla a media corrida dispara el modo
    degradado, que es exactamente lo que los jueces hacen.
    """
    STRATEGY.use_advisor(ClaudeAdvisor())

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(decide_router)
app.include_router(rest_router)
app.include_router(ws_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
