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