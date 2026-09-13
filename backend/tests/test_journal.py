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

from core.agent.contracts import CourierRuntimeState, InFlightOrder, OrderRequest
from core.agent.journal import (
    DecisionJournal,
    DecisionRecord,
    to_decision_event,
    to_order_offered_event,
)
from core.agent.safety import CompletionEstimate, SafetyVerdict, combine, evaluate_safety

EVENING = datetime(2026, 3, 21, 18, 42)
REPO = Path(__file__).resolve().parents[2]
VALIDATOR = REPO / "student-materials" / "courier" / "validate_format.py"


def make_order(order_id="ORD-0001", **overrides) -> OrderRequest:
    payload = {
        "order_id": order_id,
        "platform": "rappi",
        "sim_time": EVENING.isoformat(),
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
    payload.update(overrides)
    return OrderRequest.from_payload(payload)


def decide_and_record(
    journal: DecisionJournal,
    order: OrderRequest,
    state: CourierRuntimeState,
    *,
    economic_accept: bool = True,
    economics: dict | None = None,
) -> DecisionRecord:
    """Camino completo tal como lo va a ejecutar Persona 3 en el endpoint."""
    verdict = evaluate_safety(order, state)
    decision, reason, binding = combine(
        verdict,
        economic_accept=economic_accept,
        economic_reason="cumple el umbral de pago",
    )
    return journal.record(
        DecisionRecord(
            order=order,
            state=state,
            verdict=verdict,
            decision=decision,
            reason=reason,
            binding_constraint=binding,
            latency_ms=0.42,
            economics=economics,
        )
    )


# ==========================================================================
# Almacenamiento
# ==========================================================================


def test_guarda_y_recupera_por_order_id():
    journal = DecisionJournal()
    decide_and_record(journal, make_order("ORD-A"), CourierRuntimeState())
    assert journal.get("ORD-A") is not None
    assert journal.get("ORD-INEXISTENTE") is None
    assert journal.explain("ORD-INEXISTENTE") is None


def test_recent_devuelve_de_la_mas_nueva_a_la_mas_vieja():
    journal = DecisionJournal()
    for i in range(5):
        decide_and_record(journal, make_order(f"ORD-{i}"), CourierRuntimeState())
    assert [r.order_id for r in journal.recent(3)] == ["ORD-4", "ORD-3", "ORD-2"]


def test_no_crece_sin_techo():
    journal = DecisionJournal(max_records=10)
    for i in range(50):
        decide_and_record(journal, make_order(f"ORD-{i}"), CourierRuntimeState())
    assert len(journal) == 10
    assert journal.get("ORD-0") is None, "la mas vieja se tira primero"
    assert journal.get("ORD-49") is not None


def test_redecidir_el_mismo_pedido_gana_la_ultima():
    """Pasa al replayear un log sobre la misma bitacora."""
    journal = DecisionJournal()
    decide_and_record(journal, make_order("ORD-A"), CourierRuntimeState())
    decide_and_record(
        journal,
        make_order("ORD-A"),
        CourierRuntimeState(continuous_riding_min=999.0),
    )
    assert len(journal) == 1
    assert journal.get("ORD-A").binding_constraint == "mandatory_break"


def test_es_seguro_entre_hilos():
    journal = DecisionJournal()

    def escribir(base: int) -> None:
        for i in range(100):
            decide_and_record(journal, make_order(f"ORD-{base}-{i}"), CourierRuntimeState())

    hilos = [threading.Thread(target=escribir, args=(h,)) for h in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert len(journal) == 800


def test_registrar_es_barato_corre_dentro_de_la_ventana_de_50ms():
    from time import perf_counter

    journal = DecisionJournal()
    order, state = make_order(), CourierRuntimeState()
    verdict = evaluate_safety(order, state)
    plantilla = dict(
        order=order, state=state, verdict=verdict, decision="ACCEPT",
        reason="ok", binding_constraint=None, latency_ms=0.1,
    )
    journal.record(DecisionRecord(**plantilla))

    t0 = perf_counter()
    for i in range(1000):
        journal.record(DecisionRecord(**{**plantilla, "order": make_order(f"ORD-{i}")}))
    promedio_ms = (perf_counter() - t0) * 1000.0 / 1000
    assert promedio_ms < 0.5, f"{promedio_ms:.4f} ms por registro"


# ==========================================================================
# explain_decision: forma exigida por el contrato
# ==========================================================================


class TestExplainDecision:
    def test_trae_las_cinco_claves_requeridas_por_el_schema(self):
        journal = DecisionJournal()
        decide_and_record(journal, make_order("ORD-A"), CourierRuntimeState())
        payload = journal.explain("ORD-A")
        assert set(payload) == {
            "order_id", "decision", "reason", "inputs", "alternatives_considered",
        }

    def test_inputs_trae_lo_que_el_schema_enumera(self):
        """"position, time_remaining, time_to_completion, active strategy
        parameters, in-flight orders"."""
        journal = DecisionJournal()
        state = CourierRuntimeState(
            current_zone=7,
            shift_end_time=EVENING + timedelta(hours=2),
            in_flight_orders=(InFlightOrder(order_id="ORD-PREV", weight_kg=1.0),),
        )
        decide_and_record(journal, make_order("ORD-A"), state)
        inputs = journal.explain("ORD-A")["inputs"]

        assert inputs["position_zone"] == 7
        assert inputs["courier_state"]["time_remaining_min"] == pytest.approx(120.0)
        assert inputs["time_to_completion_min"] > 0
        assert [o["order_id"] for o in inputs["in_flight_orders"]] == ["ORD-PREV"]
        assert "strategy" in inputs

    def test_inputs_publica_los_limites_vigentes(self):
        """La pregunta que sigue siempre es "¿contra que limite?"."""
        from core.agent import safety

        journal = DecisionJournal()
        decide_and_record(journal, make_order("ORD-A"), CourierRuntimeState())
        limites = journal.explain("ORD-A")["inputs"]["active_limits"]
        assert limites["max_continuous_riding_min"] == safety.MAX_CONTINUOUS_RIDING_MIN
        assert limites["flagged_zones"] == sorted(safety.FLAGGED_ZONES)

    def test_explica_con_los_numeros_de_entonces_no_con_los_de_ahora(self):
        """Explicar no puede volver a decidir.

        Se registra un veredicto cuya estimacion es deliberadamente absurda; si
        `explain` recalculara, devolveria el numero real y no el registrado.
        """
        journal = DecisionJournal()
        order, state = make_order("ORD-A"), CourierRuntimeState()
        journal.record(
            DecisionRecord(
                order=order,
                state=state,
                verdict=SafetyVerdict(estimate=CompletionEstimate(minutes_to_dropoff=1234.5)),
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
        decide_and_record(journal, make_order("ORD-A"), CourierRuntimeState())
        for alt in journal.explain("ORD-A")["alternatives_considered"]:
            assert set(alt) >= {"option", "rejected_because"}
            assert alt["option"] and alt["rejected_because"]

    def test_al_aceptar_la_alternativa_es_rechazar(self):
        journal = DecisionJournal()
        decide_and_record(journal, make_order("ORD-A"), CourierRuntimeState())
        alts = journal.explain("ORD-A")["alternatives_considered"]
        assert alts[0]["option"] == "SKIP"

    def test_al_rechazar_la_alternativa_es_aceptar_con_la_constraint_que_mordio(self):
        journal = DecisionJournal()
        decide_and_record(
            journal, make_order("ORD-A"), CourierRuntimeState(continuous_riding_min=999.0)
        )
        alts = journal.explain("ORD-A")["alternatives_considered"]
        assert alts[0]["option"] == "ACCEPT"
        assert "Descanso obligatorio" in alts[0]["rejected_because"]

    def test_lista_las_constraints_que_seguirian_bloqueando(self):
        """Contesta "¿y si arreglo esa?" sin re-correr el sistema."""
        journal = DecisionJournal()
        order = make_order("ORD-A", sim_time="2026-03-21T13:00:00", weight_kg=99.0)
        decide_and_record(journal, order, CourierRuntimeState(continuous_riding_min=999.0))

        alts = journal.explain("ORD-A")["alternatives_considered"]
        opciones = [a["option"] for a in alts]
        assert "ACCEPT tras resolver mandatory_break" in opciones
        assert any("Capacidad de moto" in a["rejected_because"] for a in alts)

    def test_publica_la_aritmetica_economica_cuando_existe(self):
        """El hueco con nombre: hoy llega en None, y conectarlo es un argumento."""
        journal = DecisionJournal()
        decide_and_record(
            journal,
            make_order("ORD-A"),
            CourierRuntimeState(),
            economics={"adjusted_rate_mxn_hr": 140.0, "reservation_wage_mxn_hr": 185.0},
        )
        alts = journal.explain("ORD-A")["alternatives_considered"]
        assert any("$140/hr" in a["rejected_because"] for a in alts)


# ==========================================================================
# Event log JSONL
# ==========================================================================


class TestEventLog:
    def test_el_evento_decision_trae_las_claves_requeridas(self):
        journal = DecisionJournal()
        record = decide_and_record(journal, make_order("ORD-A"), CourierRuntimeState())
        evento = to_decision_event(record)
        for clave in ("event", "order_id", "sim_time", "decision", "reason", "latency_ms"):
            assert clave in evento
        assert evento["event"] == "decision"

    @pytest.mark.skipif(not VALIDATOR.exists(), reason="student-materials no esta en el repo")
    def test_los_eventos_pasan_el_validador_oficial(self, tmp_path):
        """Corre validate_format.py --event-log de verdad, en un subproceso."""
        journal = DecisionJournal()
        state = CourierRuntimeState(shift_end_time=EVENING + timedelta(hours=2))

        decide_and_record(journal, make_order("ORD-0001"), state)
        decide_and_record(journal, make_order("ORD-0002", weight_kg=99.0), state)
        decide_and_record(
            journal,
            make_order("ORD-0003", sim_time="2026-03-21T13:10:00"),
            CourierRuntimeState(continuous_riding_min=200.0),
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
                "start_location_zone": 7,
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
