"""GET /results -- la tabla de resultados medida, para el dashboard.

Contesta una sola pregunta: **cuanto mas gana el agente que las alternativas**.

Sirve el mismo archivo que los jueces leen (`data/results_table.csv`, formato
del template oficial) mas las comparaciones ya calculadas por
`core.evaluation.report`, para que la pantalla, la lamina y el documento no
puedan citar numeros distintos.

Esto NO es el turno en curso. Son medias sobre 12 turnos held-out y el
dashboard tiene que decirlo asi: la consola ya tuvo tres rotulos que
describian una cosa distinta de la que ensenaban, y no vale la pena sumar un
cuarto. Por eso `meta` viaja en la respuesta -- cuantos turnos, que vehiculo,
que seeds -- y no como prosa escrita a mano en el frontend.

No esta en el fast path: `/decide` nunca lee esto.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from core.evaluation.report import leer_resultados

router = APIRouter(tags=["results"])

DATA = Path(__file__).resolve().parents[1] / "data"

#: Commiteados los dos (~1.5 KB entre ambos): son la evidencia del criterio
#: de Results y tienen que existir en un clon limpio, sin correr nada.
RESULTS_CSV = DATA / "results_table.csv"
RESULTS_META = DATA / "results_table_meta.json"


@router.get("/results")
async def results() -> dict:
    """Tabla de resultados y comparaciones contra los baselines.

    `disponible: false` si la tabla no esta -- el panel dice como generarla
    en vez de romperse. Es evidencia, no fast path.
    """
    return leer_resultados(RESULTS_CSV, RESULTS_META).to_dict()
