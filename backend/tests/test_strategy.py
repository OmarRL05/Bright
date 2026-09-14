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
    GeminiAdvisor,
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
            reservation_wage_mxn_hr=275.0, target_zone=11, reasoning="surge en la zona 11"
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
        assert buena.reservation_wage_mxn_hr == 275.0
        assert buena.degraded is False

        layer.use_advisor(FakeAdvisor(error=ModelUnavailable("sin red")))
        layer.refresh_now(T0 + timedelta(minutes=30), CONTEXT)

        degradada = layer.snapshot()
        assert degradada.degraded is True
        assert degradada.reservation_wage_mxn_hr == 275.0, (
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
            FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=300.0, reasoning="llovio"))
        )
        layer.refresh_now(T0 + timedelta(minutes=30), CONTEXT)

        recuperada = layer.snapshot()
        assert recuperada.degraded is False
        assert recuperada.reservation_wage_mxn_hr == 300.0
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
            # La banda relativa es la que manda sobre una PROPUESTA: el modelo
            # no puede alejarse del umbral calibrado mas de lo permitido.
            (5.0, st.DEFAULT_RESERVATION_WAGE_MXN_HR * st.MIN_STRATEGY_MULTIPLIER),
            (99999.0, st.DEFAULT_RESERVATION_WAGE_MXN_HR * st.MAX_STRATEGY_MULTIPLIER),
            (275.0, 275.0),  # dentro de banda: pasa tal cual
        ],
    )
    def test_una_propuesta_absurda_queda_recortada(self, propuesto, esperado):
        layer = StrategyLayer(FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=propuesto)))
        layer.refresh_now(T0, CONTEXT)
        assert layer.snapshot().reservation_wage_mxn_hr == esperado

    def test_el_modelo_no_puede_tirar_el_turno(self):
        """El caso real que motivo la banda.

        Con la API de verdad, el modelo propuso bajar el umbral calibrado de
        $250 a $120 -- y lo justifico diciendo que lo *elevaba*. Segun nuestro
        propio barrido, ese umbral cuesta cerca de un 30% de las ganancias del
        turno, y llegaba con `degraded: false` y todo en verde. La banda existe
        para que un asesor que no ha visto la calibracion no pueda deshacerla.
        """
        layer = StrategyLayer(FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=120.0)))
        layer.refresh_now(T0, CONTEXT)
        piso = st.DEFAULT_RESERVATION_WAGE_MXN_HR * st.MIN_STRATEGY_MULTIPLIER
        assert layer.snapshot().reservation_wage_mxn_hr == pytest.approx(piso)
        assert layer.snapshot().reservation_wage_mxn_hr > 120.0

    def test_un_valor_grabado_NO_pasa_por_la_banda(self):
        """Un valor del log es un hecho, no una propuesta.

        Recortarlo al reinyectarlo haria que el replay produjera decisiones
        distintas de las grabadas -- justo lo que el diff del protocolo
        (seccion 6) existe para detectar.
        """
        layer = StrategyLayer(FakeAdvisor())
        fuera_de_banda = st.DEFAULT_RESERVATION_WAGE_MXN_HR * 0.5
        layer.apply_recorded({"reservation_wage_mxn_hr": fuera_de_banda})
        assert layer.snapshot().reservation_wage_mxn_hr == pytest.approx(fuera_de_banda)

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
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ModelUnavailable, match="GEMINI_API_KEY"):
            GeminiAdvisor().propose(CONTEXT)

    def test_una_key_vacia_cuenta_como_ausente(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "")
        with pytest.raises(ModelUnavailable):
            GeminiAdvisor().propose(CONTEXT)

    def test_el_advisor_no_cachea_el_entorno_al_construirse(self, monkeypatch):
        """Un cliente construido al importar sobreviviria a la invalidacion y
        el requisito quedaria simulado, no implementado."""
        monkeypatch.setenv("GEMINI_API_KEY", "clave-de-mentiras")
        advisor = GeminiAdvisor()  # construido CON key

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ModelUnavailable, match="GEMINI_API_KEY"):
            advisor.propose(CONTEXT)  # ...llamado SIN key

    def test_invalidar_la_key_a_media_corrida_degrada_y_restaurarla_recupera(self, monkeypatch):
        """El ensayo completo del protocolo, sin tocar la red.

        Se usa un advisor que mira el entorno igual que GeminiAdvisor, para
        recorrer el ciclo entero sin gastar una llamada real.
        """

        class AdvisorQueMiraElEntorno:
            name = "env-fake"

            def propose(self, context):
                if not os.environ.get("GEMINI_API_KEY"):
                    raise ModelUnavailable("GEMINI_API_KEY ausente o vacia en el entorno")
                return ModelProposal(reservation_wage_mxn_hr=275.0, reasoning="todo normal")

        monkeypatch.setenv("GEMINI_API_KEY", "clave-de-mentiras")
        layer = StrategyLayer(AdvisorQueMiraElEntorno())

        layer.refresh_now(T0, CONTEXT)
        assert layer.snapshot().degraded is False

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # los jueces
        layer.refresh_now(T0 + timedelta(minutes=30), CONTEXT)
        assert layer.snapshot().degraded is True
        assert layer.snapshot().reservation_wage_mxn_hr == 275.0

        monkeypatch.setenv("GEMINI_API_KEY", "clave-de-mentiras")  # restaurada
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
            time.sleep(0)  # cede el turno: sin esto un lector acapara el GIL

    lectores = [threading.Thread(target=leer, daemon=True) for _ in range(4)]
    for h in lectores:
        h.start()

    # Dentro de la banda relativa a proposito: si se recortaran, el test no
    # estaria comprobando la atomicidad de la publicacion sino el recorte, y
    # ademas solo fallaria cuando algun lector llegara a ver una publicacion
    # -- que es como paso desapercibido hasta ahora.
    publicados = [
        st.DEFAULT_RESERVATION_WAGE_MXN_HR * (0.80 + i * 0.02) for i in range(20)
    ]
    for i, wage in enumerate(publicados):
        layer.use_advisor(FakeAdvisor(ModelProposal(reservation_wage_mxn_hr=wage)))
        layer.refresh_now(T0 + timedelta(minutes=i), CONTEXT)
        # Sin esta pausa, `refresh_now` es tan rapido que el planificador puede
        # no darle turno a ningun lector entre publicaciones: el test pasaba
        # sin haber leido nada concurrente ni una vez.
        time.sleep(0.002)

    parar.set()
    for h in lectores:
        h.join(timeout=2)

    esperados = {round(w, 6) for w in publicados}
    vistos_modelo = 0
    for wage, source in vistos:
        assert source in ("bootstrap", "model")
        if source == "bootstrap":
            assert wage == st.DEFAULT_RESERVATION_WAGE_MXN_HR
        else:
            # Cada lectura tiene que ser un valor PUBLICADO, no una mezcla.
            assert round(wage, 6) in esperados, f"lectura a medias: {wage}"
            vistos_modelo += 1

    assert vistos, "los lectores no leyeron nada: el test no probo nada"
    assert vistos_modelo > 0, (
        "ningun lector alcanzo a ver una publicacion del modelo; sin eso este "
        "test pasa por suerte y no comprueba la atomicidad"
    )


# ==========================================================================
# El transporte HTTP contra Gemini
#
# Se prueba contra un servidor local que imita la API: verifica la forma de la
# peticion (que es lo que se descubriria tarde y en vivo) y el parseo de la
# respuesta, sin credencial y sin salir a internet.
# ==========================================================================


class TestTransporteGemini:
    @staticmethod
    def _servidor(handler_factory):
        """Levanta un servidor HTTP local y devuelve (url, registro, cerrar)."""
        import json as _json
        import threading as _threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        registro: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                largo = int(self.headers.get("Content-Length", 0))
                registro["body"] = _json.loads(self.rfile.read(largo).decode())
                registro["headers"] = dict(self.headers)
                codigo, cuerpo = handler_factory()
                datos = _json.dumps(cuerpo).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

            def log_message(self, *args):  # silencio en los tests
                return

        servidor = HTTPServer(("127.0.0.1", 0), Handler)
        hilo = _threading.Thread(target=servidor.serve_forever, daemon=True)
        hilo.start()
        url = f"http://127.0.0.1:{servidor.server_port}/v1beta/models/{{model}}:generateContent"
        return url, registro, servidor.shutdown

    def _advisor_contra(self, monkeypatch, url):
        monkeypatch.setenv("GEMINI_API_KEY", "clave-de-mentiras")
        monkeypatch.setattr(st, "GEMINI_ENDPOINT", url)
        return GeminiAdvisor(timeout_seconds=5)

    def test_manda_la_clave_en_la_cabecera_no_en_la_url(self, monkeypatch):
        """En la URL acabaria en los logs de acceso de cualquier proxy."""
        url, registro, cerrar = self._servidor(
            lambda: (200, {"candidates": [{"content": {"parts": [{"text": '{"reservation_wage_mxn_hr": 300}'}]}}]})
        )
        try:
            self._advisor_contra(monkeypatch, url).propose({"zona": 7})
        finally:
            cerrar()

        cabeceras = {k.lower(): v for k, v in registro["headers"].items()}
        assert cabeceras["x-goog-api-key"] == "clave-de-mentiras"
        assert "clave-de-mentiras" not in url

    def test_la_peticion_tiene_la_forma_que_espera_la_api(self, monkeypatch):
        """Es lo que se descubriria tarde: una clave mal escrita en el cuerpo
        no falla al importar, falla el dia de la demo."""
        url, registro, cerrar = self._servidor(
            lambda: (200, {"candidates": [{"content": {"parts": [{"text": '{"reservation_wage_mxn_hr": 300}'}]}}]})
        )
        try:
            self._advisor_contra(monkeypatch, url).propose({"zona": 7})
        finally:
            cerrar()

        cuerpo = registro["body"]
        assert "systemInstruction" in cuerpo
        assert cuerpo["generationConfig"]["responseMimeType"] == "application/json"
        assert "zona" in cuerpo["contents"][0]["parts"][0]["text"]

    def test_lee_la_propuesta_de_la_respuesta(self, monkeypatch):
        url, _, cerrar = self._servidor(
            lambda: (
                200,
                {"candidates": [{"content": {"parts": [{"text": '{"reservation_wage_mxn_hr": 312, "confidence": "high", "reasoning": "surge"}'}]}}]},
            )
        )
        try:
            propuesta = self._advisor_contra(monkeypatch, url).propose({})
        finally:
            cerrar()
        assert propuesta.reservation_wage_mxn_hr == 312.0
        assert propuesta.confidence == "high"

    def test_un_error_http_es_una_caida_con_su_codigo(self, monkeypatch):
        """400 con API_KEY_INVALID es la credencial revocada -- justo lo que
        hacen los jueces. El codigo tiene que llegar a status() para poder
        diagnosticarlo en vivo."""
        url, _, cerrar = self._servidor(
            lambda: (400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        )
        try:
            with pytest.raises(ModelUnavailable, match="HTTP 400"):
                self._advisor_contra(monkeypatch, url).propose({})
        finally:
            cerrar()

    def test_una_respuesta_con_otra_forma_es_una_caida_no_una_excepcion(self, monkeypatch):
        """Un modelo que contesta cualquier cosa es tan inservible como el que
        no contesta, y tiene que degradar por el mismo camino."""
        url, _, cerrar = self._servidor(lambda: (200, {"otra": "forma"}))
        try:
            with pytest.raises(ModelUnavailable, match="ilegible"):
                self._advisor_contra(monkeypatch, url).propose({})
        finally:
            cerrar()

    def test_una_caida_real_degrada_la_capa_y_conserva_el_umbral(self, monkeypatch):
        """El ciclo completo con transporte de verdad: la capa queda degradada
        y el salario de reserva no se mueve."""
        url, _, cerrar = self._servidor(lambda: (503, {"error": {"message": "overloaded"}}))
        try:
            layer = StrategyLayer(self._advisor_contra(monkeypatch, url))
            antes = layer.snapshot().reservation_wage_mxn_hr
            layer.refresh_now(T0, CONTEXT)
        finally:
            cerrar()

        assert layer.snapshot().degraded is True
        assert layer.snapshot().reservation_wage_mxn_hr == antes
        assert "HTTP 503" in layer.status().last_error
