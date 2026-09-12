"""Smoke test de integracion real: Bloque 1 -> Bloque 3 -> Bloque 2, sin mocks.

Habilitado por el estado actual del repo (ver docs/Bloque 3/01_Plan.md
seccion 0): SimulationEngine y CourierStateManager ya estan implementados de
verdad, asi que Bloque 3 se puede validar contra el resto del sistema real
que ya existe, no solo contra dobles de prueba.
"""

from core.agent.decision import DecisionEngine
from core.agent.demand import StaticDemandSignal
from core.models import Offer
from core.routing.euclidean import EuclideanDistanceProvider
from core.simulation.engine import SimulationEngine
from core.simulation.state import CourierStateManager


def test_full_shift_runs_without_exceptions_and_keeps_state_consistent():
    engine = SimulationEngine(seed=7, shift_duration=120.0)
    state_manager = CourierStateManager(shift_duration=120.0)
    decision_engine = DecisionEngine(
        state_manager=state_manager,
        distance_provider=EuclideanDistanceProvider(),
        demand_signal=StaticDemandSignal(),
    )

    last_version = 0
    last_earnings = 0.0
    offers_evaluated = 0

    for event in engine.event_stream():
        if not isinstance(event, Offer):
            continue  # RoadEvent (cierre/trafico/surge) no es responsabilidad de Bloque 3

        offers_evaluated += 1
        decision = decision_engine.evaluate(event)
        snapshot = state_manager.snapshot()

        # El version nunca retrocede -- invariante de concurrencia optimista
        # de CourierStateManager (Bloque 2), sin importar lo que decida Bloque 3.
        assert snapshot.version >= last_version

        if decision.accepted:
            assert "Aceptado" in decision.log
            assert snapshot.earnings >= last_earnings
        else:
            assert "Rechazado" in decision.log
            assert snapshot.earnings == last_earnings

        last_version = snapshot.version
        last_earnings = snapshot.earnings

    assert offers_evaluated > 0
