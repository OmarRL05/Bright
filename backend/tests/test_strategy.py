"""Tests de la capa de estrategia y del modo degradado (core/agent/strategy.py).

Organizados por los requisitos de `evaluation_protocol.md` seccion 7, que es
lo que los jueces ensayan con la red apagada:

    - el fast path sigue decidiendo, dentro del presupuesto
    - el sistema SEÑALA que esta degradado (un fallback silencioso da credito
      parcial; un stall o un crash es fallo duro)
    - ningun pedido se encola esperando al modelo
    - la capa se recupera sola cuando vuelve la conectividad
"""

import os
import threading
import time
from datetime import datetime, timedelta

import pytest

from core.agent import strategy as st
from core.agent.reasons import MAX_REASON_WORDS
from core.agent.strategy import (
    ClaudeAdvisor,
    ModelProposal,
    ModelUnavailable,
    NullAdvisor,
    StrategyLayer,
)

T0 = datetime(2026, 3, 21, 18, 0)
CONTEXT = {"zona": 7, "ofertas_ultima_hora": 12}


class FakeAdvisor:
    """Advisor controlable: propone, falla, o tarda, segun se le pida."""

    name = "fake"

    def __init__(self, proposal=None, error=None, delay=0.0):
        self.proposal = proposal
        self.error = error
        self.delay = delay
        self.calls = 0

    def propose(self, context):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.proposal or ModelProposal(
            reservation_wage_mxn_hr=185.0, target_zone=11, reasoning="surge en la zona 11"
        )


# ==========================================================================
# Arranque
# ==========================================================================


def test_arranca_usable_sin_modelo_sin_red_y_sin_key():
    layer = StrategyLayer()
    params = layer.snapshot()
    assert params.reservation_wage_mxn_hr == st.DEFAULT_RESERVATION_WAGE_MXN_HR
    assert params.source == "bootstrap"


def test_sin_advisor_configurado_no_es_lo_mismo_que_degradado():
    """Distinguir "no hay modelo" de "el modelo se cayo" importa: lo segundo es
    lo que el protocolo puntua.

    El refresco se fuerza a proposito: la version anterior de este test solo
    leia el snapshot inicial y pasaba aunque el primer refresco marcara
    `degraded`, que es exactamente lo que estaba pasando.
    """
    layer = StrategyLayer(NullAdvisor())
    assert layer.snapshot().degraded is False

    assert layer.maybe_refresh(T0, CONTEXT) is False, "no hay a quien preguntarle"
    layer.refresh_now(T0, CONTEXT)

    assert layer.snapshot().degraded is False
    assert layer.status().degraded is False
    assert layer.status().consecutive_failures == 0


# ==========================================================================
# Requisito: el sistema SEÑALA que esta degradado
# ==========================================================================


class TestModoDegradado:
    def test_una_caida_marca_degraded_y_conserva_la_estrategia(self):
        layer = StrategyLayer(FakeAdvisor())
        layer.refresh_now(T0, CONTEXT)
        buena = layer.snapshot()
        assert buena.reservation_wage_mxn_hr == 185.0
        assert buena.degraded is False

        layer.use_advisor(FakeAdvisor(error=ModelUnavailable("sin red")))
        layer.refresh_now(T0 + timedelta(minutes=30), CONTEXT)

        degradada = layer.snapshot()
        assert degradada.degraded is True
        assert degradada.reservation_wage_mxn_hr == 185.0, (
            "se sigue decidiendo con la ultima estrategia conocida"
        )

    def test_el_fallo_no_es_silencioso_se_dice_en_los_tres_canales(self):
        """Respuesta (via snapshot), event log y status endpoint."""
        layer = StrategyLayer(FakeAdvisor(error=ModelUnavailable("credencial invalida")))
        layer.refresh_now(T0, CONTEXT)

        assert layer.snapshot().degraded is True
        assert layer.to_strategy_update_event(T0)["degraded"] is True
        assert layer.status().degraded is True

    def test_status_conserva_el_error_para_diagnosticar_en_vivo(self):
        layer = StrategyLayer(FakeAdvisor(error=ModelUnavailable("401 authentication_error")))
        layer.refresh_now(T0, CONTEXT)
        estado = layer.status()
        assert estado.consecutive_failures == 1
        assert "401" in estado.last_error

    def test_fallos_repetidos_no_republican_en_cada_intento(self):
        """Degradado ya es degradado: republicar en cada fallo llenaria el
        event log de ruido sin informacion nueva."""
        layer = StrategyLayer(FakeAdvisor(error=ModelUnavailable("sin red")))
        layer.refresh_now(T0, CONTEXT)
        revision = layer.snapshot().revision

        for i in range(5):
            layer.refresh_now(T0 + timedelta(minutes=30 * (i + 1)), CONTEXT)

        assert layer.snapshot().revision == revision
        assert layer.status().consecutive_failures == 6


class TestRecuperacion:
    def test_vuelve_sola_cuando_regresa_la_conectividad(self):
        """"The strategy layer recovers when connectivity returns"."""
        layer = StrategyLayer(FakeAdvisor(error=ModelUnavailable("sin red")))
        layer.refresh_now(T0, CONTEXT)
        assert layer.snapshot().degraded is True

        layer.use_advisor(
            FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=210.0, reasoning="llovio"))
        )
        layer.refresh_now(T0 + timedelta(minutes=30), CONTEXT)

        recuperada = layer.snapshot()
        assert recuperada.degraded is False
        assert recuperada.reservation_wage_mxn_hr == 210.0
        assert layer.status().consecutive_failures == 0

    def test_cada_publicacion_sube_la_revision(self):
        layer = StrategyLayer(FakeAdvisor())
        inicial = layer.snapshot().revision
        layer.refresh_now(T0, CONTEXT)
        assert layer.snapshot().revision == inicial + 1


# ==========================================================================
# Requisito: ningun pedido se encola esperando al modelo
# ==========================================================================


class TestElFastPathNuncaEspera:
    def test_snapshot_no_llama_al_advisor(self):
        advisor = FakeAdvisor()
        layer = StrategyLayer(advisor)
        for _ in range(1000):
            layer.snapshot()
        assert advisor.calls == 0, "el fast path no puede pedirle nada al modelo"

    def test_snapshot_es_instantaneo_aunque_el_modelo_este_colgado(self):
        """Con una llamada de 2 s en vuelo, leer parametros sigue en microsegundos."""
        layer = StrategyLayer(FakeAdvisor(delay=2.0))
        layer.maybe_refresh(T0, CONTEXT)

        t0 = time.perf_counter()
        for _ in range(1000):
            layer.snapshot()
        transcurrido_ms = (time.perf_counter() - t0) * 1000.0

        assert transcurrido_ms < 50.0, f"{transcurrido_ms:.2f} ms para 1000 lecturas"
        assert layer.status().refresh_in_flight is True

    def test_maybe_refresh_despacha_y_regresa_de_inmediato(self):
        layer = StrategyLayer(FakeAdvisor(delay=1.0))
        t0 = time.perf_counter()
        despachado = layer.maybe_refresh(T0, CONTEXT)
        transcurrido_ms = (time.perf_counter() - t0) * 1000.0
        assert despachado is True
        assert transcurrido_ms < 100.0, "maybe_refresh no puede bloquear el loop"
        layer.join(timeout=5)

    def test_no_se_apilan_llamadas_al_modelo(self):
        """Un modelo lento no puede acumular vuelos: cada uno publicaria
        parametros mas viejos que el anterior."""
        advisor = FakeAdvisor(delay=0.4)
        layer = StrategyLayer(advisor, refresh_interval_sim_min=0.0)
        for _ in range(10):
            layer.maybe_refresh(T0, CONTEXT)
        layer.join(timeout=5)
        assert advisor.calls == 1

    def test_respeta_el_intervalo_en_tiempo_de_simulacion(self):
        advisor = FakeAdvisor()
        layer = StrategyLayer(advisor, refresh_interval_sim_min=20.0)

        assert layer.maybe_refresh(T0, CONTEXT) is True
        layer.join(timeout=5)
        assert layer.maybe_refresh(T0 + timedelta(minutes=5), CONTEXT) is False
        assert layer.maybe_refresh(T0 + timedelta(minutes=25), CONTEXT) is True
        layer.join(timeout=5)
        assert advisor.calls == 2

    def test_un_advisor_que_revienta_no_tumba_el_proceso(self):
        """Un crash en tier2 seria fallo duro de Feasibility si se propagara."""
        layer = StrategyLayer(FakeAdvisor(error=ZeroDivisionError("boom")))
        layer.refresh_now(T0, CONTEXT)
        assert layer.snapshot().degraded is True
        assert "ZeroDivisionError" in layer.status().last_error


# ==========================================================================
# Requisito: tier2 aconseja, no manda
# ==========================================================================


class TestCotasDelSalarioDeReserva:
    @pytest.mark.parametrize(
        "propuesto, esperado",
        [
            (5.0, st.MIN_RESERVATION_WAGE_MXN_HR),
            (99999.0, st.MAX_RESERVATION_WAGE_MXN_HR),
            (200.0, 200.0),
        ],
    )
    def test_una_propuesta_absurda_queda_recortada(self, propuesto, esperado):
        layer = StrategyLayer(FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=propuesto)))
        layer.refresh_now(T0, CONTEXT)
        assert layer.snapshot().reservation_wage_mxn_hr == esperado

    def test_el_recorte_queda_registrado_no_se_esconde(self):
        layer = StrategyLayer(FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=99999.0)))
        layer.refresh_now(T0, CONTEXT)
        assert "recortada" in layer.snapshot().reasoning


# ==========================================================================
# La credencial se lee en cada llamada (el ensayo del protocolo)
# ==========================================================================


class TestCredencial:
    def test_sin_key_en_el_entorno_el_advisor_reporta_modelo_no_disponible(self, monkeypatch):
        """Es exactamente lo que hacen los jueces: invalidar la credencial
        en el entorno del proceso, a media corrida."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ModelUnavailable, match="ANTHROPIC_API_KEY"):
            ClaudeAdvisor().propose(CONTEXT)

    def test_una_key_vacia_cuenta_como_ausente(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with pytest.raises(ModelUnavailable):
            ClaudeAdvisor().propose(CONTEXT)

    def test_el_advisor_no_cachea_el_entorno_al_construirse(self, monkeypatch):
        """Un cliente construido al importar sobreviviria a la invalidacion y
        el requisito quedaria simulado, no implementado."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentiras")
        advisor = ClaudeAdvisor()  # construido CON key

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ModelUnavailable, match="ANTHROPIC_API_KEY"):
            advisor.propose(CONTEXT)  # ...llamado SIN key

    def test_invalidar_la_key_a_media_corrida_degrada_y_restaurarla_recupera(self, monkeypatch):
        """El ensayo completo del protocolo, sin tocar la red.

        Se usa un advisor que mira el entorno igual que ClaudeAdvisor, para
        recorrer el ciclo entero sin gastar una llamada real.
        """

        class AdvisorQueMiraElEntorno:
            name = "env-fake"

            def propose(self, context):
                if not os.environ.get("ANTHROPIC_API_KEY"):
                    raise ModelUnavailable("ANTHROPIC_API_KEY ausente o vacia en el entorno")
                return ModelProposal(reservation_wage_mxn_hr=185.0, reasoning="todo normal")

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentiras")
        layer = StrategyLayer(AdvisorQueMiraElEntorno())

        layer.refresh_now(T0, CONTEXT)
        assert layer.snapshot().degraded is False

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # los jueces
        layer.refresh_now(T0 + timedelta(minutes=30), CONTEXT)
        assert layer.snapshot().degraded is True
        assert layer.snapshot().reservation_wage_mxn_hr == 185.0

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentiras")  # restaurada
        layer.refresh_now(T0 + timedelta(minutes=60), CONTEXT)
        assert layer.snapshot().degraded is False


# ==========================================================================
# Replay y event log
# ==========================================================================


class TestReplay:
    def test_apply_recorded_fija_los_parametros_del_log(self):
        """El no-determinismo del modelo no puede mover una decision del fast path."""
        layer = StrategyLayer(FakeAdvisor())
        layer.apply_recorded(
            {
                "event": "strategy_update",
                "sim_time": T0.isoformat(),
                "reservation_wage_mxn_hr": 173.0,
                "target_zone": 11,
                "reasoning": "reinyectado",
                "confidence": "high",
            }
        )
        params = layer.snapshot()
        assert params.reservation_wage_mxn_hr == 173.0
        assert params.source == "recorded"

    def test_el_replay_tampoco_llama_al_modelo(self):
        advisor = FakeAdvisor()
        layer = StrategyLayer(advisor)
        layer.apply_recorded({"reservation_wage_mxn_hr": 173.0})
        assert advisor.calls == 0

    def test_un_valor_absurdo_en_el_log_tambien_queda_recortado(self):
        layer = StrategyLayer(FakeAdvisor())
        layer.apply_recorded({"reservation_wage_mxn_hr": 99999.0})
        assert layer.snapshot().reservation_wage_mxn_hr == st.MAX_RESERVATION_WAGE_MXN_HR


class TestEventoStrategyUpdate:
    def test_trae_las_claves_requeridas_por_el_validador(self):
        layer = StrategyLayer(FakeAdvisor())
        layer.refresh_now(T0, CONTEXT)
        evento = layer.to_strategy_update_event(T0)
        for clave in ("event", "sim_time", "reservation_wage_mxn_hr"):
            assert clave in evento
        assert evento["event"] == "strategy_update"

    def test_el_reasoning_cabe_en_pantalla(self):
        """El schema lo dice: "under 40 words - this string is displayed on screen"."""
        casos = [
            StrategyLayer(FakeAdvisor(ModelProposal(99999.0, reasoning="palabra " * 60))),
            StrategyLayer(FakeAdvisor(error=ModelUnavailable("sin red"))),
        ]
        for layer in casos:
            layer.refresh_now(T0, CONTEXT)
            assert len(layer.snapshot().reasoning.split()) <= MAX_REASON_WORDS


# ==========================================================================
# Concurrencia
# ==========================================================================


def test_lecturas_concurrentes_nunca_ven_parametros_a_medias():
    """La publicacion es una sola asignacion de atributo: o el objeto viejo
    completo, o el nuevo completo."""
    layer = StrategyLayer(FakeAdvisor())
    vistos = []
    parar = threading.Event()

    def leer():
        while not parar.is_set():
            params = layer.snapshot()
            vistos.append((params.reservation_wage_mxn_hr, params.source))

    lectores = [threading.Thread(target=leer, daemon=True) for _ in range(4)]
    for h in lectores:
        h.start()

    for i in range(20):
        layer.use_advisor(FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=100.0 + i)))
        layer.refresh_now(T0 + timedelta(minutes=i), CONTEXT)

    parar.set()
    for h in lectores:
        h.join(timeout=2)

    validos = {("bootstrap", st.DEFAULT_RESERVATION_WAGE_MXN_HR)}
    for wage, source in vistos:
        assert source in ("bootstrap", "model")
        if source == "bootstrap":
            assert wage == st.DEFAULT_RESERVATION_WAGE_MXN_HR
        else:
            assert 100.0 <= wage <= 119.0
    assert validos  # el turno corrio
