"""Tests del gate de seguridad (core/agent/safety.py).

Estan organizados por lo que los jueces sondean, no por funcion: cada clase
corresponde a una categoria de `evaluation_protocol.md` seccion 3. Los tests
de frontera (`==` exacto) son la categoria "Threshold consistency" -- el
protocolo prueba explicitamente los bordes, asi que cada limite tiene un test
justo debajo, justo encima y justo en el limite.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.agent import safety
from core.agent.contracts import CourierRuntimeState, InFlightOrder, OrderRequest
from core.agent.reasons import MAX_REASON_WORDS
from core.agent.safety import (
    CompletionEstimate,
    SafetyVerdict,
    combine,
    effective_continuous_riding_min,
    evaluate_safety,
)

NOON = datetime(2026, 3, 21, 12, 0)
EVENING = datetime(2026, 3, 21, 18, 42)
SAFE_ZONE = 11
FLAGGED_ZONE = sorted(safety.FLAGGED_ZONES)[0]


def make_order(**overrides) -> OrderRequest:
    """Oferta neutral: no viola nada. Cada test rompe solo lo que prueba."""
    payload = {
        "order_id": "ORD-TEST",
        "sim_time": EVENING.isoformat(),
        "zone_pickup": 7,
        "zone_dropoff": SAFE_ZONE,
        "distance_pickup_km": 1.0,
        "distance_delivery_km": 2.0,
        "base_pay_mxn": 50.0,
        "surge_multiplier": 1.0,
        "weight_kg": 1.0,
        "volume_liters": 3.0,
        "vehicle": "moto",
    }
    payload.update(overrides)
    return OrderRequest.from_payload(payload)


class FixedEstimator:
    """Estimador de prueba: siempre devuelve los mismos minutos.

    Existe para poder probar las constraints de horario sin depender de la
    aritmetica de distancias -- que es exactamente el valor del Protocol
    `CompletionEstimator`.
    """

    def __init__(self, minutes: float) -> None:
        self.minutes = minutes

    def estimate(self, order, state) -> CompletionEstimate:
        return CompletionEstimate(minutes_to_dropoff=self.minutes)


# ==========================================================================
# Caso base
# ==========================================================================


def test_oferta_limpia_no_dispara_ninguna_constraint():
    verdict = evaluate_safety(make_order(), CourierRuntimeState())
    assert not verdict.blocked
    assert verdict.binding is None
    assert verdict.violations == ()


def test_el_probe_del_validador_oficial_no_dispara_nada():
    """El PROBE de validate_format.py no trae estado del repartidor.

    Si el gate inventara una violacion con datos ausentes, `--endpoint`
    seguiria en verde (el formato es valido) pero el agente rechazaria todo.
    """
    probe = make_order(
        order_id="FORMAT-PROBE-001",
        distance_pickup_km=1.4,
        distance_delivery_km=6.5,
        base_pay_mxn=58.0,
        surge_multiplier=1.3,
        est_tip_mxn=12.0,
        restaurant_prep_min=9,
        weight_kg=2.1,
        volume_liters=6.0,
    )
    assert not evaluate_safety(probe, CourierRuntimeState()).blocked


# ==========================================================================
# Constraint 2 -- pausa obligatoria  ("Continuous-riding safeguards")
# ==========================================================================


class TestPausaObligatoria:
    @pytest.mark.parametrize(
        "riding, esperado",
        [
            (safety.MAX_CONTINUOUS_RIDING_MIN - 0.1, False),
            (safety.MAX_CONTINUOUS_RIDING_MIN, True),  # frontera: >= dispara
            (safety.MAX_CONTINUOUS_RIDING_MIN + 0.1, True),
        ],
    )
    def test_frontera_de_las_cuatro_horas(self, riding, esperado):
        state = CourierRuntimeState(continuous_riding_min=riding)
        verdict = evaluate_safety(make_order(), state)
        assert ("mandatory_break" in verdict.constraints) is esperado

    def test_una_pausa_reciente_desmiente_al_contador(self):
        """250 min de manejo continuo con una pausa hace 10 min es incoherente.

        Gana el minimo: si no, la pausa obligatoria dispararia justo despues de
        haber descansado.
        """
        state = CourierRuntimeState(
            continuous_riding_min=250.0,
            last_break_end_time=EVENING - timedelta(minutes=10),
        )
        assert effective_continuous_riding_min(state, EVENING) == pytest.approx(10.0)
        assert not evaluate_safety(make_order(), state).blocked

    def test_una_pausa_vieja_no_borra_el_contador(self):
        state = CourierRuntimeState(
            continuous_riding_min=250.0,
            last_break_end_time=EVENING - timedelta(minutes=300),
        )
        assert effective_continuous_riding_min(state, EVENING) == pytest.approx(250.0)
        assert "mandatory_break" in evaluate_safety(make_order(), state).constraints


# ==========================================================================
# Constraint 3 -- regla de calor  ("Continuous-riding safeguards")
# ==========================================================================


class TestReglaDeCalor:
    @pytest.mark.parametrize(
        "hora, esperado",
        [
            (11, False),
            (12, True),  # frontera inferior: cerrada
            (15, True),
            (16, False),  # frontera superior: abierta
            (18, False),
        ],
    )
    def test_franja_horaria(self, hora, esperado):
        order = make_order(sim_time=NOON.replace(hour=hora).isoformat())
        state = CourierRuntimeState(continuous_riding_min=95.0)
        assert ("heat_rule" in evaluate_safety(order, state).constraints) is esperado

    @pytest.mark.parametrize(
        "riding, esperado",
        [
            (safety.HEAT_MAX_CONTINUOUS_RIDING_MIN - 0.1, False),
            (safety.HEAT_MAX_CONTINUOUS_RIDING_MIN, True),  # frontera
            (safety.HEAT_MAX_CONTINUOUS_RIDING_MIN + 0.1, True),
        ],
    )
    def test_frontera_de_los_noventa_minutos(self, riding, esperado):
        order = make_order(sim_time=NOON.isoformat())
        state = CourierRuntimeState(continuous_riding_min=riding)
        assert ("heat_rule" in evaluate_safety(order, state).constraints) is esperado

    def test_fuera_de_la_franja_el_tope_vuelve_a_ser_240(self):
        """95 min de manejo continuo es violacion a las 12:00 y no lo es a las 18:00."""
        state = CourierRuntimeState(continuous_riding_min=95.0)
        assert evaluate_safety(make_order(sim_time=NOON.isoformat()), state).blocked
        assert not evaluate_safety(make_order(sim_time=EVENING.isoformat()), state).blocked


# ==========================================================================
# Constraint 1 -- zona marcada de noche
# ==========================================================================


class TestZonaMarcadaDeNoche:
    def test_zona_no_marcada_entrega_de_noche_sin_problema(self):
        order = make_order(sim_time="2026-03-21T23:30:00", zone_dropoff=SAFE_ZONE)
        assert not evaluate_safety(order, CourierRuntimeState()).blocked

    def test_zona_marcada_de_dia_no_dispara(self):
        order = make_order(sim_time="2026-03-21T14:00:00", zone_dropoff=FLAGGED_ZONE)
        state = CourierRuntimeState()
        assert "flagged_zone_night" not in evaluate_safety(order, state).constraints

    def test_se_evalua_la_llegada_no_la_hora_del_ping(self):
        """21:50 + 20 min de viaje = 22:10: cae en toque de queda.

        Es la diferencia entre la regla real y una que se burla sola aceptando
        a las 21:50 algo que entrega pasadas las 22:00.
        """
        order = make_order(sim_time="2026-03-21T21:50:00", zone_dropoff=FLAGGED_ZONE)
        verdict = evaluate_safety(order, CourierRuntimeState(), estimator=FixedEstimator(20.0))
        assert "flagged_zone_night" in verdict.constraints

        verdict_corto = evaluate_safety(order, CourierRuntimeState(), estimator=FixedEstimator(5.0))
        assert "flagged_zone_night" not in verdict_corto.constraints

    def test_el_toque_de_queda_cruza_medianoche(self):
        order = make_order(sim_time="2026-03-22T01:00:00", zone_dropoff=FLAGGED_ZONE)
        assert "flagged_zone_night" in evaluate_safety(order, CourierRuntimeState()).constraints


# ==========================================================================
# Constraint 5 -- capacidad del vehiculo  ("Vehicle capacity compliance")
# ==========================================================================


class TestCapacidadDelVehiculo:
    def test_los_tres_perfiles_tienen_limites_distintos(self):
        """Requisito explicito del protocolo: distinct weight and volume limits."""
        from core.agent.contracts import VEHICLE_PROFILES

        pesos = {p.max_weight_kg for p in VEHICLE_PROFILES.values()}
        volumenes = {p.max_volume_liters for p in VEHICLE_PROFILES.values()}
        velocidades = {p.speed_kmh for p in VEHICLE_PROFILES.values()}
        assert len(pesos) == len(volumenes) == len(velocidades) == 3

    def test_el_mismo_pedido_cabe_en_car_y_no_en_bike(self):
        pesado = {"weight_kg": 10.0, "volume_liters": 10.0}
        assert not evaluate_safety(make_order(vehicle="car", **pesado), CourierRuntimeState()).blocked
        verdict = evaluate_safety(make_order(vehicle="bike", **pesado), CourierRuntimeState())
        assert verdict.binding.constraint == "vehicle_capacity"

    @pytest.mark.parametrize("delta, esperado", [(-0.1, False), (0.0, False), (0.1, True)])
    def test_frontera_de_peso_el_limite_exacto_cabe(self, delta, esperado):
        from core.agent.contracts import VEHICLE_PROFILES

        limite = VEHICLE_PROFILES["moto"].max_weight_kg
        order = make_order(weight_kg=limite + delta, volume_liters=0.0)
        verdict = evaluate_safety(order, CourierRuntimeState())
        assert ("vehicle_capacity" in verdict.constraints) is esperado

    def test_la_capacidad_es_acumulada_sobre_la_mochila(self):
        """Hallazgo 3 de la auditoria: la mochila crecia sin limite.

        2 kg son inofensivos solos y son imposibles si ya lleva 11 en una moto
        de 12.
        """
        order = make_order(weight_kg=2.0, volume_liters=1.0)
        vacia = CourierRuntimeState()
        cargada = CourierRuntimeState(
            in_flight_orders=(InFlightOrder(order_id="A", weight_kg=11.0),)
        )
        assert not evaluate_safety(order, vacia).blocked
        assert evaluate_safety(order, cargada).binding.constraint == "vehicle_capacity"

    def test_el_volumen_tambien_bloquea_no_solo_el_peso(self):
        order = make_order(weight_kg=0.1, volume_liters=999.0)
        verdict = evaluate_safety(order, CourierRuntimeState())
        assert verdict.binding.constraint == "vehicle_capacity"
        assert verdict.binding.detail["dimension"] == "volumen"

    def test_vehiculo_desconocido_cae_al_perfil_mas_restrictivo(self):
        """Un dato raro nunca puede AFLOJAR un limite."""
        order = make_order(vehicle="helicopter", weight_kg=8.0)
        assert evaluate_safety(order, CourierRuntimeState()).binding.constraint == "vehicle_capacity"


# ==========================================================================
# Constraint 4 -- fin de turno  ("End-of-shift feasibility")
# ==========================================================================


class TestFinDeTurno:
    def test_cabe_comodo_antes_del_cierre(self):
        state = CourierRuntimeState(shift_end_time=EVENING + timedelta(hours=2))
        assert not evaluate_safety(make_order(), state).blocked

    def test_no_cierra_antes_del_fin_de_turno(self):
        state = CourierRuntimeState(shift_end_time=EVENING + timedelta(minutes=10))
        verdict = evaluate_safety(make_order(), state, estimator=FixedEstimator(30.0))
        assert verdict.binding.constraint == "shift_end_infeasible"

    def test_el_margen_de_seguridad_se_respeta(self):
        """Terminar exactamente a la hora de cierre no es 'a tiempo'."""
        state = CourierRuntimeState(shift_end_time=EVENING + timedelta(minutes=30))
        justo = evaluate_safety(make_order(), state, estimator=FixedEstimator(30.0))
        con_margen = evaluate_safety(
            make_order(),
            state,
            estimator=FixedEstimator(30.0 - safety.SHIFT_END_SAFETY_MARGIN_MIN),
        )
        assert "shift_end_infeasible" in justo.constraints
        assert "shift_end_infeasible" not in con_margen.constraints

    def test_cuenta_el_trabajo_en_vuelo_no_solo_el_pedido_nuevo(self):
        """Hallazgo 2: seis deltas individuales aceptables pasan del cierre.

        El pedido dura poco; lo que no cabe es el pedido DESPUES de terminar lo
        que ya trae encima. Categoria de sondeo "Stacking and route feasibility".
        """
        order = make_order(distance_pickup_km=0.5, distance_delivery_km=0.5)
        cierre = EVENING + timedelta(minutes=40)

        solo = CourierRuntimeState(shift_end_time=cierre)
        assert not evaluate_safety(order, solo).blocked

        con_cola = CourierRuntimeState(
            shift_end_time=cierre,
            in_flight_orders=(
                InFlightOrder(order_id="A", eta_dropoff=EVENING + timedelta(minutes=38)),
            ),
        )
        assert "shift_end_infeasible" in evaluate_safety(order, con_cola).constraints

    def test_sin_hora_de_cierre_la_constraint_no_inventa_nada(self):
        assert not evaluate_safety(make_order(), CourierRuntimeState()).blocked


# ==========================================================================
# Precedencia
# ==========================================================================


class TestPrecedencia:
    def test_se_reportan_todas_pero_manda_la_de_mayor_precedencia(self):
        order = make_order(
            sim_time=NOON.isoformat(), zone_dropoff=FLAGGED_ZONE, weight_kg=99.0
        )
        state = CourierRuntimeState(
            continuous_riding_min=999.0, shift_end_time=NOON - timedelta(hours=1)
        )
        verdict = evaluate_safety(order, state, estimator=FixedEstimator(700.0))

        assert verdict.binding.constraint == "mandatory_break"
        assert len(verdict.violations) >= 4, "el journal debe ver todas, no solo la que manda"

    def test_el_orden_de_violations_sigue_la_precedencia_documentada(self):
        order = make_order(sim_time=NOON.isoformat(), weight_kg=99.0)
        state = CourierRuntimeState(continuous_riding_min=999.0)
        verdict = evaluate_safety(order, state)
        rangos = [safety.CONSTRAINT_PRECEDENCE.index(c) for c in verdict.constraints]
        assert rangos == sorted(rangos)

    def test_la_precedencia_cubre_exactamente_las_cinco_constraints(self):
        assert len(safety.CONSTRAINT_PRECEDENCE) == 5
        assert len(set(safety.CONSTRAINT_PRECEDENCE)) == 5


# ==========================================================================
# Safety-over-pay: la invariante que los jueces prueban a proposito
# ==========================================================================


class TestSeguridadSobrePago:
    def test_mejorar_la_paga_no_convierte_un_refusal_en_accept(self):
        """'Safety refusals must remain refusals even when pay conditions improve.'"""
        state = CourierRuntimeState(continuous_riding_min=999.0)
        for pago, surge, propina in [(50, 1.0, 0), (500, 3.0, 200), (99999, 9.9, 9999)]:
            order = make_order(base_pay_mxn=pago, surge_multiplier=surge, est_tip_mxn=propina)
            verdict = evaluate_safety(order, state)
            decision, _, binding = combine(
                verdict, economic_accept=True, economic_reason="paga excelente"
            )
            assert decision == "SKIP"
            assert binding == "mandatory_break"

    def test_el_gate_no_recibe_el_pago_en_su_firma(self):
        """La invariante es estructural: esta capa no puede ver el pago.

        Si alguien agrega un parametro de pago a evaluate_safety, este test
        falla y obliga a discutirlo.
        """
        import inspect

        params = set(inspect.signature(evaluate_safety).parameters)
        assert params == {"order", "state", "estimator"}

    def test_combine_deja_pasar_el_veredicto_economico_si_no_hay_bloqueo(self):
        libre = SafetyVerdict()
        assert combine(libre, economic_accept=True, economic_reason="conviene")[0] == "ACCEPT"

        decision, _, binding = combine(
            libre, economic_accept=False, economic_reason="paga poco"
        )
        assert (decision, binding) == ("SKIP", "reservation_wage")

    def test_combine_nunca_devuelve_reason_vacio(self):
        decision, reason, _ = combine(SafetyVerdict(), economic_accept=True, economic_reason="   ")
        assert reason.strip()


# ==========================================================================
# Propiedades del fast path: determinismo, pureza, latencia, formato
# ==========================================================================


class TestFastPath:
    def test_es_determinista_sobre_la_misma_entrada(self):
        order = make_order(sim_time=NOON.isoformat(), weight_kg=99.0)
        state = CourierRuntimeState(continuous_riding_min=999.0)
        primero = evaluate_safety(order, state)
        for _ in range(50):
            otro = evaluate_safety(order, state)
            assert otro.constraints == primero.constraints
            assert [v.reason for v in otro.violations] == [v.reason for v in primero.violations]

    def test_ningun_modulo_del_fast_path_lee_el_reloj_de_pared(self):
        """Un `datetime.now()` aqui rompe el diff de replay (protocolo, seccion 6).

        Se inspecciona el AST y no el texto: buscar la cadena tambien encontraria
        la mencion en un comentario o docstring, y este test tiene que fallar
        solo cuando alguien llame de verdad al reloj.
        """
        import ast

        prohibidos = {"now", "utcnow", "today", "time", "monotonic", "perf_counter"}
        base = Path(__file__).resolve().parents[1] / "core" / "agent"

        for nombre in ("safety.py", "contracts.py", "reasons.py"):
            arbol = ast.parse((base / nombre).read_text())
            for nodo in ast.walk(arbol):
                if not isinstance(nodo, ast.Call):
                    continue
                objetivo = nodo.func
                llamada = (
                    objetivo.attr
                    if isinstance(objetivo, ast.Attribute)
                    else getattr(objetivo, "id", None)
                )
                assert llamada not in prohibidos, f"{nombre} llama a {llamada}() en el fast path"

    def test_entra_holgadamente_en_el_presupuesto_de_50ms(self):
        from time import perf_counter

        order = make_order(sim_time=NOON.isoformat())
        state = CourierRuntimeState(
            continuous_riding_min=100.0,
            shift_end_time=NOON + timedelta(hours=3),
            in_flight_orders=tuple(
                InFlightOrder(order_id=f"A{i}", weight_kg=0.01, eta_dropoff=NOON)
                for i in range(50)
            ),
        )
        evaluate_safety(order, state)  # calentar

        t0 = perf_counter()
        for _ in range(1000):
            evaluate_safety(order, state)
        promedio_ms = (perf_counter() - t0) * 1000.0 / 1000
        assert promedio_ms < 1.0, f"{promedio_ms:.4f} ms por decision"

    def test_todo_reason_emitido_cabe_en_cuarenta_palabras(self):
        """validate_format.py rechaza el response entero si se pasa."""
        casos = [
            (make_order(weight_kg=999.0), CourierRuntimeState()),
            (make_order(), CourierRuntimeState(continuous_riding_min=999.0)),
            (make_order(sim_time=NOON.isoformat()), CourierRuntimeState(continuous_riding_min=95.0)),
            (
                make_order(sim_time="2026-03-21T23:00:00", zone_dropoff=FLAGGED_ZONE),
                CourierRuntimeState(),
            ),
            (make_order(), CourierRuntimeState(shift_end_time=EVENING - timedelta(hours=1))),
        ]
        vistos = set()
        for order, state in casos:
            verdict = evaluate_safety(order, state)
            assert verdict.blocked
            for violation in verdict.violations:
                assert violation.reason.strip()
                assert len(violation.reason.split()) <= MAX_REASON_WORDS
                vistos.add(violation.constraint)
        assert vistos == set(safety.CONSTRAINT_PRECEDENCE), "faltan constraints por ejercitar"

    def test_todo_binding_constraint_es_un_id_del_enum_oficial(self):
        """Los mismos valores que valida validate_format.py."""
        oficiales = {
            "flagged_zone_night", "mandatory_break", "heat_rule",
            "shift_end_infeasible", "vehicle_capacity", "reservation_wage",
        }
        assert set(safety.CONSTRAINT_PRECEDENCE) <= oficiales

    def test_cada_violacion_trae_sus_numeros_para_explain_decision(self):
        verdict = evaluate_safety(make_order(weight_kg=999.0), CourierRuntimeState())
        detalle = verdict.binding.detail
        assert detalle["required"] > detalle["limit"]
        assert detalle["vehicle"] == "moto"
