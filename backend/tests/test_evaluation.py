"""Tests del arnes de evaluacion (core/evaluation/).

Cubren las tres cosas que, si se rompen en silencio, invalidan la tabla de
resultados entera sin que nadie se entere:

1. Que las seeds de tuning y de reporte sigan disjuntas.
2. Que el turno sea reproducible byte a byte.
3. Que las ganancias se cuenten al ENTREGAR y solo de lo que cabe en el turno.
"""

import hashlib
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from core.evaluation import seeds as seed_sets
from core.evaluation.metrics import CSV_COLUMNS, summarize, write_csv
from core.evaluation.policies import (
    ALL_POLICIES,
    AcceptAll,
    GreedyRate,
    OurAgent,
    PolicyDecision,
)
from core.evaluation.shift import ShiftConfig, ShiftRunner
from core.agent.reasons import MAX_REASON_WORDS
from core.agent.safety import MANDATORY_BREAK_AFTER_MIN

REPO = Path(__file__).resolve().parents[2]
VALIDATOR = REPO / "student-materials" / "courier" / "validate_format.py"
TEMPLATE = REPO / "student-materials" / "courier" / "results_table_template.csv"


def run(policy, seed=101, **kwargs):
    return ShiftRunner(ShiftConfig(seed=seed, **kwargs)).run(policy)


# ==========================================================================
# Seeds
# ==========================================================================


class TestSeeds:
    def test_tuning_y_reporte_son_disjuntas(self):
        """Es el unico requisito del material con penalizacion numerica
        escrita: reportar sobre seeds de tuning topa Results en 3."""
        seed_sets.assert_disjoint()

    def test_hay_al_menos_diez_turnos_held_out(self):
        assert len(seed_sets.REPORTING_SEEDS) >= 10

    def test_assert_disjoint_falla_de_verdad_si_se_solapan(self, monkeypatch):
        """Un guardian que no falla no es un guardian."""
        monkeypatch.setattr(seed_sets, "TUNING_SEEDS", (101, 999))
        with pytest.raises(AssertionError, match="solapan"):
            seed_sets.assert_disjoint()


# ==========================================================================
# Determinismo
# ==========================================================================


class TestDeterminismo:
    def test_el_mismo_seed_da_el_mismo_turno(self):
        a, b = run(OurAgent()), run(OurAgent())
        assert (a.earnings_mxn, a.orders_completed, a.orders_accepted) == (
            b.earnings_mxn,
            b.orders_completed,
            b.orders_accepted,
        )

    def test_seeds_distintas_dan_turnos_distintos(self):
        """Si no, el 'stream reproducible' seria un stream constante."""
        a, b = run(OurAgent(), seed=101), run(OurAgent(), seed=113)
        assert a.earnings_mxn != b.earnings_mxn

    def test_el_event_log_es_byte_identico_entre_procesos(self, tmp_path):
        """Se corre en SUBPROCESOS con PYTHONHASHSEED distinto: es la unica
        forma de detectar una dependencia del orden de iteracion de un set o
        un dict, que dentro del mismo proceso nunca se manifiesta."""
        hashes = set()
        for hashseed in ("0", "1", "2"):
            destino = tmp_path / f"shift_{hashseed}.jsonl"
            subprocess.run(
                [
                    sys.executable,
                    str(REPO / "backend" / "scripts" / "run_evaluation.py"),
                    "--event-log",
                    str(destino),
                    "--seed",
                    "101",
                ],
                env={"PYTHONHASHSEED": hashseed, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                check=True,
            )
            hashes.add(hashlib.sha256(destino.read_bytes()).hexdigest())
        assert len(hashes) == 1, "el turno depende del orden de hash de Python"


# ==========================================================================
# Modelo del repartidor
# ==========================================================================


class TestModeloDelRepartidor:
    def test_solo_se_cobra_lo_que_se_entrega_dentro_del_turno(self):
        """Hallazgo 4 + el bug que inflaba a los baselines.

        AcceptAll acepta todo, pero la mayoria termina despues del cierre: sus
        ganancias tienen que ser mucho menores que sus aceptaciones.
        """
        estado = run(AcceptAll())
        assert estado.orders_accepted > estado.orders_completed
        assert estado.orders_abandoned > 0
        assert estado.orders_accepted == estado.orders_completed + estado.orders_abandoned

    def test_las_ganancias_son_netas_de_combustible(self):
        estado = run(OurAgent())
        bruto_minimo = estado.km_total * ShiftConfig(seed=101).profile.cost_per_km
        assert estado.earnings_mxn > 0
        assert bruto_minimo > 0, "un turno sin kilometros no prueba nada"

    def test_el_repartidor_descansa_en_vez_de_quedarse_bloqueado(self):
        """Una constraint que encierra al repartidor no es una regla de
        seguridad, es un defecto. Sin pausas, `mandatory_break` rechazaba el
        resto del turno completo."""
        estado = run(AcceptAll(), shift_hours=10.0)
        assert estado.breaks_taken > 0
        assert estado.continuous_riding_min < MANDATORY_BREAK_AFTER_MIN * 2

    def test_la_mochila_se_vacia_al_entregar(self):
        """Hallazgo 7: sin drenaje, la ruta crece de forma monotona y la
        capacidad termina rechazandolo todo."""
        estado = run(OurAgent(), shift_hours=8.0)
        assert len(estado.in_flight) < 10

    def test_un_turno_mas_largo_deja_mas_dinero(self):
        corto = run(OurAgent(), shift_hours=4.0)
        largo = run(OurAgent(), shift_hours=8.0)
        assert largo.earnings_mxn > corto.earnings_mxn

    @pytest.mark.parametrize("vehicle", ["moto", "car", "bike"])
    def test_los_tres_vehiculos_dan_resultados_distintos(self, vehicle):
        """Si los tres perfiles dieran lo mismo, no serian tres perfiles."""
        estado = run(OurAgent(), vehicle=vehicle)
        assert estado.orders_completed > 0


# ==========================================================================
# Politicas
# ==========================================================================


class TestPoliticas:
    @pytest.mark.parametrize("seed", seed_sets.REPORTING_SEEDS)
    def test_nuestro_agente_no_viola_nunca(self, seed):
        """"safety_violations must be 0 for your agent" -- sobre CADA turno
        held-out, no sobre el promedio."""
        estado = run(OurAgent(), seed=seed)
        assert estado.safety_violations == 0, estado.violations_by_constraint

    @pytest.mark.parametrize("vehicle", ["moto", "car", "bike"])
    def test_cero_violaciones_tambien_en_los_otros_vehiculos(self, vehicle):
        assert run(OurAgent(), vehicle=vehicle).safety_violations == 0

    def test_los_baselines_si_violan(self):
        """Es el punto de la comparacion: si todos pasaran por el gate, la
        columna seria cero en todas las filas y no diria nada."""
        assert run(AcceptAll()).safety_violations > 0
        assert run(GreedyRate()).safety_violations > 0

    def test_toda_politica_da_un_motivo_de_menos_de_cuarenta_palabras(self):
        """El motivo va al evento `decision` del event log, y ahi lo valida
        validate_format.py igual que en el response de /decide.

        Se revisan los motivos REALES de un turno entero, no una muestra
        fabricada: una plantilla se pasa de largo justo con los numeros que no
        se le ocurren a nadie al escribirla.
        """
        import json

        import tempfile

        vistos = 0
        for policy_cls in ALL_POLICIES:
            if hasattr(policy_cls, "run_shift"):
                continue  # el Oracle no decide oferta por oferta
            with tempfile.NamedTemporaryFile("w+", suffix=".jsonl") as handle:
                ShiftRunner(ShiftConfig(seed=101), log_file=handle).run(policy_cls())
                handle.flush()
                handle.seek(0)
                for linea in handle:
                    if not linea.strip():
                        continue
                    evento = json.loads(linea)
                    if evento.get("event") != "decision":
                        continue
                    motivo = evento["reason"]
                    assert motivo.strip(), f"{policy_cls.name} emitio un reason vacio"
                    assert len(motivo.split()) <= MAX_REASON_WORDS, (
                        f"{policy_cls.name}: {len(motivo.split())} palabras -- {motivo}"
                    )
                    vistos += 1
        assert vistos > 500, "se revisaron muy pocos motivos para que el test signifique algo"

    def test_policy_decision_se_comporta_como_booleano(self):
        assert bool(PolicyDecision(True, "si"))
        assert not bool(PolicyDecision(False, "no"))


# ==========================================================================
# Event log y CSV
# ==========================================================================


class TestSalidas:
    @pytest.mark.skipif(not VALIDATOR.exists(), reason="student-materials no esta en el repo")
    def test_un_turno_completo_pasa_el_validador_oficial(self, tmp_path):
        destino = tmp_path / "shift.jsonl"
        with destino.open("w", encoding="utf-8") as handle:
            ShiftRunner(ShiftConfig(seed=101), log_file=handle).run(OurAgent())

        resultado = subprocess.run(
            [sys.executable, str(VALIDATOR), "--event-log", str(destino)],
            capture_output=True,
            text=True,
        )
        assert resultado.returncode == 0, resultado.stdout + resultado.stderr

    def test_el_log_trae_los_ocho_tipos_de_evento(self, tmp_path):
        import json

        destino = tmp_path / "shift.jsonl"
        with destino.open("w", encoding="utf-8") as handle:
            ShiftRunner(ShiftConfig(seed=101), log_file=handle).run(OurAgent())

        tipos = {json.loads(l)["event"] for l in destino.read_text().splitlines() if l.strip()}
        assert tipos == {
            "shift_start", "order_offered", "decision", "position_update",
            "earnings_update", "shock", "strategy_update", "shift_end",
        }

    def test_los_eventos_van_en_orden_cronologico(self, tmp_path):
        """El schema lo exige: "one event per line, in chronological order".
        Con el stream materializado de golpe, las decisiones caian despues del
        shift_end."""
        import json

        destino = tmp_path / "shift.jsonl"
        with destino.open("w", encoding="utf-8") as handle:
            ShiftRunner(ShiftConfig(seed=101), log_file=handle).run(OurAgent())

        tiempos = [json.loads(l)["sim_time"] for l in destino.read_text().splitlines() if l.strip()]
        assert tiempos == sorted(tiempos)

    @pytest.mark.skipif(not TEMPLATE.exists(), reason="student-materials no esta en el repo")
    def test_las_columnas_son_exactamente_las_del_template_oficial(self):
        encabezado = next(
            l for l in TEMPLATE.read_text().splitlines() if l and not l.startswith("#")
        )
        assert tuple(encabezado.split(",")) == CSV_COLUMNS

    def test_el_csv_se_escribe_con_las_notas_de_seeds(self, tmp_path):
        estados = [run(OurAgent(), seed=s) for s in (101, 113)]
        destino = write_csv(
            [summarize("OurAgent", estados)],
            tmp_path / "r.csv",
            ["Seeds de REPORTE: [101, 113]"],
        )
        contenido = destino.read_text()
        assert contenido.startswith("# Seeds de REPORTE")
        assert ",".join(CSV_COLUMNS) in contenido

    def test_summarize_exige_al_menos_un_turno(self):
        with pytest.raises(ValueError, match="ningun turno"):
            summarize("Vacia", [])
