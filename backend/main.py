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

app = FastAPI(title="The Courier - HackMTY 2026")

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
