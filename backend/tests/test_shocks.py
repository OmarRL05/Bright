"""Tests de los shocks en vivo (core/agent/shocks.py + POST /shock).

El protocolo pide dos cosas a la vez y es facil cumplir una sola:

    "Judges may inject shocks live... Your system must react without stalling
     the decision loop."
    -- evaluation_protocol.md, seccion 5

**Reaccionar** se prueba en `TestReaccionDelFastPath`: un shock inyectado
cambia la decision siguiente sobre la MISMA oferta. Sin esos tests, "lo
registramos" pasaria por implementado.

**Sin estancar** se prueba en `TestNoEstanca`: la latencia reportada sigue
dentro del presupuesto de 50 ms con shocks vigentes, y el registro no toma
locks en la lectura.

Y `TestFormatoOficial` corre `validate_format.py` de verdad, en un subproceso,
sobre un event log con los cuatro tipos de shock -- porque el evento `shock`
que emitimos tiene que ser el que el validador acepta, no el que creemos.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.agent.shocks import (
    CLOSURE_DETOUR_FACTOR,
    DEFAULT_SHOCK_DURATION_MIN,
    DELAY_DEFAULT_DURATION_MIN,
    MAX_SURGE_MULTIPLIER,
    RAIN_SPEED_FACTOR,
    SHOCKS,
    Shock,
    ShockRegistry,
    shock_from_payload,
)

EVENING = datetime(2026, 3, 21, 18, 42)
REPO = Path(__file__).resolve().parents[2]
VALIDATOR = REPO / "student-materials" / "courier" / "validate_format.py"


@pytest.fixture
def client():
    """Cliente sobre la app real, con el registro compartido limpio.

    Se resetea ANTES y DESPUES: `SHOCKS` es una instancia de proceso (como
    `JOURNAL` y `STRATEGY`), asi que un shock que sobreviva a un test
    contamina al siguiente y el fallo aparece lejos de su causa. `reset()` y
    no `clear()` porque el historial tambien se hereda.
    """
    from main import app

    SHOCKS.reset()
    with TestClient(app) as test_client:
        yield test_client
    SHOCKS.reset()


def order_payload(order_id="ORD-SHOCK-1", **campos):
    """Oferta base: rentable pero no por mucho, para que un shock la mueva."""
    payload = {
        "order_id": order_id,
        "platform": "rappi",
        "sim_time": EVENING.isoformat(),
        "zone_pickup": 0,
        "zone_dropoff": 1,
        "distance_pickup_km": 1.5,
        "distance_delivery_km": 4.0,
        "base_pay_mxn": 90.0,
        "est_tip_mxn": 10.0,
        "surge_multiplier": 1.0,
        "restaurant_prep_min": 5.0,
        "weight_kg": 2.0,
        "volume_liters": 6.0,
        "vehicle": "moto",
    }
    payload.update(campos)
    return payload


# ==========================================================================
# El registro
# ==========================================================================


class TestRegistro:
    def test_ventana_de_vigencia_es_semiabierta(self):
        shock = Shock("surge", sim_time=EVENING, zone=1, multiplier=1.6, duration_min=25)

        assert shock.active_at(EVENING) is True
        assert shock.active_at(EVENING + timedelta(minutes=24)) is True
        # A los 25 exactos ya no: [inicio, inicio+duracion).
        assert shock.active_at(EVENING + timedelta(minutes=25)) is False
        assert shock.active_at(EVENING - timedelta(minutes=1)) is False

    def test_shock_sin_sim_time_vige_siempre(self):
        """No hay contra que medir la ventana, y no se va a leer el reloj de
        pared solo para inventarle una (rompe el replay)."""
        shock = Shock("rain")
        assert shock.active_at(None) is True
        assert shock.active_at(EVENING) is True
        assert shock.active_at(EVENING + timedelta(days=3)) is True

    def test_surge_absurdo_se_recorta_en_vez_de_lanzar(self):
        registro = ShockRegistry()
        guardado = registro.inject(Shock("surge", sim_time=EVENING, zone=1, multiplier=100.0))
        assert guardado.multiplier == MAX_SURGE_MULTIPLIER

    def test_delay_dura_mas_que_los_demas_por_defecto(self):
        """Un retraso es un hecho sobre un pedido, no un estado del mundo que
        pasa: si el restaurante va tarde, sigue yendo tarde cuando ese pedido
        se decida."""
        assert shock_from_payload({"shock_type": "delay", "order_id": "X"}).duration_min == (
            DELAY_DEFAULT_DURATION_MIN
        )
        assert shock_from_payload({"shock_type": "rain"}).duration_min == (
            DEFAULT_SHOCK_DURATION_MIN
        )

    def test_tipo_invalido_devuelve_none(self):
        assert shock_from_payload({"shock_type": "terremoto"}) is None
        assert shock_from_payload({}) is None

    def test_payload_tolera_basura_sin_lanzar(self):
        shock = shock_from_payload(
            {"shock_type": "surge", "zone": "no-un-numero", "multiplier": None, "extra": 1}
        )
        assert shock is not None
        assert shock.zone is None

    def test_clear_vacia_vigentes_pero_conserva_historial(self):
        registro = ShockRegistry()
        registro.inject(Shock("rain", sim_time=EVENING))
        assert registro.clear() == 1
        assert len(registro.all_shocks()) == 0
        assert len(registro.history()) == 1

    def test_apply_recorded_reinyecta_desde_el_log(self):
        """Replay: los shocks vuelven del log grabado, no de que alguien los
        pinche otra vez a mano en el mismo minuto (protocolo, seccion 6)."""
        registro = ShockRegistry()
        original = Shock("surge", sim_time=EVENING, zone=2, multiplier=1.6, duration_min=25)
        vuelto = registro.apply_recorded(original.to_event())

        assert vuelto is not None
        assert vuelto.to_event() == original.to_event()
        assert registro.apply_recorded({"event": "order_offered"}) is None


# ==========================================================================
# Combinacion de efectos
# ==========================================================================


class TestEfectos:
    def test_surge_no_se_apila_se_toma_el_mayor(self):
        """Dos surges en la misma zona son el mismo evento visto dos veces.
        Multiplicarlos volveria rentable cualquier pedido con dos curl."""
        registro = ShockRegistry()
        registro.inject(Shock("surge", sim_time=EVENING, zone=1, multiplier=1.4))
        registro.inject(Shock("surge", sim_time=EVENING, zone=1, multiplier=1.8))

        efectos = registro.effects(EVENING, zone_pickup=1, zone_dropoff=2)
        assert efectos.surge_multiplier == 1.8

    def test_surge_solo_toca_su_zona(self):
        registro = ShockRegistry()
        registro.inject(Shock("surge", sim_time=EVENING, zone=11, multiplier=2.0))

        assert registro.effects(EVENING, zone_pickup=0, zone_dropoff=1).surge_multiplier == 1.0
        assert registro.effects(EVENING, zone_pickup=11, zone_dropoff=1).surge_multiplier == 2.0
        # Tambien cuenta si es la zona DONDE TERMINA: el surge de esa zona es
        # lo que hace valioso acabar ahi.
        assert registro.effects(EVENING, zone_pickup=0, zone_dropoff=11).surge_multiplier == 2.0

    def test_cierres_si_se_componen(self):
        """Rodear dos zonas cerradas son efectivamente dos desvios."""
        registro = ShockRegistry()
        registro.inject(Shock("closure", sim_time=EVENING, zone=0))
        registro.inject(Shock("closure", sim_time=EVENING, zone=1))

        efectos = registro.effects(EVENING, zone_pickup=0, zone_dropoff=1)
        assert efectos.distance_factor == pytest.approx(CLOSURE_DETOUR_FACTOR**2)

    def test_lluvia_no_se_apila(self):
        registro = ShockRegistry()
        registro.inject(Shock("rain", sim_time=EVENING))
        registro.inject(Shock("rain", sim_time=EVENING))

        assert registro.effects(EVENING).speed_factor == RAIN_SPEED_FACTOR

    def test_delay_solo_toca_al_pedido_que_nombra(self):
        registro = ShockRegistry()
        registro.inject(Shock("delay", sim_time=EVENING, order_id="ORD-9", slip_min=15))

        assert registro.effects(EVENING, order_id="ORD-9").extra_prep_min == 15.0
        assert registro.effects(EVENING, order_id="ORD-1").extra_prep_min == 0.0

    def test_delay_sin_order_id_no_mueve_numeros(self):
        """No se puede atribuir a nadie. Queda en el historial, no en la
        aritmetica: inventar a quien culpar es peor que no aplicarlo."""
        registro = ShockRegistry()
        registro.inject(Shock("delay", sim_time=EVENING, slip_min=15))
        assert registro.effects(EVENING, order_id="ORD-1").extra_prep_min == 0.0

    def test_shock_caducado_deja_de_aplicar(self):
        registro = ShockRegistry()
        registro.inject(Shock("surge", sim_time=EVENING, zone=1, multiplier=2.0, duration_min=25))

        despues = EVENING + timedelta(minutes=30)
        assert registro.effects(despues, zone_pickup=1).surge_multiplier == 1.0
        assert registro.effects(despues, zone_pickup=1).applied == ()


# ==========================================================================
# El endpoint
# ==========================================================================


class TestEndpoint:
    def test_inyeccion_basica(self, client):
        respuesta = client.post(
            "/shock",
            json={
                "shock_type": "surge",
                "sim_time": EVENING.isoformat(),
                "zone": 11,
                "multiplier": 1.6,
                "duration_min": 25,
            },
        )
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["accepted"] is True
        assert cuerpo["shock"]["event"] == "shock"
        assert cuerpo["shock"]["shock_type"] == "surge"
        assert cuerpo["active_shocks"] == 1

    def test_acepta_una_linea_copiada_del_event_log(self, client):
        """El body es el mismo objeto que el log emite. Un `event: "shock"` de
        mas -- que es justo lo que trae una linea copiada -- no puede ser 422."""
        linea = {
            "event": "shock",
            "sim_time": "2026-03-21T18:20:00",
            "shock_type": "surge",
            "zone": 11,
            "multiplier": 1.6,
            "duration_min": 25,
        }
        assert client.post("/shock", json=linea).status_code == 200

    def test_tipo_invalido_es_422_no_500(self, client):
        respuesta = client.post("/shock", json={"shock_type": "terremoto"})
        assert respuesta.status_code == 422

    def test_multiplicador_recortado_se_avisa_en_la_respuesta(self, client):
        respuesta = client.post(
            "/shock",
            json={"shock_type": "surge", "sim_time": EVENING.isoformat(), "zone": 1, "multiplier": 50},
        )
        cuerpo = respuesta.json()
        assert cuerpo["shock"]["multiplier"] == MAX_SURGE_MULTIPLIER
        assert "recortado" in cuerpo["note"]

    def test_listar_y_limpiar(self, client):
        client.post("/shock", json={"shock_type": "rain", "sim_time": EVENING.isoformat()})

        activos = client.get("/shocks", params={"at": EVENING.isoformat()}).json()
        assert len(activos["active"]) == 1

        limpio = client.delete("/shocks").json()
        assert limpio["active"] == []
        # El historial sobrevive: sirve para reconstruir lo que se inyecto.
        assert len(limpio["history"]) == 1

    def test_listar_respeta_la_ventana(self, client):
        client.post(
            "/shock",
            json={
                "shock_type": "surge",
                "sim_time": EVENING.isoformat(),
                "zone": 1,
                "multiplier": 1.5,
                "duration_min": 25,
            },
        )
        tarde = (EVENING + timedelta(minutes=40)).isoformat()
        assert client.get("/shocks", params={"at": tarde}).json()["active"] == []


# ==========================================================================
# Reaccion del fast path -- el corazon del requisito
# ==========================================================================


class TestReaccionDelFastPath:
    def test_surge_en_vivo_convierte_un_skip_en_accept(self, client):
        """La demostracion de la seccion 5 del protocolo, en un test.

        MISMA oferta, dos veces. Lo unico que cambia entre las dos es el
        `POST /shock` de en medio.
        """
        oferta = order_payload(base_pay_mxn=55.0, est_tip_mxn=0.0)

        antes = client.post("/decide", json=oferta).json()
        assert antes["decision"] == "SKIP"
        assert antes["binding_constraint"] == "reservation_wage"

        client.post(
            "/shock",
            json={
                "shock_type": "surge",
                "sim_time": EVENING.isoformat(),
                "zone": 0,
                "multiplier": 3.0,
                "duration_min": 60,
            },
        )

        despues = client.post("/decide", json=oferta).json()
        assert despues["decision"] == "ACCEPT"
        # Y el reason lo dice: sin esto, la demo muestra dos decisiones
        # distintas sobre entradas identicas y el credito se pierde.
        assert "shock" in despues["reason"]
        assert despues["shocks_applied"]

    def test_lluvia_alarga_el_pedido(self, client):
        oferta = order_payload()

        antes = client.post("/decide", json=oferta).json()["economics"]["total_time_min"]
        client.post("/shock", json={"shock_type": "rain", "sim_time": EVENING.isoformat()})
        despues = client.post("/decide", json=oferta).json()["economics"]["total_time_min"]

        assert despues > antes

    def test_cierre_suma_kilometraje_y_combustible(self, client):
        oferta = order_payload()

        antes = client.post("/decide", json=oferta).json()["economics"]
        client.post(
            "/shock",
            json={"shock_type": "closure", "sim_time": EVENING.isoformat(), "zone": 0, "road": "Constitucion"},
        )
        despues = client.post("/decide", json=oferta).json()["economics"]

        assert despues["total_km"] == pytest.approx(antes["total_km"] * CLOSURE_DETOUR_FACTOR)
        assert despues["fuel_cost_mxn"] > antes["fuel_cost_mxn"]

    def test_delay_de_restaurante_alarga_solo_su_pedido(self, client):
        """"What if this order's restaurant is running 15 minutes late?" es una
        de las preguntas escritas del material."""
        suyo = order_payload("ORD-TARDE")
        ajeno = order_payload("ORD-NORMAL")

        base = client.post("/decide", json=suyo).json()["economics"]["total_time_min"]
        client.post(
            "/shock",
            json={
                "shock_type": "delay",
                "sim_time": EVENING.isoformat(),
                "order_id": "ORD-TARDE",
                "slip_min": 15,
            },
        )

        assert client.post("/decide", json=suyo).json()["economics"]["total_time_min"] > base
        assert client.post("/decide", json=ajeno).json()["economics"]["total_time_min"] == (
            pytest.approx(base)
        )

    def test_shock_puede_disparar_una_constraint_de_seguridad(self, client):
        """La reaccion al shock PASA por el gate, no lo esquiva.

        Un pedido que cabia justo antes del cierre del turno deja de caber
        cuando llueve, porque la lluvia alarga el trayecto.
        """
        oferta = order_payload(
            distance_delivery_km=12.0,
            courier_state_overrides={
                "shift_end_time": (EVENING + timedelta(minutes=42)).isoformat(),
            },
        )

        antes = client.post("/decide", json=oferta).json()
        assert antes["binding_constraint"] != "shift_end_infeasible"

        client.post("/shock", json={"shock_type": "rain", "sim_time": EVENING.isoformat()})

        despues = client.post("/decide", json=oferta).json()
        assert despues["decision"] == "SKIP"
        assert despues["binding_constraint"] == "shift_end_infeasible"

    def test_safety_over_pay_sobrevive_a_un_surge_de_3x(self, client):
        """La invariante que los jueces prueban a proposito.

        Un surge maximo no puede convertir un refusal de seguridad en ACCEPT,
        porque el gate no recibe el multiplicador: no tiene forma de enterarse
        de que mejoro la paga.
        """
        imposible = order_payload(weight_kg=99.0, base_pay_mxn=500.0)

        client.post(
            "/shock",
            json={
                "shock_type": "surge",
                "sim_time": EVENING.isoformat(),
                "zone": 0,
                "multiplier": 3.0,
                "duration_min": 60,
            },
        )

        respuesta = client.post("/decide", json=imposible).json()
        assert respuesta["decision"] == "SKIP"
        assert respuesta["binding_constraint"] == "vehicle_capacity"

    def test_explain_decision_guarda_los_shocks_del_momento(self, client):
        """"¿Por que esta oferta si, y la de hace un minuto no?" se contesta
        desde la bitacora, con los shocks que habia AL DECIDIR."""
        client.post(
            "/shock",
            json={
                "shock_type": "surge",
                "sim_time": EVENING.isoformat(),
                "zone": 0,
                "multiplier": 2.0,
                "duration_min": 60,
            },
        )
        client.post("/decide", json=order_payload("ORD-EXPLICA"))

        inputs = client.get("/explain_decision/ORD-EXPLICA").json()["inputs"]
        assert inputs["shocks"]["surge_multiplier"] == 2.0
        assert inputs["shocks"]["applied"]


# ==========================================================================
# Sin estancar el loop
# ==========================================================================


class TestNoEstanca:
    def test_la_latencia_sigue_en_presupuesto_con_shocks_vigentes(self, client):
        """El presupuesto del fast path son 50 ms y el validador lo comprueba.

        Se inyectan varios shocks a proposito: el pliegue de efectos recorre
        la tupla completa en cada decision, asi que si eso costara, aqui se
        veria.
        """
        for i in range(10):
            client.post(
                "/shock",
                json={
                    "shock_type": "surge",
                    "sim_time": EVENING.isoformat(),
                    "zone": i,
                    "multiplier": 1.5,
                    "duration_min": 60,
                },
            )

        respuesta = client.post("/decide", json=order_payload()).json()
        assert respuesta["latency_ms"] < 50

    def test_mismo_shock_misma_decision(self, client):
        """Determinismo: el registro no guarda nada que dependa del reloj de
        pared, asi que dos pings identicos bajo el mismo shock coinciden."""
        client.post(
            "/shock",
            json={
                "shock_type": "surge",
                "sim_time": EVENING.isoformat(),
                "zone": 0,
                "multiplier": 1.7,
                "duration_min": 60,
            },
        )
        oferta = order_payload()

        primera = client.post("/decide", json=oferta).json()
        segunda = client.post("/decide", json=oferta).json()

        for campo in ("decision", "reason", "binding_constraint", "shocks_applied"):
            assert primera[campo] == segunda[campo]
        assert primera["economics"] == segunda["economics"]


# ==========================================================================
# Formato oficial
# ==========================================================================


class TestFormatoOficial:
    def test_los_cuatro_tipos_pasan_el_validador_real(self, tmp_path):
        """Corre `validate_format.py` de verdad, en un subproceso.

        Un event log necesita `shift_start` y `order_offered` para que el
        validador no se queje de otra cosa; lo que se esta probando son las
        cuatro lineas de `shock` de en medio.
        """
        registro = ShockRegistry()
        registro.inject(Shock("surge", sim_time=EVENING, zone=11, multiplier=1.6, duration_min=25))
        registro.inject(Shock("closure", sim_time=EVENING, zone=2, road="Constitucion"))
        registro.inject(Shock("rain", sim_time=EVENING, duration_min=45))
        registro.inject(Shock("delay", sim_time=EVENING, order_id="ORD-0001", slip_min=15))

        log = tmp_path / "shocks.jsonl"
        lineas = [
            {
                "event": "shift_start",
                "sim_time": "2026-03-21T15:00:00",
                "seed": 1,
                "shift_hours": 8,
                "vehicle": "moto",
                "start_location_zone": 7,
                "shift_end_time": "2026-03-21T23:00:00",
            },
            {
                "event": "order_offered",
                "order_id": "ORD-0001",
                "sim_time": EVENING.isoformat(),
                "zone_pickup": 7,
                "zone_dropoff": 11,
                "distance_pickup_km": 1.4,
                "distance_delivery_km": 6.5,
                "base_pay_mxn": 58.0,
                "surge_multiplier": 1.3,
                "vehicle": "moto",
            },
            *[s.to_event() for s in registro.all_shocks()],
        ]
        log.write_text(
            "\n".join(json.dumps(linea, ensure_ascii=False) for linea in lineas) + "\n",
            encoding="utf-8",
        )

        proceso = subprocess.run(
            [sys.executable, str(VALIDATOR), "--event-log", str(log)],
            capture_output=True,
            text=True,
        )
        assert proceso.returncode == 0, proceso.stdout + proceso.stderr
        assert "shock=4" in proceso.stdout
