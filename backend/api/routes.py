"""Endpoints REST para controlar la simulacion (Bloque 6).

Ver docs/01_Arquitectura.md seccion 3 (Bloque 6).
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["simulation"])


@router.post("/simulation/start")
async def start_simulation() -> dict:
    """TODO(equipo): instanciar SimulationEngine + CourierStateManager
    (agente IA y baseline) y arrancar el loop del hilo principal.
    """
    raise NotImplementedError


@router.post("/simulation/pause")
async def pause_simulation() -> dict:
    """TODO(equipo): pausar el loop de simulacion sin perder el estado."""
    raise NotImplementedError


@router.get("/simulation/state")
async def get_state() -> dict:
    """TODO(equipo): devolver snapshot() de ambos agentes (IA y baseline)."""
    raise NotImplementedError
