"""Seeds de tuning y de reporte. Disjuntas, nombradas, y en codigo.

    "Use disjoint seed sets for tuning and reporting. Your reported numbers
     must come from seeds you did not tune on."
    -- evaluation_protocol.md, seccion 1

    "If your reported numbers come from the seeds you tuned on, Results caps
     at 3 regardless of the margin."
    -- results_table_template.csv

Es el unico requisito del material con una penalizacion numerica escrita, y se
pierde por accidente con una facilidad absurda: basta con que alguien corra el
arnes sin argumentos mientras calibra una constante y despues copie ESE numero
a la lamina. Por eso las dos listas viven aqui, con nombre, y `assert_disjoint`
corre como test.

Como se usan
------------
- **TUNING_SEEDS**: para calibrar constantes (`RESERVATION_WAGE_MXN_HR`,
  `DROPOFF_DEMAND_WEIGHT`, los umbrales de los baselines). Mirar estos
  numeros tanto como haga falta.
- **REPORTING_SEEDS**: solo para la tabla final. **No calibrar contra
  estos.** Si una constante se movio despues de mirar un resultado de esta
  lista, ese resultado ya no es held-out y hay que cambiar la lista.

Son 12 de reporte porque el protocolo pide "at least 10 held-out shifts" para
las bandas altas, y dos de sobra cuestan segundos.
"""

#: Seeds para calibrar. Mirar libremente.
#:
#: Son 12 y no 6 porque con 6 la superficie de calibracion es ruidosa: elegir
#: el maximo de una rejilla medida sobre pocos turnos es sobreajustar al ruido,
#: y el numero que sale de ahi no sobrevive a las seeds de reporte.
TUNING_SEEDS: tuple[int, ...] = (
    11, 23, 37, 41, 59, 67, 71, 83, 89, 97, 103, 109,
)

#: Seeds held-out: SOLO para la tabla de resultados final.
REPORTING_SEEDS: tuple[int, ...] = (
    101, 113, 127, 131, 149, 151, 163, 173, 181, 191, 199, 211,
)


def assert_disjoint() -> None:
    """Falla si alguien mete una seed de reporte en la lista de tuning.

    Se llama desde los tests. Un solapamiento silencioso es exactamente el
    error que el protocolo castiga con techo de 3 en Results.
    """
    solapadas = set(TUNING_SEEDS) & set(REPORTING_SEEDS)
    if solapadas:
        raise AssertionError(
            f"seeds de tuning y de reporte se solapan: {sorted(solapadas)}. "
            "Los numeros reportados dejarian de ser held-out y Results queda "
            "con techo de 3."
        )
    if len(REPORTING_SEEDS) < 10:
        raise AssertionError(
            f"solo {len(REPORTING_SEEDS)} seeds de reporte; el protocolo pide "
            "al menos 10 turnos held-out para las bandas altas."
        )
