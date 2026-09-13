"""Corre la evaluacion completa y escribe el CSV de resultados.

    python3 scripts/run_evaluation.py                 # seeds de REPORTE (la lamina)
    python3 scripts/run_evaluation.py --tuning        # seeds de TUNING (calibrar)
    python3 scripts/run_evaluation.py --vehicle bike  # otro perfil de vehiculo
    python3 scripts/run_evaluation.py --event-log out/shift.jsonl --seed 101

El modo por defecto es el de reporte a proposito: lo que se pega en la lamina
debe salir del comando sin argumentos, para que nadie reporte por accidente los
numeros de las seeds con las que calibro. Ver `core/evaluation/seeds.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.evaluation import seeds as seed_sets  # noqa: E402
from core.evaluation.metrics import render_table, summarize, write_csv  # noqa: E402
from core.evaluation.policies import ALL_POLICIES  # noqa: E402
from core.evaluation.shift import ShiftConfig, ShiftRunner  # noqa: E402


def run_policy(policy_cls, shift_seeds, vehicle: str, shift_hours: float, start_zone: int):
    """Corre una politica sobre todas las seeds. Instancia nueva por turno:
    el `Oracle` guarda el stream del turno, y reusarla filtraria informacion
    de un turno al siguiente."""
    estados = []
    for seed in shift_seeds:
        config = ShiftConfig(
            seed=seed,
            shift_hours=shift_hours,
            vehicle=vehicle,
            start_location_zone=start_zone,
        )
        if hasattr(policy_cls, "run_shift"):
            # El Oracle no decide oferta por oferta: corre el turno entero
            # varias veces y se queda con la mejor (ver policies.Oracle).
            estados.append(policy_cls.run_shift(config))
        else:
            estados.append(ShiftRunner(config).run(policy_cls()))
    return estados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tuning",
        action="store_true",
        help="usar TUNING_SEEDS en vez de REPORTING_SEEDS (para calibrar, NO para reportar)",
    )
    parser.add_argument("--vehicle", default="moto", choices=["moto", "car", "bike"])
    parser.add_argument("--shift-hours", type=float, default=8.0)
    parser.add_argument("--start-zone", type=int, default=0)
    parser.add_argument("--out", default="out/results_table.csv")
    parser.add_argument(
        "--event-log",
        help="graba el event log JSONL de UN turno (requiere --seed)",
    )
    parser.add_argument("--seed", type=int, help="turno unico a grabar con --event-log")
    args = parser.parse_args()

    seed_sets.assert_disjoint()

    if args.event_log:
        if args.seed is None:
            parser.error("--event-log necesita --seed")
        return _record_one_shift(args)

    conjunto = "TUNING" if args.tuning else "REPORTE (held-out)"
    shift_seeds = seed_sets.TUNING_SEEDS if args.tuning else seed_sets.REPORTING_SEEDS

    print(f"\nConjunto de seeds: {conjunto}")
    print(f"  tuning:  {list(seed_sets.TUNING_SEEDS)}")
    print(f"  reporte: {list(seed_sets.REPORTING_SEEDS)}")
    print(f"Vehiculo: {args.vehicle}   Turno: {args.shift_hours} h   Turnos: {len(shift_seeds)}\n")

    resultados = []
    for policy_cls in ALL_POLICIES:
        estados = run_policy(
            policy_cls, shift_seeds, args.vehicle, args.shift_hours, args.start_zone
        )
        resultado = summarize(policy_cls.name, estados)
        resultados.append(resultado)
        print(
            f"  {resultado.policy:<12} ${resultado.mean_earnings_mxn:>8.1f} medios   "
            f"{resultado.safety_violations:>4} violaciones de seguridad"
        )

    print("\n" + render_table(resultados) + "\n")

    agente = next(r for r in resultados if r.policy == "OurAgent")
    oracle = next(r for r in resultados if r.policy == "Oracle")
    mejor_baseline = max(
        (r for r in resultados if r.policy in {"AcceptAll", "HighestPay", "NearestFirst", "GreedyRate"}),
        key=lambda r: r.mean_earnings_mxn,
    )

    notas = [
        "Results criterion, Courier.",
        f"Seeds de REPORTE (held-out, no se calibro con ellas): {list(seed_sets.REPORTING_SEEDS)}",
        f"Seeds de TUNING (se calibro con ellas, NO se reportan): {list(seed_sets.TUNING_SEEDS)}",
        f"{len(shift_seeds)} turnos de {args.shift_hours} h, vehiculo {args.vehicle}, zona inicial {args.start_zone}.",
        "Ganancias contadas AL ENTREGAR y netas de combustible (cost_per_km del perfil).",
        "Los baselines no consultan el gate de seguridad; las violaciones se miden por fuera,",
        "sobre lo que cada politica acepto. Por eso la columna no es cero en sus filas.",
        "Oracle = agente clarividente y goloso con las mismas constraints de seguridad:",
        "cota superior de esta familia de politicas, no un optimo demostrado.",
    ]
    destino = write_csv(resultados, Path(args.out), notas)

    print(f"CSV escrito en {destino}")
    print(
        f"\nOurAgent vs {mejor_baseline.policy} (mejor baseline): "
        f"{_delta_pct(agente.mean_earnings_mxn, mejor_baseline.mean_earnings_mxn):+.1f}%"
    )
    if oracle.mean_earnings_mxn:
        print(
            f"OurAgent captura el {100 * agente.mean_earnings_mxn / oracle.mean_earnings_mxn:.1f}% "
            "de la cota superior (Oracle)"
        )
    print(f"Violaciones de seguridad de OurAgent: {agente.safety_violations}")
    if agente.safety_violations:
        print(f"  desglose: {agente.violations_by_constraint}")

    return 0 if agente.safety_violations == 0 else 1


def _delta_pct(nuevo: float, viejo: float) -> float:
    return 100.0 * (nuevo - viejo) / viejo if viejo else 0.0


def _record_one_shift(args) -> int:
    """Graba el event log JSONL de un turno unico con OurAgent."""
    from core.evaluation.policies import OurAgent

    destino = Path(args.event_log)
    destino.parent.mkdir(parents=True, exist_ok=True)

    config = ShiftConfig(
        seed=args.seed,
        shift_hours=args.shift_hours,
        vehicle=args.vehicle,
        start_location_zone=args.start_zone,
    )
    with destino.open("w", encoding="utf-8") as handle:
        estado = ShiftRunner(config, log_file=handle).run(OurAgent())

    print(f"Turno seed={args.seed} grabado en {destino}")
    print(
        f"  {estado.orders_offered} ofertas, {estado.orders_accepted} aceptadas, "
        f"${estado.earnings_mxn:.0f}, {estado.safety_violations} violaciones"
    )
    print(f"\nValidar con:\n  python3 ../student-materials/courier/validate_format.py --event-log {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
