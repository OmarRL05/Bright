"""Recalibra los umbrales del agente y de los baselines sobre TUNING_SEEDS.

    python3 scripts/calibrate.py                  # todo
    python3 scripts/calibrate.py --only agent     # solo el agente
    python3 scripts/calibrate.py --vehicle bike

Por que existe
--------------
`docs/Bloque 3/RESULTADOS.md` seccion 3 describe un procedimiento de
calibracion -- barridos sobre `TUNING_SEEDS`, criterio del "peor vecino" --
que hasta ahora vivia solo en la prosa. Cuando el `ZoneMap` paso de 4 a 16
zonas, las distancias del turno cambiaron y todos los umbrales quedaron mal
afinados; rehacer el barrido a mano habria sido irreproducible y lento.

Dos reglas que este script hace cumplir por construccion
--------------------------------------------------------

**1. Solo TUNING_SEEDS.** Nunca toca `REPORTING_SEEDS`. Es el unico requisito
del material con una penalizacion numerica escrita ("Results caps at 3"), y se
pierde por accidente con una facilidad absurda: basta calibrar mirando los
numeros de reporte una sola vez.

**2. Se calibran TAMBIEN los baselines**, con el mismo procedimiento y la
misma rejilla de esfuerzo. Afinar solo el nuestro y compararlo contra umbrales
puestos a ojo convierte la tabla en un espantapajaros, y "¿por que deberia
confiar en ese numero?" es una de las preguntas escritas de los jueces.

El criterio del peor vecino
----------------------------
No se toma el maximo de la rejilla. Con 12 turnos, la diferencia entre el pico
y su vecino suele estar dentro del ruido, y elegir el pico es ajustar al ruido:
el numero no sobrevive a las seeds de reporte. Se elige el punto cuyo **peor
vecino** es mas alto -- el que sigue siendo bueno si el turno sale distinto.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.evaluation import seeds as seed_sets  # noqa: E402
from core.evaluation.shift import ShiftConfig, ShiftRunner  # noqa: E402


def _mean_earnings(make_policy, shift_seeds, vehicle: str, shift_hours: float, start_zone: int) -> float:
    """Ganancia media por turno de una politica sobre un conjunto de seeds."""
    totales = []
    for seed in shift_seeds:
        config = ShiftConfig(
            seed=seed, shift_hours=shift_hours, vehicle=vehicle, start_location_zone=start_zone
        )
        totales.append(ShiftRunner(config).run(make_policy()).earnings_mxn)
    return statistics.fmean(totales)


def _best_worst_neighbour(puntuaciones: list[tuple[float, float]]) -> tuple[float, float]:
    """Elige el candidato cuyo peor vecino inmediato es mas alto.

    `puntuaciones` viene ordenado por valor del parametro. Los extremos solo
    tienen un vecino, asi que se comparan contra ese.
    """
    mejor_valor, mejor_robustez = puntuaciones[0][0], float("-inf")
    for i, (valor, score) in enumerate(puntuaciones):
        vecinos = [score]
        if i > 0:
            vecinos.append(puntuaciones[i - 1][1])
        if i < len(puntuaciones) - 1:
            vecinos.append(puntuaciones[i + 1][1])
        robustez = min(vecinos)
        if robustez > mejor_robustez:
            mejor_valor, mejor_robustez = valor, robustez
    return mejor_valor, mejor_robustez


def _sweep(nombre: str, parametro: str, candidatos, make_policy, args) -> float:
    """Barre `candidatos` y devuelve el elegido, imprimiendo la rejilla."""
    print(f"\n{nombre} — barriendo {parametro} sobre {len(args.seeds)} seeds de TUNING")
    puntuaciones = []
    for valor in candidatos:
        media = _mean_earnings(
            lambda v=valor: make_policy(v), args.seeds, args.vehicle, args.shift_hours, args.start_zone
        )
        puntuaciones.append((valor, media))
        print(f"    {parametro}={valor:<8g} ${media:>9.1f}")

    pico_valor, pico_score = max(puntuaciones, key=lambda p: p[1])
    elegido, robustez = _best_worst_neighbour(puntuaciones)
    elegido_score = next(s for v, s in puntuaciones if v == elegido)

    print(f"  pico de la rejilla : {parametro}={pico_valor:g}  (${pico_score:.1f})")
    print(f"  ELEGIDO            : {parametro}={elegido:g}  (${elegido_score:.1f}, peor vecino ${robustez:.1f})")
    if elegido != pico_valor:
        print("    (no es el pico: se prefiere el punto que sobrevive a que el turno salga distinto)")

    # Un optimo en el EXTREMO de la rejilla casi nunca es un optimo: significa
    # que la rejilla se quedo corta y el verdadero esta mas alla, o que el
    # borde gano por ruido y no tiene vecino que lo desmienta. Avisarlo es lo
    # que separa una calibracion de un numero elegido a ciegas.
    extremos = (candidatos[0], candidatos[-1])
    if elegido in extremos and len(candidatos) > 2:
        print(
            f"    AVISO: {parametro}={elegido:g} cae en el extremo de la rejilla "
            f"[{candidatos[0]:g}, {candidatos[-1]:g}]."
        )
        print("           Amplia el rango antes de creerte este valor.")
    return elegido


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vehicle", default="moto", choices=["moto", "car", "bike"])
    parser.add_argument("--shift-hours", type=float, default=8.0)
    parser.add_argument("--start-zone", type=int, default=0)
    parser.add_argument(
        "--only",
        choices=["agent", "baselines", "all"],
        default="all",
        help="que recalibrar (por defecto todo)",
    )
    args = parser.parse_args()

    seed_sets.assert_disjoint()
    args.seeds = seed_sets.TUNING_SEEDS

    print("=" * 72)
    print("  CALIBRACION  --  solo seeds de TUNING, nunca las de reporte")
    print("=" * 72)
    print(f"  tuning : {list(seed_sets.TUNING_SEEDS)}")
    print(f"  (reporte, intacto: {list(seed_sets.REPORTING_SEEDS)})")
    print(f"  vehiculo {args.vehicle}, turno {args.shift_hours} h, zona inicial {args.start_zone}")

    import core.agent.economics as eco
    from core.evaluation.policies import GreedyRate, HighestPay, NearestFirst, OurAgent

    elegidos: dict[str, float] = {}

    if args.only in ("agent", "all"):
        elegidos["RESERVATION_WAGE (OurAgent)"] = _sweep(
            "OurAgent",
            "wage",
            [float(w) for w in range(100, 501, 25)],
            lambda v: OurAgent(reservation_wage_mxn_hr=v),
            args,
        )

        # El peso de zona se barre CON el wage ya elegido: los dos interactuan
        # (un peso alto sube la tasa ajustada y por lo tanto cuantas ofertas
        # cruzan el umbral), asi que barrerlos por separado sobre el valor
        # viejo daria un optimo que no existe.
        wage = elegidos["RESERVATION_WAGE (OurAgent)"]
        anterior = eco.DROPOFF_DEMAND_WEIGHT

        def _con_peso(peso: float):
            eco.DROPOFF_DEMAND_WEIGHT = peso
            return OurAgent(reservation_wage_mxn_hr=wage)

        try:
            elegidos["DROPOFF_DEMAND_WEIGHT"] = _sweep(
                # Cota dura: con demanda minima 0.25 en el ZoneMap, un peso de
                # 2.0 ya multiplica la tasa de la zona mas fria por 0.5, y a
                # partir de 4.0 la volveria negativa. Se barre hasta 2.0, que
                # es donde el ajuste deja de ser un matiz y pasa a decidir solo.
                "OurAgent",
                "peso_zona",
                [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
                _con_peso,
                args,
            )
        finally:
            eco.DROPOFF_DEMAND_WEIGHT = anterior

    if args.only in ("baselines", "all"):
        elegidos["HIGHEST_PAY_MIN_MXN"] = _sweep(
            "HighestPay",
            "min_pay",
            [float(p) for p in range(60, 301, 20)],
            lambda v: HighestPay(min_pay_mxn=v),
            args,
        )
        elegidos["NEAREST_FIRST_MAX_DEADHEAD_KM"] = _sweep(
            "NearestFirst",
            "max_deadhead",
            [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0],
            lambda v: NearestFirst(max_deadhead_km=v),
            args,
        )
        elegidos["GREEDY_RATE_MIN_MXN_HR"] = _sweep(
            "GreedyRate",
            "min_rate",
            [float(r) for r in range(100, 501, 25)],
            lambda v: GreedyRate(min_rate_mxn_hr=v),
            args,
        )

    print("\n" + "=" * 72)
    print("  VALORES ELEGIDOS  --  copiarlos a su constante y volver a correr")
    print("=" * 72)
    for nombre, valor in elegidos.items():
        print(f"  {nombre:<34} {valor:g}")
    print("\n  Despues: python3 scripts/run_evaluation.py   (seeds de reporte)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
