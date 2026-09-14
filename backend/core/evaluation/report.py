"""Lee la tabla de resultados publicada y calcula las comparaciones.

Por que esto vive en el backend y no en el dashboard
----------------------------------------------------
"Nuestro agente gana un 29.7% mas" es la afirmacion central del proyecto, y
depende por completo de CONTRA QUE se compara. Elegir el contrincante es una
decision de evaluacion, no de presentacion: si el dashboard la tomara por su
cuenta, la lamina, el documento y la pantalla podrian acabar citando tres
numeros distintos, todos ciertos y ninguno el mismo.

Asi que las comparaciones se definen aqui, una sola vez, y el dashboard
dibuja lo que se le diga.

Las tres comparaciones, y que contesta cada una
-----------------------------------------------
Ninguna sobra, porque cada una aisla una cosa distinta:

1. **Contra `AcceptAll`** -- cuanto vale tener un agente. Es el default
   literal: aceptar todo lo que llegue. Es la comparacion mas halagadora y
   por eso NO es el titular: `AcceptAll` ademas comete 5318 violaciones de
   seguridad, asi que no es una alternativa que alguien pudiera operar.

2. **Contra `GreedyRateSafe`** -- cuanto vale NUESTRA capa, con la seguridad
   fija. Es la misma politica golosa por tarifa corriendo tras el mismo gate
   de seguridad, con el peso de demanda apagado. La diferencia es
   exactamente lo que aporta el posicionamiento por demanda: nada de
   seguridad, nada de suerte. Este es el titular.

3. **Contra `GreedyRate`** -- el baseline mas fuerte, que llega ahi
   saltandose el gate. El dinero empata (+0.3%, un empate sobre 12 turnos);
   lo que cambia es 440 violaciones contra 0. Esta es la que remata: la
   seguridad salio gratis.

Y el techo (`Oracle`) no es un contrincante: es una cota superior de esta
familia de politicas -- barrido de salario de reserva con el turno ya
conocido. Se reporta como porcentaje capturado, no como una fila mas.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Nuestro agente. La fila que el dashboard resalta.
POLITICA_PROPIA = "OurAgent"

#: Cota superior, no contrincante.
POLITICA_TECHO = "Oracle"

#: Nombres legibles. Viven aqui y no en el frontend para que la pantalla y
#: los documentos llamen igual a la misma politica.
ETIQUETAS = {
    "AcceptAll": "Acepta todo",
    "HighestPay": "La que más paga",
    "NearestFirst": "La más cercana",
    "GreedyRate": "Goloso por tarifa",
    "GreedyRateSafe": "Goloso con seguridad",
    "OurAgent": "Nuestro agente",
    "Oracle": "techo clarividente",
}

#: (contra, pregunta que contesta, si es el titular). El orden es el de
#: lectura: de la mas relevante a la mas circunstancial.
COMPARACIONES = (
    ("GreedyRateSafe", "lo que aporta posicionar por demanda, con la misma seguridad", True),
    ("GreedyRate", "contra el baseline mas fuerte, que llega ahi saltandose el gate", False),
    ("AcceptAll", "contra no tener agente: aceptar todo lo que llegue", False),
)


@dataclass(frozen=True)
class FilaResultado:
    """Una fila de results_table.csv, ya tipada."""

    policy: str
    etiqueta: str
    mean_earnings_mxn: float
    median_earnings_mxn: float
    mean_mxn_per_hr: float
    accept_rate_pct: float
    orders_completed: float
    deadhead_pct_of_km: float
    deadline_misses: int
    safety_violations: int
    #: Nuestra fila, para que el dashboard sepa cual resaltar sin repetir
    #: aqui la regla de quien somos.
    propia: bool
    #: La cota superior. Se dibuja como linea, no como barra.
    techo: bool


@dataclass(frozen=True)
class Comparacion:
    contra: str
    etiqueta_contra: str
    pregunta: str
    titular: bool
    ganancia_propia_mxn: float
    ganancia_contra_mxn: float
    delta_mxn: float
    delta_pct: float
    violaciones_contra: int


@dataclass(frozen=True)
class Resultados:
    disponible: bool
    #: Comentarios `#` del CSV, tal cual. Llevan la regla de seeds disjuntas,
    #: que tiene que viajar con los numeros y no solo en la lamina.
    notas: list[str] = field(default_factory=list)
    filas: list[FilaResultado] = field(default_factory=list)
    comparaciones: list[Comparacion] = field(default_factory=list)
    #: Que fraccion del techo capturamos, en porcentaje. None si no hay Oracle.
    captura_del_techo_pct: float | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "disponible": self.disponible,
            "notas": self.notas,
            "filas": [asdict(f) for f in self.filas],
            "comparaciones": [asdict(c) for c in self.comparaciones],
            "captura_del_techo_pct": self.captura_del_techo_pct,
            "meta": self.meta,
        }


def _leer_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Separa los comentarios `#` de las filas.

    `csv.DictReader` no entiende comentarios, y el template oficial los lleva
    arriba, asi que se apartan antes de parsear.
    """
    notas: list[str] = []
    utiles: list[str] = []
    for linea in path.read_text(encoding="utf-8").splitlines():
        if linea.startswith("#"):
            notas.append(linea.lstrip("# ").rstrip())
        elif linea.strip():
            utiles.append(linea)
    return notas, list(csv.DictReader(utiles))


def leer_resultados(csv_path: Path, meta_path: Path | None = None) -> Resultados:
    """Carga la tabla publicada. Nunca levanta.

    Un archivo ausente o corrupto devuelve `disponible=False`, y el dashboard
    dice "sin resultados, corre la evaluacion" en vez de romperse. Es un panel
    de evidencia, no el fast path: su ausencia no puede tumbar nada.
    """
    if not csv_path.exists():
        return Resultados(disponible=False)

    try:
        notas, crudas = _leer_csv(csv_path)
    except (OSError, csv.Error, UnicodeDecodeError):
        return Resultados(disponible=False)

    filas: list[FilaResultado] = []
    for cruda in crudas:
        try:
            nombre = cruda["policy"]
            filas.append(
                FilaResultado(
                    policy=nombre,
                    etiqueta=ETIQUETAS.get(nombre, nombre),
                    mean_earnings_mxn=float(cruda["mean_earnings_mxn"]),
                    median_earnings_mxn=float(cruda["median_earnings_mxn"]),
                    mean_mxn_per_hr=float(cruda["mean_mxn_per_hr"]),
                    accept_rate_pct=float(cruda["accept_rate_pct"]),
                    orders_completed=float(cruda["orders_completed"]),
                    deadhead_pct_of_km=float(cruda["deadhead_pct_of_km"]),
                    deadline_misses=int(cruda["deadline_misses"]),
                    safety_violations=int(cruda["safety_violations"]),
                    propia=nombre == POLITICA_PROPIA,
                    techo=nombre == POLITICA_TECHO,
                )
            )
        except (KeyError, TypeError, ValueError):
            # Una fila ilegible se salta; el resto de la tabla sigue siendo
            # util y perderla entera por una celda seria peor.
            continue

    if not filas:
        return Resultados(disponible=False, notas=notas)

    por_nombre = {f.policy: f for f in filas}
    propia = por_nombre.get(POLITICA_PROPIA)

    comparaciones: list[Comparacion] = []
    captura: float | None = None

    if propia is not None:
        for contra, pregunta, titular in COMPARACIONES:
            otra = por_nombre.get(contra)
            if otra is None or otra.mean_earnings_mxn <= 0:
                continue
            delta = propia.mean_earnings_mxn - otra.mean_earnings_mxn
            comparaciones.append(
                Comparacion(
                    contra=contra,
                    etiqueta_contra=otra.etiqueta,
                    pregunta=pregunta,
                    titular=titular,
                    ganancia_propia_mxn=propia.mean_earnings_mxn,
                    ganancia_contra_mxn=otra.mean_earnings_mxn,
                    delta_mxn=delta,
                    delta_pct=100.0 * delta / otra.mean_earnings_mxn,
                    violaciones_contra=otra.safety_violations,
                )
            )

        techo = por_nombre.get(POLITICA_TECHO)
        if techo is not None and techo.mean_earnings_mxn > 0:
            captura = 100.0 * propia.mean_earnings_mxn / techo.mean_earnings_mxn

    meta: dict = {}
    if meta_path is not None and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}

    return Resultados(
        disponible=True,
        notas=notas,
        filas=filas,
        comparaciones=comparaciones,
        captura_del_techo_pct=captura,
        meta=meta,
    )
