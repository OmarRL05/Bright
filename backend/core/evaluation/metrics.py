"""Agregacion de turnos a las filas de `results_table_template.csv`.

Las columnas son las del template oficial, en su orden. Dos decisiones de
agregacion que conviene tener escritas porque el template no las fija y un
juez puede preguntar:

- **Las medias son por turno.** `mean_earnings_mxn` es lo que gana un turno
  promedio, no la suma de los 12. `orders_completed` tambien es por turno.
- **`deadline_misses` y `safety_violations` son TOTALES** sobre todos los
  turnos reportados. Promediar una violacion de seguridad la diluye: "0.08
  violaciones por turno" suena aceptable y no lo es. El numero que importa es
  si hubo alguna, y cuantas.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from core.evaluation.shift import ShiftState

#: Encabezado exacto de results_table_template.csv.
CSV_COLUMNS = (
    "policy",
    "mean_earnings_mxn",
    "median_earnings_mxn",
    "mean_mxn_per_hr",
    "accept_rate_pct",
    "orders_completed",
    "deadhead_pct_of_km",
    "deadline_misses",
    "safety_violations",
)


@dataclass(frozen=True)
class PolicyResult:
    policy: str
    shifts: int
    mean_earnings_mxn: float
    median_earnings_mxn: float
    mean_mxn_per_hr: float
    accept_rate_pct: float
    orders_completed: float
    deadhead_pct_of_km: float
    deadline_misses: int
    safety_violations: int
    violations_by_constraint: dict[str, int]

    def as_csv_row(self) -> dict[str, str]:
        return {
            "policy": self.policy,
            "mean_earnings_mxn": f"{self.mean_earnings_mxn:.1f}",
            "median_earnings_mxn": f"{self.median_earnings_mxn:.1f}",
            "mean_mxn_per_hr": f"{self.mean_mxn_per_hr:.1f}",
            "accept_rate_pct": f"{self.accept_rate_pct:.1f}",
            "orders_completed": f"{self.orders_completed:.1f}",
            "deadhead_pct_of_km": f"{self.deadhead_pct_of_km:.1f}",
            "deadline_misses": str(self.deadline_misses),
            "safety_violations": str(self.safety_violations),
        }


def _safe_ratio(numerator: float, denominator: float) -> float:
    return (numerator / denominator) if denominator else 0.0


def summarize(policy: str, states: Sequence[ShiftState]) -> PolicyResult:
    """Agrega los turnos de una politica a una fila del CSV."""
    if not states:
        raise ValueError(f"la politica {policy!r} no corrio ningun turno")

    earnings = [s.earnings_mxn for s in states]
    horas = [s.config.shift_hours for s in states]

    violations: dict[str, int] = {}
    for state in states:
        for constraint, count in state.violations_by_constraint.items():
            violations[constraint] = violations.get(constraint, 0) + count

    return PolicyResult(
        policy=policy,
        shifts=len(states),
        mean_earnings_mxn=statistics.fmean(earnings),
        median_earnings_mxn=statistics.median(earnings),
        mean_mxn_per_hr=statistics.fmean(
            _safe_ratio(e, h) for e, h in zip(earnings, horas)
        ),
        accept_rate_pct=100.0
        * _safe_ratio(
            sum(s.orders_accepted for s in states), sum(s.orders_offered for s in states)
        ),
        orders_completed=statistics.fmean(s.orders_completed for s in states),
        deadhead_pct_of_km=100.0
        * _safe_ratio(sum(s.km_deadhead for s in states), sum(s.km_total for s in states)),
        deadline_misses=sum(s.deadline_misses for s in states),
        safety_violations=sum(s.safety_violations for s in states),
        violations_by_constraint=violations,
    )


def write_csv(results: Iterable[PolicyResult], path: Path, header_notes: Sequence[str] = ()) -> Path:
    """Escribe el CSV con el encabezado del template y las notas arriba.

    Las notas van como comentarios `#`, igual que el template oficial, porque
    la regla de seeds disjuntas ("you must say which is which") tiene que
    viajar con el archivo y no solo en la lamina.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        for note in header_notes:
            handle.write(f"# {note}\n")
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for result in results:
            writer.writerow(result.as_csv_row())
    return path


def render_table(results: Sequence[PolicyResult]) -> str:
    """Tabla de texto alineada, para leerla en la terminal."""
    anchos = [max(len(c), 9) for c in CSV_COLUMNS]
    filas = [r.as_csv_row() for r in results]
    for fila in filas:
        for i, col in enumerate(CSV_COLUMNS):
            anchos[i] = max(anchos[i], len(fila[col]))

    def linea(valores: Sequence[str]) -> str:
        return "  ".join(v.rjust(anchos[i]) if i else v.ljust(anchos[0]) for i, v in enumerate(valores))

    salida = [linea(CSV_COLUMNS), "  ".join("-" * a for a in anchos)]
    salida.extend(linea([fila[c] for c in CSV_COLUMNS]) for fila in filas)
    return "\n".join(salida)
