from core.models import Offer, RoadEvent
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
    engine = SimulationEngine(seed=42, shift_duration=120)
    zone_id = 11  # Parque Industrial

    # Inyectar shock de surge en vivo
    event = engine.inject_shock(
        shock_type="surge",
        zone_id=zone_id,
        multiplier=1.8,
        duration_min=25.0,
    )

    assert event.type == "surge"
    assert event.multiplier == 1.8
    assert engine._active_surge_multiplier(zone_id) == 1.8

    # Mockeamos el RNG para que la siguiente oferta se genere en la zona 11
    target_zone = engine.zone_map.by_id(zone_id)
    engine._rng.choice = lambda seq: target_zone
    engine._rng.choices = lambda seq, weights=None, k=1: [target_zone] * k

    offer = engine._generate_offer()
    assert offer.surge_multiplier == 1.8
    # El BRUTO sube con el surge; la tarifa base se queda como estaba, porque
    # el contrato lleva los dos numeros separados (ver TestShockEconomics).
    assert offer.pay >= 40.0
    assert offer.pay * offer.surge_multiplier >= 40.0 * 1.8


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