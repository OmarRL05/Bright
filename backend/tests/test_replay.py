"""Tests del replay determinista (core/evaluation/replay.py).

El requisito del protocolo (seccion 6) es una sola frase — "identical
fast-path accept/skip decisions on identical input" — pero se puede aprobar
haciendo trampa de tres formas, y estos tests cierran las tres:

1. Reproducir **volviendo a correr el simulador** en vez de leer el log. Se
   cierra comprobando que las decisiones salen de los numeros del log, no de
   una regeneracion (`test_usa_las_distancias_del_log`).
2. Comparar un turno contra **si mismo** sin haber pasado por el sistema. Se
   cierra comprobando que el diff SI detecta una diferencia cuando la hay
   (`test_detecta_una_diferencia_de_verdad`).
3. Dejar que **tier2 mueva el umbral** a media reproduccion y que el diff
   salga distinto por diseño (`TestParametrosClavados`).
"""

import json
from datetime import datetime, timedelta

import pytest

from core.agent.shocks import SHOCKS
from core.agent.strategy import STRATEGY
from core.evaluation.policies import OurAgent
from core.evaluation.replay import (
    ReplayReport,
    _config_from_shift_start,
    in_process_decide,
    load_log,
    replay_log,
)
from core.evaluation.shift import ShiftConfig, ShiftRunner

SEED = 101


@pytest.fixture
def log_de_turno(tmp_path):
    """Graba un turno completo y devuelve sus eventos."""
    destino = tmp_path / f"replay_seed_{SEED}.jsonl"
    with destino.open("w", encoding="utf-8") as handle:
        ShiftRunner(ShiftConfig(seed=SEED), log_file=handle).run(OurAgent())
    eventos = load_log(destino)
    yield eventos
    SHOCKS.reset()
    STRATEGY.resume_live()


# ==========================================================================
# El criterio del protocolo
# ==========================================================================


def test_un_turno_grabado_se_reproduce_identico(log_de_turno):
    """"identical fast-path accept/skip decisions on identical input"."""
    reporte = replay_log(log_de_turno, in_process_decide())

    assert reporte.compared > 100, "un turno con pocas decisiones no prueba gran cosa"
    assert reporte.identical, reporte.render()
    assert reporte.hard_mismatches == []


def test_reproducir_dos_veces_da_lo_mismo(log_de_turno):
    """Si el propio replay tuviera estado mutable, la segunda pasada diferiria."""
    primero = replay_log(log_de_turno, in_process_decide())
    segundo = replay_log(log_de_turno, in_process_decide())
    assert (primero.compared, primero.accepted) == (segundo.compared, segundo.accepted)
    assert segundo.identical


def test_el_deadhead_del_log_no_es_cero(log_de_turno):
    """El log tiene que llevar el deadhead REAL.

    El generador no sabe donde esta el repartidor, asi que si el evento lo
    emitiera el, `distance_pickup_km` seria 0.0 y el log no seria un registro
    fiel de la entrada. Reproducirlo daria otra economia, y el diff pareceria
    no-determinismo sin serlo. Fue el primer fallo real que encontro el replay.
    """
    ofertas = [e for e in log_de_turno if e.get("event") == "order_offered"]
    con_deadhead = [e for e in ofertas if (e.get("distance_pickup_km") or 0) > 0]
    assert len(con_deadhead) > len(ofertas) // 2


def test_usa_las_distancias_del_log_y_no_las_recalcula(log_de_turno):
    """El log ES la entrada. Si el replay recalculara, seria volver a correr
    el simulador -- otra cosa, y mas facil de aprobar."""
    eventos = [dict(e) for e in log_de_turno]
    tocadas = 0
    for evento in eventos:
        if evento.get("event") == "order_offered":
            evento["distance_delivery_km"] = float(evento.get("distance_delivery_km") or 0) * 8
            tocadas += 1
    assert tocadas > 0

    reporte = replay_log(eventos, in_process_decide())
    assert not reporte.identical, (
        "multiplicar por 8 las distancias del log no cambio ninguna decision: "
        "el replay no esta leyendo el log"
    )


def test_detecta_una_diferencia_de_verdad(log_de_turno):
    """Un diff que nunca falla no prueba nada."""
    eventos = [dict(e) for e in log_de_turno]
    volteadas = 0
    for evento in eventos:
        if evento.get("event") == "decision" and volteadas < 3:
            evento["decision"] = "SKIP" if evento["decision"] == "ACCEPT" else "ACCEPT"
            volteadas += 1
    assert volteadas == 3

    reporte = replay_log(eventos, in_process_decide())
    assert not reporte.identical
    duras = [m for m in reporte.hard_mismatches if m.field == "decision"]
    assert len(duras) >= 3


# ==========================================================================
# Parametros clavados
# ==========================================================================


class TestParametrosClavados:
    def test_reinyectar_clava_la_capa_de_estrategia(self, log_de_turno):
        """Sin clavar, tier2 podria publicar una revision a media reproduccion
        y el diff saldria distinto por diseño, no por un bug."""
        assert not STRATEGY.replay_pinned
        replay_log(log_de_turno, in_process_decide())
        assert STRATEGY.replay_pinned

    def test_clavada_no_despacha_refrescos(self, log_de_turno):
        replay_log(log_de_turno, in_process_decide())
        momento = datetime(2026, 6, 1, 12, 0)
        assert STRATEGY.maybe_refresh(momento, {}) is False
        assert STRATEGY.maybe_refresh(momento + timedelta(hours=5), {}) is False

    def test_resume_live_la_suelta(self, log_de_turno):
        replay_log(log_de_turno, in_process_decide())
        STRATEGY.resume_live()
        assert not STRATEGY.replay_pinned

    def test_el_umbral_del_log_es_el_que_se_usa(self, log_de_turno):
        evento = next(e for e in log_de_turno if e.get("event") == "strategy_update")
        replay_log(log_de_turno, in_process_decide())
        assert STRATEGY.snapshot().reservation_wage_mxn_hr == pytest.approx(
            evento["reservation_wage_mxn_hr"]
        )
        assert STRATEGY.snapshot().source == "recorded"


# ==========================================================================
# Shocks
# ==========================================================================


class TestShocksDelLog:
    def test_se_reinyectan_los_del_turno(self, log_de_turno):
        grabados = sum(1 for e in log_de_turno if e.get("event") == "shock")
        reporte = replay_log(log_de_turno, in_process_decide())
        assert grabados > 0, "este turno no tuvo shocks: el test no prueba nada"
        assert reporte.shocks_replayed == grabados

    def test_sin_reinyectarlos_el_turno_deja_de_coincidir(self, log_de_turno):
        """Es la razon por la que el arnes inyecta sus shocks en el registro
        real: durante un tiempo los escribia al log y no afectaban a ninguna
        decision, asi que el turno se registraba como si hubiera habido
        cierres y decidia como si no."""
        SHOCKS.reset()
        reporte = replay_log(log_de_turno, in_process_decide(), prime_layers=False)
        assert not reporte.identical


# ==========================================================================
# Lectura del log
# ==========================================================================


class TestLectura:
    def test_tolera_lineas_vacias_y_basura(self, tmp_path):
        destino = tmp_path / "sucio.jsonl"
        destino.write_text('{"event": "shift_start"}\n\nno es json\n{"event": "shock"}\n')
        assert len(load_log(destino)) == 2

    def test_la_config_sale_del_shift_start(self):
        config = _config_from_shift_start(
            {
                "sim_time": "2026-03-21T15:00:00",
                "shift_end_time": "2026-03-21T23:00:00",
                "seed": 7,
                "vehicle": "bike",
                "start_location_zone": 3,
            }
        )
        assert (config.seed, config.vehicle, config.start_location_zone) == (7, "bike", 3)
        assert config.shift_hours == pytest.approx(8.0)

    def test_shift_end_time_manda_sobre_shift_hours(self):
        """El schema lo marca como "read from state, never hardcoded"."""
        config = _config_from_shift_start(
            {
                "sim_time": "2026-03-21T15:00:00",
                "shift_end_time": "2026-03-21T21:00:00",
                "shift_hours": 8,
            }
        )
        assert config.shift_hours == pytest.approx(6.0)

    def test_un_log_sin_shift_start_no_revienta(self):
        reporte = replay_log([{"event": "shock", "shock_type": "rain"}], in_process_decide())
        assert isinstance(reporte, ReplayReport)
        assert reporte.orders == 0


def test_una_oferta_sin_decision_grabada_se_reporta(log_de_turno):
    """No se puede declarar identico un turno del que faltan decisiones."""
    eventos = [e for e in log_de_turno if e.get("event") != "decision"]
    reporte = replay_log(eventos, in_process_decide())
    assert reporte.unmatched
    assert not reporte.identical
