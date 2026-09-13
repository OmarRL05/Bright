"""Feed de decisiones y descarga de replays (Bloque 6).

Dos rutas que el dashboard (P2.3) y el replay offline (P2.2) necesitan y que
no existian: el endpoint solo sabia decidir y explicar una decision suelta,
asi que no habia forma de pintar un feed ni de bajarse un turno grabado.

    "Decoupling the simulator from the renderer via an event log is what makes
     replay mode possible. Judges may ask you to reproduce a completed shift
     with the network disabled."
    -- event_log_schema.json

De ahi que el replay se sirva como **archivo JSONL tal cual**, no como JSON
procesado: es el mismo artefacto que pasa `validate_format.py --event-log`, y
el frontend puede reproducirlo sin backend, sin motor y sin red.

Nota sobre la version de Persona 4
-----------------------------------
Esta ruta recoge su idea (`/api/replay/{seed}` sirviendo el .jsonl grabado) y
su convencion de nombres (`replay_seed_{seed}.jsonl`). Cambia dos cosas: vive
en un `APIRouter` montado en la app real en vez de crear un segundo
`FastAPI()` que nunca se montaba, y valida la ruta del archivo antes de
abrirla -- un `seed` que venga por la URL no puede acabar leyendo un archivo
fuera del directorio de replays.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core.agent.journal import JOURNAL, to_dashboard_decision_event

router = APIRouter(tags=["replay"])

#: Donde viven los turnos grabados. Coincide con lo que escribe
#: `scripts/run_evaluation.py --event-log`.
REPLAY_DIR = Path(__file__).resolve().parents[1] / "logs"

#: Tope del feed. El dashboard pinta una lista, no un turno entero.
MAX_FEED_LIMIT = 200


def replay_path(seed: int) -> Path:
    """Ruta del turno grabado para `seed`, dentro de REPLAY_DIR y solo ahi.

    `seed` llega por la URL, asi que se normaliza a entero y se compone el
    nombre nosotros: no hay forma de que un valor de la URL se convierta en
    una ruta arbitraria del disco.
    """
    return REPLAY_DIR / f"replay_seed_{int(seed)}.jsonl"


@router.get("/decisions")
async def decisions(limit: int = 20) -> dict:
    """Ultimas decisiones, de la mas nueva a la mas vieja.

    Es lo que alimenta el feed del dashboard. Cada entrada trae
    `binding_constraint`, que es el punto entero de P2.3: sin el, el feed dice
    "rechazado" y no *por que* -- y distinguir un refusal de seguridad de uno
    de dinero sin leer la prosa es justo lo que el contrato pide.
    """
    limit = max(1, min(limit, MAX_FEED_LIMIT))
    registros = JOURNAL.recent(limit)
    return {
        "count": len(registros),
        "decisions": [to_dashboard_decision_event(r) for r in registros],
    }


@router.get("/replays")
async def replays() -> dict:
    """Turnos grabados disponibles."""
    if not REPLAY_DIR.exists():
        return {"replays": []}
    disponibles = []
    for archivo in sorted(REPLAY_DIR.glob("replay_seed_*.jsonl")):
        try:
            seed = int(archivo.stem.removeprefix("replay_seed_"))
        except ValueError:
            continue
        disponibles.append(
            {"seed": seed, "bytes": archivo.stat().st_size, "url": f"/replay/{seed}"}
        )
    return {"replays": disponibles}


@router.get("/replay/{seed}")
async def replay(seed: int) -> FileResponse:
    """Descarga el event log de un turno grabado, en JSONL crudo.

    Crudo a proposito: es el mismo archivo que pasa
    `validate_format.py --event-log`, asi que el frontend puede reproducirlo
    sin backend y un juez puede validarlo sin pedirnos nada.
    """
    archivo = replay_path(seed)
    if not archivo.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"no hay replay grabado para seed={seed}. "
                f"Grabalo con: python3 scripts/run_evaluation.py --event-log "
                f"logs/replay_seed_{seed}.jsonl --seed {seed}"
            ),
        )
    return FileResponse(
        path=archivo,
        media_type="application/x-ndjson",
        filename=archivo.name,
    )


@router.get("/replay/{seed}/summary")
async def replay_summary(seed: int) -> dict:
    """Resumen de un turno grabado, sin bajarse el archivo entero.

    Cuenta los eventos por tipo y devuelve el `shift_start` y el `shift_end`,
    que es lo que el dashboard necesita para ofrecer el replay antes de
    cargarlo.
    """
    archivo = replay_path(seed)
    if not archivo.exists():
        raise HTTPException(status_code=404, detail=f"no hay replay para seed={seed}")

    conteo: dict[str, int] = {}
    inicio = fin = None
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            evento = json.loads(linea)
        except json.JSONDecodeError:
            continue
        nombre = evento.get("event")
        conteo[nombre] = conteo.get(nombre, 0) + 1
        if nombre == "shift_start":
            inicio = evento
        elif nombre == "shift_end":
            fin = evento

    return {
        "seed": seed,
        "events": conteo,
        "shift_start": inicio,
        "shift_end": fin,
        "url": f"/replay/{seed}",
    }
