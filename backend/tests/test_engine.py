from core.models import Offer, RoadEvent, Zone, ZoneMap
from core.simulation.engine import SimulationEngine


def test_same_seed_generates_same_events():

    engine1 = SimulationEngine(
        seed=42,
        shift_duration=60
    )

    engine2 = SimulationEngine(
        seed=42,
        shift_duration=60
    )


    events1 = list(engine1.event_stream())
    events2 = list(engine2.event_stream())


    assert events1 == events2



def test_event_stream_generates_events():

    engine = SimulationEngine(
        seed=42,
        shift_duration=60
    )

    events = list(engine.event_stream())

    assert len(events) > 0



def test_events_are_valid_models():

    engine = SimulationEngine(
        seed=42,
        shift_duration=60
    )

    events = list(engine.event_stream())


    for event in events:

        assert isinstance(
            event,
            (Offer, RoadEvent)
        )


def test_inject_shock_affects_economy_and_subsequent_offers():
    """Un shock inyectado en vivo tiene que mover las ofertas siguientes.

    La zona se fuerza con un ZoneMap de una sola zona en vez de parchear el
    RNG: el generador elige el pickup con `choices(...)` ponderado por
    demanda, asi que cualquier mock tiene que replicar esa firma y se rompe en
    cuanto cambie. Con una sola zona no hay nada que elegir.
    """
    solo_una = ZoneMap([Zone(11, "Parque Industrial", (25.740, -100.220), demand_score=0.25)])
    engine = SimulationEngine(seed=42, shift_duration=120, zone_map=solo_una)

    control = SimulationEngine(seed=42, shift_duration=120, zone_map=solo_una)._generate_offer()

    event = engine.inject_shock(
        shock_type="surge",
        zone_id=11,
        multiplier=1.8,
        duration_min=25.0,
    )

    assert event.type == "surge"
    assert event.multiplier == 1.8
    assert event.duration_min == 25.0
    assert engine._active_surge_multiplier(11) == 1.8

    offer = engine._generate_offer()
    assert offer.zone_pickup == 11
    assert offer.surge_multiplier == 1.8
    # La tarifa base NO se toca: el surge viaja en su propio campo, porque la
    # economia aguas abajo ya calcula `base_pay * surge`. Ver
    # test_abraham.py::TestShockEconomics::test_el_surge_no_se_cuenta_dos_veces.
    assert offer.pay == control.pay


def test_inject_shock_surge_expira_y_deja_de_afectar():
    solo_una = ZoneMap([Zone(11, "Parque Industrial", (25.740, -100.220), demand_score=0.25)])
    engine = SimulationEngine(seed=42, shift_duration=120, zone_map=solo_una)

    engine.inject_shock(shock_type="surge", zone_id=11, multiplier=1.8, duration_min=25.0)
    assert engine._active_surge_multiplier(11) == 1.8

    engine.current_time = 25.0
    assert engine._active_surge_multiplier(11) == 1.0


def test_inject_shock_emits_valid_jsonl_log():
    import io
    import json

    log_buf = io.StringIO()
    engine = SimulationEngine(seed=42, shift_duration=60, log_file=log_buf)

    engine.inject_shock(
        shock_type="surge",
        zone_id=7,
        multiplier=1.6,
        duration_min=30.0,
    )

    lines = [json.loads(l) for l in log_buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    shock_record = lines[0]
    assert shock_record["event"] == "shock"
    assert shock_record["shock_type"] == "surge"
    assert shock_record["zone"] == 7
    assert shock_record["multiplier"] == 1.6
    assert shock_record["duration_min"] == 30.0