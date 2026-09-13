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
from core.agent.reasons import MAX_REASON_WORDS
from core.agent.safety import (
    SafetyVerdict,
    SafetyViolation,
    combine,
    effective_continuous_riding_min,
    evaluate_safety,
    evaluate_safety_full,
)
from core.models import VEHICLE_PROFILES, VehicleType

NOON = datetime(2026, 3, 21, 12, 0)
EVENING = datetime(2026, 3, 21, 18, 42)
SAFE_ZONE = 999  # fuera del ZoneMap: nunca marcada
FLAGGED_ZONE = sorted(safety.FLAGGED_ZONES)[0]

MOTO = VEHICLE_PROFILES[VehicleType.MOTO]


def inputs(**overrides) -> dict:
    """Entrada neutral: no viola nada. Cada test rompe solo lo que prueba."""
    base = dict(
        vehicle="moto",
        weight_kg=1.0,
        volume_liters=3.0,
        sim_time=EVENING,
        zone_dropoff=SAFE_ZONE,
        continuous_riding_min=0.0,
        order_total_time_min=20.0,
        shift_end_time=None,
    )
    base.update(overrides)
    return base


def constraints(**overrides) -> tuple[str, ...]:
    return evaluate_safety_full(**inputs(**overrides)).constraints


# ==========================================================================
# Caso base
# ==========================================================================


def test_oferta_limpia_no_dispara_ninguna_constraint():
    verdict = evaluate_safety_full(**inputs())
    assert not verdict.blocked
    assert verdict.binding is None
    assert evaluate_safety(**inputs()) is None


def test_el_probe_del_validador_oficial_no_dispara_nada():
    """El PROBE de validate_format.py no trae estado del repartidor.

    Si el gate inventara una violacion con datos ausentes, `--endpoint`
    seguiria en verde (el formato es valido) pero el agente rechazaria todo.
    """
    assert not evaluate_safety_full(
        **inputs(weight_kg=2.1, volume_liters=6.0, zone_dropoff=11, order_total_time_min=23.0)
    ).blocked


def test_las_constantes_derivan_las_zonas_marcadas_del_zonemap():
    """Escribir ids sueltos permitiria marcar una zona que no existe."""
    from core.models import DEFAULT_ZONE_MAP

    validos = {z.zone_id for z in DEFAULT_ZONE_MAP.zones}
    assert safety.FLAGGED_ZONES
    assert safety.FLAGGED_ZONES <= validos


# ==========================================================================
# Constraint 2 -- pausa obligatoria  ("Continuous-riding safeguards")
# ==========================================================================


class TestPausaObligatoria:
    @pytest.mark.parametrize(
        "riding, esperado",
        [
            (safety.MANDATORY_BREAK_AFTER_MIN - 0.1, False),
            (safety.MANDATORY_BREAK_AFTER_MIN, True),  # frontera: >= dispara
            (safety.MANDATORY_BREAK_AFTER_MIN + 0.1, True),
        ],
    )
    def test_frontera_de_las_cuatro_horas(self, riding, esperado):
        assert ("mandatory_break" in constraints(continuous_riding_min=riding)) is esperado

    def test_es_retrospectiva_no_proyectada(self):
        """La pausa es un DISPARADOR ("after 4 continuous hours"), no un tope:
        un pedido largo que cruce la marca no se rechaza por esta regla."""
        assert "mandatory_break" not in constraints(
            continuous_riding_min=230.0, order_total_time_min=120.0
        )

    def test_una_pausa_reciente_desmiente_al_contador(self):
        """250 min continuos con una pausa hace 10 min es incoherente.

        Gana el minimo: si no, la pausa obligatoria dispararia justo despues de
        haber descansado.
        """
        assert effective_continuous_riding_min(250.0, EVENING - timedelta(minutes=10), EVENING) == (
            pytest.approx(10.0)
        )
        assert not evaluate_safety_full(
            **inputs(
                continuous_riding_min=250.0,
                last_break_end_time=EVENING - timedelta(minutes=10),
            )
        ).blocked

    def test_una_pausa_vieja_no_borra_el_contador(self):
        assert effective_continuous_riding_min(
            250.0, EVENING - timedelta(minutes=300), EVENING
        ) == pytest.approx(250.0)
        assert "mandatory_break" in constraints(
            continuous_riding_min=250.0, last_break_end_time=EVENING - timedelta(minutes=300)
        )

    def test_una_pausa_en_el_futuro_es_dato_incoherente_y_se_ignora(self):
        assert effective_continuous_riding_min(
            250.0, EVENING + timedelta(minutes=10), EVENING
        ) == pytest.approx(250.0)


# ==========================================================================
# Constraint 3 -- regla de calor  ("Continuous-riding safeguards")
# ==========================================================================


class TestReglaDeCalor:
    @pytest.mark.parametrize(
        "hora, esperado",
        [(11, False), (12, True), (15, True), (16, False), (18, False)],
    )
    def test_franja_horaria(self, hora, esperado):
        """[12:00, 16:00): cerrada abajo, abierta arriba."""
        assert (
            "heat_rule"
            in constraints(sim_time=NOON.replace(hour=hora), continuous_riding_min=95.0)
        ) is esperado

    @pytest.mark.parametrize(
        "proyectado, esperado",
        [
            (safety.HEAT_RULE_MAX_CONTINUOUS_MIN - 0.1, False),
            (safety.HEAT_RULE_MAX_CONTINUOUS_MIN, False),  # frontera: == cabe
            (safety.HEAT_RULE_MAX_CONTINUOUS_MIN + 0.1, True),
        ],
    )
    def test_frontera_de_los_noventa_minutos(self, proyectado, esperado):
        assert (
            "heat_rule"
            in constraints(
                sim_time=NOON, continuous_riding_min=proyectado, order_total_time_min=0.0
            )
        ) is esperado

    def test_es_un_tope_prospectivo_no_un_disparador(self):
        """Un pedido de 95 min arrancando desde cero es exactamente lo que el
        tope existe para impedir. Evaluarlo solo sobre el acumulado lo dejaria
        pasar."""
        assert "heat_rule" in constraints(
            sim_time=NOON, continuous_riding_min=0.0, order_total_time_min=95.0
        )

    def test_fuera_de_la_franja_el_tope_vuelve_a_ser_240(self):
        comun = dict(continuous_riding_min=95.0, order_total_time_min=10.0)
        assert evaluate_safety_full(**inputs(sim_time=NOON, **comun)).blocked
        assert not evaluate_safety_full(**inputs(sim_time=EVENING, **comun)).blocked


# ==========================================================================
# Constraint 1 -- zona marcada de noche
# ==========================================================================


class TestZonaMarcadaDeNoche:
    def test_zona_no_marcada_entrega_de_noche_sin_problema(self):
        assert not evaluate_safety_full(
            **inputs(sim_time=datetime(2026, 3, 21, 23, 30), zone_dropoff=SAFE_ZONE)
        ).blocked

    def test_zona_marcada_de_dia_no_dispara(self):
        assert "flagged_zone_night" not in constraints(
            sim_time=datetime(2026, 3, 21, 14, 0), zone_dropoff=FLAGGED_ZONE
        )

    def test_se_evalua_la_llegada_no_la_hora_del_ping(self):
        """21:50 + 20 min de viaje = 22:10: cae en toque de queda.

        Es la diferencia entre la regla real y una que se burla sola aceptando
        a las 21:50 algo que entrega pasadas las 22:00.
        """
        comun = dict(sim_time=datetime(2026, 3, 21, 21, 50), zone_dropoff=FLAGGED_ZONE)
        assert "flagged_zone_night" in constraints(order_total_time_min=20.0, **comun)
        assert "flagged_zone_night" not in constraints(order_total_time_min=5.0, **comun)

    def test_la_cola_del_trabajo_en_vuelo_tambien_empuja_la_llegada(self):
        comun = dict(
            sim_time=datetime(2026, 3, 21, 21, 40),
            zone_dropoff=FLAGGED_ZONE,
            order_total_time_min=10.0,
        )
        assert "flagged_zone_night" not in constraints(**comun)
        assert "flagged_zone_night" in constraints(queue_offset_min=15.0, **comun)

    def test_el_toque_de_queda_cruza_medianoche(self):
        assert "flagged_zone_night" in constraints(
            sim_time=datetime(2026, 3, 22, 1, 0), zone_dropoff=FLAGGED_ZONE
        )


# ==========================================================================
# Constraint 5 -- capacidad del vehiculo  ("Vehicle capacity compliance")
# ==========================================================================


class TestCapacidadDelVehiculo:
    def test_los_tres_perfiles_tienen_limites_distintos(self):
        """Requisito explicito: distinct speed profiles and distinct weight
        and volume limits."""
        assert len({p.weight_limit_kg for p in VEHICLE_PROFILES.values()}) == 3
        assert len({p.volume_limit_liters for p in VEHICLE_PROFILES.values()}) == 3
        assert len({p.avg_speed_kmh for p in VEHICLE_PROFILES.values()}) == 3

    def test_el_mismo_pedido_cabe_en_car_y_no_en_bike(self):
        pesado = dict(weight_kg=10.0, volume_liters=10.0)
        assert not evaluate_safety_full(**inputs(vehicle="car", **pesado)).blocked
        assert (
            evaluate_safety_full(**inputs(vehicle="bike", **pesado)).binding.constraint
            == "vehicle_capacity"
        )

    @pytest.mark.parametrize("delta, esperado", [(-0.1, False), (0.0, False), (0.1, True)])
    def test_frontera_de_peso_el_limite_exacto_cabe(self, delta, esperado):
        assert (
            "vehicle_capacity"
            in constraints(weight_kg=MOTO.weight_limit_kg + delta, volume_liters=0.0)
        ) is esperado

    def test_la_capacidad_es_acumulada_sobre_la_mochila(self):
        """Hallazgo 3 de la auditoria: la mochila crecia sin limite.

        2 kg son inofensivos solos y son imposibles si ya lleva casi el limite.
        """
        casi_lleno = MOTO.weight_limit_kg - 1.0
        assert not evaluate_safety_full(**inputs(weight_kg=2.0, volume_liters=1.0)).blocked
        assert (
            evaluate_safety_full(
                **inputs(weight_kg=2.0, volume_liters=1.0, in_flight_weight_kg=casi_lleno)
            ).binding.constraint
            == "vehicle_capacity"
        )

    def test_el_volumen_tambien_bloquea_no_solo_el_peso(self):
        binding = evaluate_safety_full(**inputs(weight_kg=0.1, volume_liters=9999.0)).binding
        assert binding.constraint == "vehicle_capacity"
        assert binding.detail["dimension"] == "volumen"

    def test_un_peso_ausente_no_inventa_una_violacion(self):
        """weight_kg/volume_liters son opcionales en el schema."""
        assert not evaluate_safety_full(**inputs(weight_kg=None, volume_liters=None)).blocked

    def test_un_peso_ausente_tampoco_borra_la_mochila_que_si_conocemos(self):
        assert "vehicle_capacity" in constraints(
            weight_kg=None, volume_liters=None, in_flight_weight_kg=MOTO.weight_limit_kg + 1
        )

    def test_vehiculo_desconocido_cae_al_perfil_mas_restrictivo(self):
        """Un dato raro nunca puede AFLOJAR un limite."""
        peso = VEHICLE_PROFILES[VehicleType.BIKE].weight_limit_kg + 0.5
        assert "vehicle_capacity" in constraints(vehicle="helicopter", weight_kg=peso)

    def test_un_vehiculo_desconocido_no_tumba_el_endpoint(self):
        assert safety.profile_for("helicopter").type is VehicleType.BIKE
        assert safety.profile_for("").type is VehicleType.BIKE


# ==========================================================================
# Constraint 4 -- fin de turno  ("End-of-shift feasibility")
# ==========================================================================


class TestFinDeTurno:
    def test_cabe_comodo_antes_del_cierre(self):
        assert not evaluate_safety_full(
            **inputs(shift_end_time=EVENING + timedelta(hours=2))
        ).blocked

    def test_no_cierra_antes_del_fin_de_turno(self):
        assert (
            evaluate_safety_full(
                **inputs(shift_end_time=EVENING + timedelta(minutes=10), order_total_time_min=30.0)
            ).binding.constraint
            == "shift_end_infeasible"
        )

    def test_el_margen_de_seguridad_se_respeta(self):
        """Terminar exactamente a la hora de cierre no es 'a tiempo'."""
        cierre = EVENING + timedelta(minutes=30)
        assert "shift_end_infeasible" in constraints(
            shift_end_time=cierre, order_total_time_min=30.0
        )
        assert "shift_end_infeasible" not in constraints(
            shift_end_time=cierre,
            order_total_time_min=30.0 - safety.SHIFT_END_SAFETY_MARGIN_MIN,
        )

    def test_cuenta_el_trabajo_en_vuelo_no_solo_el_pedido_nuevo(self):
        """Hallazgo 2: seis deltas individuales aceptables pasan del cierre.

        El pedido dura poco; lo que no cabe es el pedido DESPUES de terminar lo
        que ya trae encima. Categoria de sondeo "Stacking and route feasibility".
        """
        comun = dict(shift_end_time=EVENING + timedelta(minutes=40), order_total_time_min=10.0)
        assert "shift_end_infeasible" not in constraints(**comun)
        assert "shift_end_infeasible" in constraints(queue_offset_min=38.0, **comun)

    def test_sin_hora_de_cierre_la_constraint_no_inventa_nada(self):
        assert not evaluate_safety_full(**inputs(shift_end_time=None)).blocked


# ==========================================================================
# Precedencia
# ==========================================================================


class TestPrecedencia:
    def test_se_reportan_todas_pero_manda_la_de_mayor_precedencia(self):
        verdict = evaluate_safety_full(
            **inputs(
                sim_time=NOON,
                zone_dropoff=FLAGGED_ZONE,
                weight_kg=9999.0,
                continuous_riding_min=999.0,
                order_total_time_min=700.0,
                shift_end_time=NOON - timedelta(hours=1),
            )
        )
        assert verdict.binding.constraint == "mandatory_break"
        assert len(verdict.violations) >= 4, "el journal debe ver todas, no solo la que manda"

    def test_el_orden_de_violations_sigue_la_precedencia_documentada(self):
        verdict = evaluate_safety_full(
            **inputs(sim_time=NOON, weight_kg=9999.0, continuous_riding_min=999.0)
        )
        rangos = [safety.CONSTRAINT_PRECEDENCE.index(c) for c in verdict.constraints]
        assert rangos == sorted(rangos)

    def test_la_precedencia_cubre_exactamente_las_cinco_constraints(self):
        assert len(set(safety.CONSTRAINT_PRECEDENCE)) == 5


# ==========================================================================
# Safety-over-pay: la invariante que los jueces prueban a proposito
# ==========================================================================


class TestSeguridadSobrePago:
    def test_el_gate_no_recibe_el_pago_en_su_firma(self):
        """La invariante es estructural: esta capa no puede ver el pago.

        Si alguien agrega un parametro de pago a evaluate_safety_full, este
        test falla y obliga a discutirlo.
        """
        import inspect

        params = set(inspect.signature(evaluate_safety_full).parameters)
        prohibidos = {"base_pay_mxn", "surge_multiplier", "est_tip_mxn", "pay", "economics"}
        assert not (params & prohibidos)

    def test_combine_ignora_lo_economico_cuando_la_seguridad_bloquea(self):
        """"Safety refusals must remain refusals even when pay conditions improve"."""
        verdict = evaluate_safety_full(**inputs(continuous_riding_min=999.0))
        decision, _, binding = combine(
            verdict, economic_accept=True, economic_reason="paga excelente"
        )
        assert (decision, binding) == ("SKIP", "mandatory_break")

    def test_combine_acepta_verdict_violacion_suelta_o_none(self):
        """Tolerancia deliberada: sirve con `evaluate_safety` y con `_full`."""
        violacion = SafetyViolation("mandatory_break", "toca parar")
        for entrada in (SafetyVerdict((violacion,)), violacion):
            assert combine(entrada, economic_accept=True, economic_reason="x")[0] == "SKIP"
        assert combine(None, economic_accept=True, economic_reason="x")[0] == "ACCEPT"

    def test_combine_deja_pasar_el_veredicto_economico_si_no_hay_bloqueo(self):
        libre = SafetyVerdict()
        assert combine(libre, economic_accept=True, economic_reason="conviene")[0] == "ACCEPT"
        decision, _, binding = combine(libre, economic_accept=False, economic_reason="paga poco")
        assert (decision, binding) == ("SKIP", "reservation_wage")

    def test_combine_nunca_devuelve_reason_vacio(self):
        _, reason, _ = combine(SafetyVerdict(), economic_accept=True, economic_reason="   ")
        assert reason.strip()


# ==========================================================================
# Propiedades del fast path: determinismo, pureza, latencia, formato
# ==========================================================================


class TestFastPath:
    def test_es_determinista_sobre_la_misma_entrada(self):
        args = inputs(sim_time=NOON, weight_kg=9999.0, continuous_riding_min=999.0)
        primero = evaluate_safety_full(**args)
        for _ in range(50):
            otro = evaluate_safety_full(**args)
            assert otro.constraints == primero.constraints
            assert [v.reason for v in otro.violations] == [v.reason for v in primero.violations]

    def test_ningun_modulo_del_fast_path_lee_el_reloj_de_pared(self):
        """Un `datetime.now()` aqui rompe el diff de replay (protocolo, seccion 6).

        Se inspecciona el AST y no el texto: buscar la cadena tambien encontraria
        la mencion en un comentario o docstring, y este test tiene que fallar
        solo cuando alguien llame de verdad al reloj.
        """
        import ast

        prohibidos = {"now", "utcnow", "today", "monotonic"}
        base = Path(__file__).resolve().parents[1] / "core" / "agent"

        for nombre in ("safety.py", "reasons.py", "economics.py", "journal.py"):
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

        args = inputs(
            sim_time=NOON,
            continuous_riding_min=100.0,
            shift_end_time=NOON + timedelta(hours=3),
            in_flight_weight_kg=5.0,
            queue_offset_min=12.0,
        )
        evaluate_safety_full(**args)  # calentar

        t0 = perf_counter()
        for _ in range(1000):
            evaluate_safety_full(**args)
        promedio_ms = (perf_counter() - t0) * 1000.0 / 1000
        assert promedio_ms < 1.0, f"{promedio_ms:.4f} ms por decision"

    def test_todo_reason_emitido_cabe_en_cuarenta_palabras(self):
        """validate_format.py rechaza el response entero si se pasa."""
        casos = [
            inputs(weight_kg=9999.0),
            inputs(continuous_riding_min=999.0),
            inputs(sim_time=NOON, continuous_riding_min=95.0),
            inputs(sim_time=datetime(2026, 3, 21, 23, 0), zone_dropoff=FLAGGED_ZONE),
            inputs(shift_end_time=EVENING - timedelta(hours=1)),
        ]
        vistos = set()
        for args in casos:
            verdict = evaluate_safety_full(**args)
            assert verdict.blocked
            for violation in verdict.violations:
                assert violation.reason.strip()
                assert len(violation.reason.split()) <= MAX_REASON_WORDS
                vistos.add(violation.constraint)
        assert vistos == set(safety.CONSTRAINT_PRECEDENCE), "faltan constraints por ejercitar"

    def test_todo_binding_constraint_es_un_id_del_enum_oficial(self):
        oficiales = {
            "flagged_zone_night", "mandatory_break", "heat_rule",
            "shift_end_infeasible", "vehicle_capacity", "reservation_wage",
        }
        assert set(safety.CONSTRAINT_PRECEDENCE) <= oficiales

    def test_cada_violacion_trae_sus_numeros_para_explain_decision(self):
        detalle = evaluate_safety_full(**inputs(weight_kg=9999.0)).binding.detail
        assert detalle["required"] > detalle["limit"]
        assert detalle["vehicle"] == "moto"
