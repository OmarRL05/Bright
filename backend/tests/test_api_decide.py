"""Tests del endpoint contra el contrato oficial, con el servidor real.

Usan `TestClient`, asi que ejercitan el ciclo completo -- pydantic, el
handler, la serializacion -- y no solo las funciones por dentro. Las trampas
que cubren son las que el material castiga y que no se ven llamando al gate
directamente: campos extra, overrides ausentes, y el 500.
"""

import json

import pytest
from fastapi.testclient import TestClient

from core.agent.journal import JOURNAL
from core.agent.strategy import STRATEGY, GeminiAdvisor, NullAdvisor, StrategyLayer

PROBE = {
    "order_id": "FORMAT-PROBE-001",
    "platform": "rappi",
    "sim_time": "2026-03-21T18:42:00",
    "zone_pickup": 7,
    "zone_dropoff": 11,
    "distance_pickup_km": 1.4,
    "distance_delivery_km": 6.5,
    "base_pay_mxn": 58.0,
    "est_tip_mxn": 12.0,
    "surge_multiplier": 1.3,
    "restaurant_prep_min": 9,
    "weight_kg": 2.1,
    "volume_liters": 6.0,
    "vehicle": "moto",
}


@pytest.fixture
def client(monkeypatch):
    """Cliente con la capa de estrategia aislada.

    `STRATEGY` es una instancia compartida del proceso: sin aislarla, un test
    que la degrada contamina a los siguientes.
    """
    monkeypatch.setattr(STRATEGY, "_advisor", NullAdvisor())
    monkeypatch.setattr(STRATEGY, "_params", STRATEGY.snapshot())
    JOURNAL.clear()
    import main

    with TestClient(main.app) as c:
        yield c


class TestContratoHTTP:
    def test_el_probe_del_validador_responde_200_y_decide(self, client):
        """El PROBE no manda `courier_state_overrides`; si fuera requerido,
        `validate_format.py --endpoint` quedaria en rojo."""
        r = client.post("/decide", json=PROBE)
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["decision"] in ("ACCEPT", "SKIP")
        assert cuerpo["order_id"] == "FORMAT-PROBE-001"
        assert len(cuerpo["reason"].split()) <= 40
        assert cuerpo["latency_ms"] < 50

    def test_los_campos_desconocidos_no_son_un_422(self, client):
        """Los jueces pueden mandar campos que no conocemos. Un modelo
        pydantic estricto devolveria 422 y el validador quedaria en rojo."""
        payload = {**PROBE, "campo_inventado": 123, "otro": {"anidado": True}}
        assert client.post("/decide", json=payload).status_code == 200

    def test_los_overrides_se_aplican_no_se_ignoran(self, client):
        """"Your system must apply these rather than ignoring them"."""
        payload = {**PROBE, "courier_state_overrides": {"continuous_riding_min": 500}}
        cuerpo = client.post("/decide", json=payload).json()
        assert cuerpo["decision"] == "SKIP"
        assert cuerpo["binding_constraint"] == "mandatory_break"

    def test_nunca_devuelve_500_ante_un_fallo_interno(self, client, monkeypatch):
        """Un crash en la ventana de decision es fallo duro de Feasibility."""
        import api.decide as modulo

        def explota(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(modulo, "evaluate_safety_full", explota)
        r = client.post("/decide", json={**PROBE, "order_id": "ERR-1"})
        assert r.status_code == 200
        assert r.json()["decision"] == "SKIP"
        assert r.json()["reason"].strip()

    def test_un_fallo_interno_igual_queda_explicable(self, client, monkeypatch):
        """Sin esto, "¿por que saltaste esa?" justo despues de un error
        devuelve 404 y parece que perdimos la decision."""
        import api.decide as modulo

        monkeypatch.setattr(
            modulo, "evaluate_safety_full", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        client.post("/decide", json={**PROBE, "order_id": "ERR-2"})

        r = client.get("/explain_decision/ERR-2")
        assert r.status_code == 200
        assert r.json()["decision"] == "SKIP"
        assert "RuntimeError" in json.dumps(r.json()["inputs"]["economics"])

    def test_explain_decision_devuelve_las_cinco_claves(self, client):
        client.post("/decide", json={**PROBE, "order_id": "EXP-1"})
        cuerpo = client.get("/explain_decision/EXP-1").json()
        assert set(cuerpo) == {
            "order_id", "decision", "reason", "inputs", "alternatives_considered",
        }

    def test_explain_de_un_pedido_inexistente_es_404(self, client):
        assert client.get("/explain_decision/NO-EXISTE").status_code == 404


class TestModoDegradado:
    def test_sin_credencial_al_arrancar_no_se_reporta_degradado(self, monkeypatch):
        """"Sin modelo configurado" NO es lo mismo que "el modelo se cayo".

        Si se conecta GeminiAdvisor sin credencial, el primer refresco falla y
        TODAS las respuestas salen con `degraded: true` desde el primer ping.
        Un juez lo lee como "este sistema esta roto", que es lo contrario de lo
        que el flag significa -- y ademas mata el ensayo, porque no se puede
        demostrar la transicion sano -> degradado si arranca degradado.
        """
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        capa = StrategyLayer()
        monkeypatch.setattr("core.agent.strategy.STRATEGY", capa)

        import main

        with TestClient(main.app) as c:
            assert c.get("/status").json()["degraded"] is False
            assert c.post("/decide", json=PROBE).json()["degraded"] is False

    def test_con_credencial_al_arrancar_si_se_conecta_el_advisor(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "clave-de-mentiras")
        capa = StrategyLayer()
        monkeypatch.setattr("core.agent.strategy.STRATEGY", capa)
        monkeypatch.setattr("main.STRATEGY", capa)

        import main

        with TestClient(main.app):
            assert isinstance(capa._advisor, GeminiAdvisor)

    def test_el_flag_degradado_viaja_a_la_respuesta(self, client, monkeypatch):
        from core.agent.strategy import ModelUnavailable

        class Caido:
            name = "caido"

            def propose(self, context):
                raise ModelUnavailable("sin red")

        STRATEGY.use_advisor(Caido())
        STRATEGY.refresh_now(None, {})

        cuerpo = client.post("/decide", json={**PROBE, "order_id": "DEG-1"}).json()
        assert cuerpo["degraded"] is True
        assert cuerpo["decision"] in ("ACCEPT", "SKIP"), "sigue decidiendo estando degradado"
        assert cuerpo["latency_ms"] < 50, "y dentro del presupuesto"


class TestStatus:
    def test_expone_lo_que_el_protocolo_pide_señalar(self, client):
        cuerpo = client.get("/status").json()
        for clave in ("degraded", "reservation_wage_mxn_hr", "advisor", "decisions_recorded"):
            assert clave in cuerpo
