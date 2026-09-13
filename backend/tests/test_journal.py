"""Tests de la bitacora de decisiones (core/agent/journal.py).

El test que mas vale de este archivo es
`test_los_eventos_pasan_el_validador_oficial`: no comprueba nuestra idea del
formato, corre `student-materials/courier/validate_format.py` de verdad, en un
subproceso, sobre un event log que produjo el journal.
"""

import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from api.schemas import DecideRequest
from core.agent.journal import (
    DecisionJournal,
    DecisionRecord,
    in_flight_totals,
    latest_in_flight_eta,
    queue_offset_min,
    to_decision_event,
    to_order_offered_event,
)
from core.agent.safety import SafetyVerdict, combine, evaluate_safety_full

EVENING = datetime(2026, 3, 21, 18, 42)
REPO = Path(__file__).resolve().parents[2]
VALIDATOR = REPO / "student-materials" / "courier" / "validate_format.py"


def make_request(order_id="ORD-0001", overrides=None, **campos) -> DecideRequest:
    payload = {
        "order_id": order_id,
        "platform": "rappi",
        "sim_time": EVENING.isoformat(),
        "zone_pickup": 0,
        "zone_dropoff": 1,
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
    payload.update(campos)
    if overrides is not None:
        payload["courier_state_overrides"] = overrides
    return DecideRequest(**payload)


def decide_and_record(journal, request, *, economic_accept=True, economics=None):
    """Camino completo, tal como lo ejecuta api/decide.py."""
    overrides = request.courier_state_overrides
    weight, volume = in_flight_totals(overrides.in_flight_orders)
    verdict = evaluate_safety_full(
        vehicle=request.vehicle,
        weight_kg=request.weight_kg,
        volume_liters=request.volume_liters,
        sim_time=request.sim_time,
        zone_dropoff=request.zone_dropoff,
        continuous_riding_min=overrides.continuous_riding_min,
        order_total_time_min=20.0,
        shift_end_time=overrides.shift_end_time,
        in_flight_weight_kg=weight,
        in_flight_volume_liters=volume,
        last_break_end_time=overrides.last_break_end_time,
        queue_offset_min=queue_offset_min(overrides.in_flight_orders, request.sim_time),
    )
    decision, reason, binding = combine(
        verdict,
        economic_accept=economic_accept,
        economic_reason="cumple el umbral de pago",
    )
    return journal.record(
        DecisionRecord(
            order=request,
            overrides=overrides,
            verdict=verdict,
            decision=decision,
            reason=reason,
            binding_constraint=binding,
            latency_ms=0.42,
            economics=economics,
        )
    )


# ==========================================================================
# Parseo de la mochila (in_flight_orders es list[dict] sin forma fijada)
# ==========================================================================


class TestMochilaEnVuelo:
    def test_suma_peso_y_volumen(self):
        assert in_flight_totals(
            [{"weight_kg": 3.0, "volume_liters": 8.0}, {"weight_kg": 1.5}]
        ) == (4.5, 8.0)

    def test_tolera_basura_sin_lanzar(self):
        """El material oficial no fija la forma ("el ejemplo solo muestra []")."""
        assert in_flight_totals(None) == (0.0, 0.0)
        assert in_flight_totals(["ORD-1", 42, {"weight_kg": "mucho"}, {}]) == (0.0, 0.0)

    def test_lee_la_eta_mas_tardia_en_iso_o_datetime(self):
        etas = [
            {"eta_dropoff": "2026-03-21T19:00:00"},
            {"eta_dropoff": datetime(2026, 3, 21, 19, 30)},
            {"eta_dropoff": "no es fecha"},
        ]
        assert latest_in_flight_eta(etas) == datetime(2026, 3, 21, 19, 30)

    def test_la_cola_nunca_es_negativa(self):
        pasado = [{"eta_dropoff": "2026-03-21T17:00:00"}]
        assert queue_offset_min(pasado, EVENING) == 0.0

    def test_la_cola_mide_lo_que_falta_para_terminar_lo_aceptado(self):
        futuro = [{"eta_dropoff": (EVENING + timedelta(minutes=25)).isoformat()}]
        assert queue_offset_min(futuro, EVENING) == pytest.approx(25.0)


# ==========================================================================
# Almacenamiento
# ==========================================================================


def test_guarda_y_recupera_por_order_id():
    journal = DecisionJournal()
    decide_and_record(journal, make_request("ORD-A"))
    assert journal.get("ORD-A") is not None
    assert journal.get("ORD-INEXISTENTE") is None
    assert journal.explain("ORD-INEXISTENTE") is None


def test_recent_devuelve_de_la_mas_nueva_a_la_mas_vieja():
    journal = DecisionJournal()
    for i in range(5):
        decide_and_record(journal, make_request(f"ORD-{i}"))
    assert [r.order_id for r in journal.recent(3)] == ["ORD-4", "ORD-3", "ORD-2"]


def test_no_crece_sin_techo():
    journal = DecisionJournal(max_records=10)
    for i in range(50):
        decide_and_record(journal, make_request(f"ORD-{i}"))
    assert len(journal) == 10
    assert journal.get("ORD-0") is None, "la mas vieja se tira primero"
    assert journal.get("ORD-49") is not None


def test_redecidir_el_mismo_pedido_gana_la_ultima():
    """Pasa al replayear un log sobre la misma bitacora."""
    journal = DecisionJournal()
    decide_and_record(journal, make_request("ORD-A"))
    decide_and_record(
        journal, make_request("ORD-A", overrides={"continuous_riding_min": 999.0})
    )
    assert len(journal) == 1
    assert journal.get("ORD-A").binding_constraint == "mandatory_break"


def test_es_seguro_entre_hilos():
    journal = DecisionJournal()

    def escribir(base: int) -> None:
        for i in range(100):
            decide_and_record(journal, make_request(f"ORD-{base}-{i}"))

    hilos = [threading.Thread(target=escribir, args=(h,)) for h in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert len(journal) == 800


def test_registrar_es_barato_corre_dentro_de_la_ventana_de_50ms():
    from time import perf_counter

    journal = DecisionJournal()
    request = make_request()
    plantilla = dict(
        order=request,
        overrides=request.courier_state_overrides,
        verdict=SafetyVerdict(),
        decision="ACCEPT",
        reason="ok",
        binding_constraint=None,
        latency_ms=0.1,
    )
    journal.record(DecisionRecord(**plantilla))

    peticiones = [make_request(f"ORD-{i}") for i in range(1000)]
    t0 = perf_counter()
    for peticion in peticiones:
        journal.record(DecisionRecord(**{**plantilla, "order": peticion}))
    promedio_ms = (perf_counter() - t0) * 1000.0 / 1000
    assert promedio_ms < 0.5, f"{promedio_ms:.4f} ms por registro"


# ==========================================================================
# explain_decision: forma exigida por el contrato
# ==========================================================================


class TestExplainDecision:
    def test_trae_las_cinco_claves_requeridas_por_el_schema(self):
        journal = DecisionJournal()
        decide_and_record(journal, make_request("ORD-A"))
        assert set(journal.explain("ORD-A")) == {
            "order_id", "decision", "reason", "inputs", "alternatives_considered",
        }

    def test_inputs_trae_lo_que_el_schema_enumera(self):
        """"position, time_remaining, time_to_completion, active strategy
        parameters, in-flight orders"."""
        journal = DecisionJournal()
        decide_and_record(
            journal,
            make_request(
                "ORD-A",
                overrides={
                    "shift_end_time": (EVENING + timedelta(hours=2)).isoformat(),
                    "in_flight_orders": [{"order_id": "ORD-PREV", "weight_kg": 1.0}],
                },
            ),
        )
        inputs = journal.explain("ORD-A")["inputs"]

        assert inputs["position_zone"] == 0
        assert inputs["courier_state"]["time_remaining_min"] == pytest.approx(120.0)
        assert inputs["time_to_completion_min"] > 0
        assert [o["order_id"] for o in inputs["in_flight_orders"]] == ["ORD-PREV"]
        assert inputs["courier_state"]["in_flight_weight_kg"] == 1.0
        assert "strategy" in inputs

    def test_inputs_publica_los_limites_vigentes(self):
        """La pregunta que sigue siempre es "¿contra que limite?"."""
        from core.agent import safety

        journal = DecisionJournal()
        decide_and_record(journal, make_request("ORD-A"))
        limites = journal.explain("ORD-A")["inputs"]["active_limits"]
        assert limites["mandatory_break_after_min"] == safety.MANDATORY_BREAK_AFTER_MIN
        assert limites["flagged_zones"] == sorted(safety.FLAGGED_ZONES)

    def test_explica_con_los_numeros_de_entonces_no_con_los_de_ahora(self):
        """Explicar no puede volver a decidir.

        Se registra un veredicto cuya estimacion es deliberadamente absurda; si
        `explain` recalculara, devolveria el numero real y no el registrado.
        """
        journal = DecisionJournal()
        request = make_request("ORD-A")
        journal.record(
            DecisionRecord(
                order=request,
                overrides=request.courier_state_overrides,
                verdict=SafetyVerdict(minutes_to_completion=1234.5),
                decision="ACCEPT",
                reason="ok",
                binding_constraint=None,
                latency_ms=0.1,
            )
        )
        assert journal.explain("ORD-A")["inputs"]["time_to_completion_min"] == 1234.5


class TestAlternativasConsideradas:
    def test_cada_alternativa_trae_las_dos_claves_del_schema(self):
        journal = DecisionJournal()
        decide_and_record(journal, make_request("ORD-A"))
        for alt in journal.explain("ORD-A")["alternatives_considered"]:
            assert set(alt) >= {"option", "rejected_because"}
            assert alt["option"] and alt["rejected_because"]

    def test_al_aceptar_la_alternativa_es_rechazar(self):
        journal = DecisionJournal()
        decide_and_record(journal, make_request("ORD-A"))
        assert journal.explain("ORD-A")["alternatives_considered"][0]["option"] == "SKIP"

    def test_al_rechazar_la_alternativa_es_aceptar_con_la_constraint_que_mordio(self):
        journal = DecisionJournal()
        decide_and_record(
            journal, make_request("ORD-A", overrides={"continuous_riding_min": 999.0})
        )
        alts = journal.explain("ORD-A")["alternatives_considered"]
        assert alts[0]["option"] == "ACCEPT"
        assert "Descanso obligatorio" in alts[0]["rejected_because"]

    def test_lista_las_constraints_que_seguirian_bloqueando(self):
        """Contesta "¿y si arreglo esa?" sin re-correr el sistema."""
        journal = DecisionJournal()
        decide_and_record(
            journal,
            make_request(
                "ORD-A",
                weight_kg=9999.0,
                overrides={"continuous_riding_min": 999.0},
            ),
        )
        alts = journal.explain("ORD-A")["alternatives_considered"]
        assert "ACCEPT tras resolver mandatory_break" in [a["option"] for a in alts]
        assert any("Capacidad de moto" in a["rejected_because"] for a in alts)

    def test_un_accept_explica_por_que_SI_cuando_hay_economia(self):
        """Sin la capa economica, un ACCEPT solo puede decir "nada lo bloqueo"."""
        journal = DecisionJournal()
        decide_and_record(
            journal,
            make_request("ORD-A"),
            economics={"adjusted_rate_mxn_hr": 220.0, "reservation_wage_mxn_hr": 150.0},
        )
        alts = journal.explain("ORD-A")["alternatives_considered"]
        assert any("$220/hr" in a["rejected_because"] for a in alts)


# ==========================================================================
# Event log JSONL
# ==========================================================================


class TestEventLog:
    def test_el_evento_decision_trae_las_claves_requeridas(self):
        journal = DecisionJournal()
        record = decide_and_record(journal, make_request("ORD-A"))
        evento = to_decision_event(record)
        for clave in ("event", "order_id", "sim_time", "decision", "reason", "latency_ms"):
            assert clave in evento
        assert evento["event"] == "decision"

    @pytest.mark.skipif(not VALIDATOR.exists(), reason="student-materials no esta en el repo")
    def test_los_eventos_pasan_el_validador_oficial(self, tmp_path):
        """Corre validate_format.py --event-log de verdad, en un subproceso."""
        journal = DecisionJournal()
        cierre = {"shift_end_time": (EVENING + timedelta(hours=2)).isoformat()}

        decide_and_record(journal, make_request("ORD-0001", overrides=cierre))
        decide_and_record(journal, make_request("ORD-0002", weight_kg=9999.0, overrides=cierre))
        decide_and_record(
            journal,
            make_request("ORD-0003", overrides={**cierre, "continuous_riding_min": 300.0}),
        )

        # shift_start es de Bloque 1; aqui se fabrica el minimo para que el
        # validador acepte el log como completo.
        lineas = [
            {
                "event": "shift_start",
                "sim_time": "2026-03-21T15:00:00",
                "seed": 1,
                "shift_hours": 8,
                "vehicle": "moto",
                "start_location_zone": 0,
                "shift_end_time": "2026-03-21T23:00:00",
            }
        ]
        for record in journal.all_records():
            lineas.append(to_order_offered_event(record))
            lineas.append(to_decision_event(record))

        log = tmp_path / "shift.jsonl"
        log.write_text("\n".join(json.dumps(linea) for linea in lineas) + "\n")

        resultado = subprocess.run(
            [sys.executable, str(VALIDATOR), "--event-log", str(log)],
            capture_output=True,
            text=True,
        )
        assert resultado.returncode == 0, resultado.stdout + resultado.stderr
        assert "PASS" in resultado.stdout
