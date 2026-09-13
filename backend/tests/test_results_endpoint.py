"""Tests de GET /results y de core.evaluation.report.

Lo que se prueba aqui no es "sabe dividir": es que las comparaciones digan lo
que el panel afirma. "Gana un 29.7% mas" depende por completo de contra que se
compara, y esa eleccion vive en `COMPARACIONES`. Si alguien la cambia sin
querer, el dashboard seguiria enseniando un numero -- solo que otro.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.results as results_module
from core.evaluation.report import POLITICA_PROPIA, POLITICA_TECHO, leer_resultados
from main import app

client = TestClient(app)

TABLA = """\
# Results criterion, Courier.
# 12 turnos de 8.0 h, vehiculo moto, zona inicial 0.
policy,mean_earnings_mxn,median_earnings_mxn,mean_mxn_per_hr,accept_rate_pct,orders_completed,deadhead_pct_of_km,deadline_misses,safety_violations
AcceptAll,500.0,500.0,62.5,100.0,6.9,50.1,2359,5318
GreedyRate,2000.0,2000.0,250.0,13.0,13.3,55.4,287,440
GreedyRateSafe,1600.0,1600.0,200.0,5.2,10.2,58.5,76,0
OurAgent,2000.0,2000.0,250.0,7.1,14.0,55.3,142,0
Oracle,2500.0,2500.0,312.5,7.3,14.4,54.3,139,0
"""


@pytest.fixture
def tabla(tmp_path) -> Path:
    destino = tmp_path / "results_table.csv"
    destino.write_text(TABLA, encoding="utf-8")
    return destino


class TestLectura:
    def test_las_notas_se_separan_de_las_filas(self, tabla):
        r = leer_resultados(tabla)
        assert r.disponible
        assert len(r.filas) == 5
        assert "Results criterion, Courier." in r.notas

    def test_marca_cual_es_la_nuestra_y_cual_el_techo(self, tabla):
        r = leer_resultados(tabla)
        propias = [f.policy for f in r.filas if f.propia]
        techos = [f.policy for f in r.filas if f.techo]
        assert propias == [POLITICA_PROPIA]
        assert techos == [POLITICA_TECHO]

    def test_traduce_los_nombres_de_politica(self, tabla):
        r = leer_resultados(tabla)
        etiquetas = {f.policy: f.etiqueta for f in r.filas}
        assert etiquetas["OurAgent"] == "Nuestro agente"
        assert etiquetas["AcceptAll"] == "Acepta todo"

    def test_un_archivo_ausente_no_levanta(self, tmp_path):
        r = leer_resultados(tmp_path / "no-existe.csv")
        assert r.disponible is False
        assert r.filas == []

    def test_una_fila_ilegible_se_salta_sin_perder_la_tabla(self, tmp_path):
        roto = tmp_path / "roto.csv"
        roto.write_text(TABLA + "Basura,no-es-un-numero,,,,,,,\n", encoding="utf-8")
        r = leer_resultados(roto)
        assert r.disponible
        assert len(r.filas) == 5

    def test_lee_el_sidecar_de_metadatos(self, tabla, tmp_path):
        meta = tmp_path / "results_table_meta.json"
        meta.write_text('{"shifts": 12, "vehicle": "moto"}', encoding="utf-8")
        r = leer_resultados(tabla, meta)
        assert r.meta["shifts"] == 12


class TestComparaciones:
    def test_el_titular_es_contra_la_misma_politica_con_la_misma_seguridad(self, tabla):
        """Es la comparacion que aisla lo que aporta NUESTRA capa.

        Contra AcceptAll el numero es mucho mas grande y mucho menos honesto:
        AcceptAll comete 5318 violaciones, asi que no es una alternativa que
        alguien pudiera operar.
        """
        r = leer_resultados(tabla)
        titular = next(c for c in r.comparaciones if c.titular)
        assert titular.contra == "GreedyRateSafe"
        assert titular.delta_pct == pytest.approx(25.0)  # 2000 vs 1600
        assert titular.violaciones_contra == 0

    def test_el_empate_con_el_mejor_baseline_se_reporta_con_sus_violaciones(self, tabla):
        r = leer_resultados(tabla)
        contra_greedy = next(c for c in r.comparaciones if c.contra == "GreedyRate")
        assert contra_greedy.delta_pct == pytest.approx(0.0)
        # El dinero empata; la diferencia entera esta en esta columna.
        assert contra_greedy.violaciones_contra == 440

    def test_la_captura_del_techo_es_sobre_el_oracle(self, tabla):
        r = leer_resultados(tabla)
        assert r.captura_del_techo_pct == pytest.approx(80.0)  # 2000 / 2500

    def test_sin_nuestra_fila_no_inventa_comparaciones(self, tmp_path):
        sin_nosotros = tmp_path / "t.csv"
        sin_nosotros.write_text(
            "\n".join(l for l in TABLA.splitlines() if not l.startswith("OurAgent")) + "\n",
            encoding="utf-8",
        )
        r = leer_resultados(sin_nosotros)
        assert r.disponible
        assert r.comparaciones == []
        assert r.captura_del_techo_pct is None


class TestEndpoint:
    def test_sirve_la_tabla_publicada(self):
        resp = client.get("/results")
        assert resp.status_code == 200
        datos = resp.json()
        assert datos["disponible"], "falta data/results_table.csv en el repo"
        assert any(f["propia"] for f in datos["filas"])
        assert datos["comparaciones"], "sin comparaciones no hay panel"

    def test_la_tabla_commiteada_dice_que_no_violamos_nada(self):
        """Si esto falla, el panel esta presumiendo algo que dejo de ser cierto."""
        datos = client.get("/results").json()
        nuestra = next(f for f in datos["filas"] if f["propia"])
        assert nuestra["safety_violations"] == 0

    def test_sin_tabla_responde_disponible_false_en_vez_de_romperse(self, monkeypatch, tmp_path):
        monkeypatch.setattr(results_module, "RESULTS_CSV", tmp_path / "no-existe.csv")
        resp = client.get("/results")
        assert resp.status_code == 200
        assert resp.json()["disponible"] is False
