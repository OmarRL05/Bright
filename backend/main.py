"""Punto de entrada del backend (Bloque 6).

Correr con:
    uvicorn main:app --reload --port 8000
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.decide import router as decide_router
from api.replay import router as replay_router
from core.agent.strategy import STRATEGY, ClaudeAdvisor


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Conecta la capa de estrategia (tier2) SOLO si hay credencial al arrancar.

    La distincion importa y es facil de perder: `strategy.py` separa a
    proposito "no hay modelo configurado" (NullAdvisor, `degraded=False`) de
    "el modelo se cayo" (`degraded=True`). Conectar ClaudeAdvisor sin tener
    credencial borra esa distincion: el primer refresco falla y **todas** las
    respuestas salen con `degraded: true` desde el primer ping.

    Eso tiene dos costos concretos. Un juez lee `degraded: true` en cada
    respuesta como "este sistema esta operando roto", que es lo contrario de lo
    que el flag quiere decir. Y el ensayo del requisito 7 se pierde: no se
    puede demostrar la transicion sano -> degradado si arranca degradado.

    La credencial se sigue leyendo en CADA llamada, no aqui, asi que el ensayo
    funciona tal como lo describe el protocolo: arrancar con la key, quitarla a
    media corrida, verla degradarse, restaurarla y verla recuperarse.

    Es un `lifespan` y no un `@app.on_event("startup")` porque ese decorador
    esta deprecado y **imprime un aviso en cada arranque de uvicorn** -- ruido
    en la terminal justo cuando alguien puede estar mirandola.
    """
    if os.getenv("ANTHROPIC_API_KEY"):
        STRATEGY.use_advisor(ClaudeAdvisor())
    yield


app = FastAPI(title="The Courier - HackMTY 2026", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(decide_router)
app.include_router(replay_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
