import pytest

from core.models import Offer, RouteStop
from core.routing.euclidean import EuclideanDistanceProvider
from core.routing.greedy import cheapest_insertion

PROVIDER = EuclideanDistanceProvider()

TEC = (25.651, -100.289)
CENTRO = (25.680, -100.310)
APODACA = (25.780, -100.180)


def make_offer(offer_id, pickup, dropoff, received_at=0.0, window=(0.0, 10_000.0)):
    return Offer(
        id=offer_id,
        pickup=pickup,
        dropoff=dropoff,
        pay=50.0,
        time_window=window,
        received_at=received_at,
    )


def test_empty_route_inserts_pickup_and_dropoff_at_the_end():
    offer = make_offer("o1", TEC, CENTRO)

    result = cheapest_insertion([], offer, frozen_index=0, accepted_offers={}, distance_provider=PROVIDER)

    assert result.feasible
    assert [s.kind for s in result.new_route] == ["pickup", "dropoff"]
    assert result.new_route[0].eta == offer.received_at
    assert result.extra_time > 0


def test_chooses_cheapest_position_among_several():
    existing = make_offer("A", TEC, CENTRO, received_at=0.0)
    dropoff_eta = PROVIDER.travel_time(TEC, CENTRO)
    route = [
        RouteStop(offer_id="A", kind="pickup", eta=0.0),
        RouteStop(offer_id="A", kind="dropoff", eta=dropoff_eta),
    ]
    accepted = {"A": existing}

    # B recoge justo donde A entrega (Centro): insertarla al final es mucho
    # mas barato que meterla entre el pickup y el dropoff de A.
    new_offer = make_offer("B", CENTRO, APODACA, received_at=dropoff_eta)

    result = cheapest_insertion(
        route, new_offer, frozen_index=0, accepted_offers=accepted, distance_provider=PROVIDER
    )

    assert result.feasible
    stops = [(s.offer_id, s.kind) for s in result.new_route]
    assert stops == [("A", "pickup"), ("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]

    b_pickup, b_dropoff = result.new_route[2], result.new_route[3]
    assert b_pickup.eta == pytest.approx(dropoff_eta)
    assert b_dropoff.eta == pytest.approx(dropoff_eta + PROVIDER.travel_time(CENTRO, APODACA))


def test_respects_frozen_index_never_inserts_before_it():
    existing = make_offer("A", APODACA, CENTRO, received_at=0.0)
    dropoff_eta = PROVIDER.travel_time(APODACA, CENTRO)
    route = [
        RouteStop(offer_id="A", kind="pickup", eta=0.0),
        RouteStop(offer_id="A", kind="dropoff", eta=dropoff_eta),
    ]
    accepted = {"A": existing}

    # C recoge y entrega justo donde arranca la ruta (Apodaca): sin freeze
    # seria mas barato meterla antes del pickup de A (indice 0).
    new_offer = make_offer("C", APODACA, APODACA, received_at=0.0)

    result = cheapest_insertion(
        route, new_offer, frozen_index=1, accepted_offers=accepted, distance_provider=PROVIDER
    )

    assert result.feasible
    first_stop = result.new_route[0]
    assert (first_stop.offer_id, first_stop.kind, first_stop.eta) == ("A", "pickup", 0.0)


def test_infeasible_when_no_position_fits_time_window():
    # Ventana de 0.001 min: ninguna insercion (ni siquiera al vuelo) cabe.
    offer = make_offer("o1", TEC, APODACA, received_at=0.0, window=(0.0, 0.001))

    result = cheapest_insertion([], offer, frozen_index=0, accepted_offers={}, distance_provider=PROVIDER)

    assert result.feasible is False
    assert result.new_route is None


def test_rejects_cheapest_position_that_breaks_a_downstream_window():
    """Regresion del Hallazgo 1 (auditoria del 12 sep): insertar B en la
    posicion mas barata (en medio de la ruta de A) atrasa el dropoff de A
    mas alla de SU ventana. `cheapest_insertion` debia validar solo la
    ventana de la oferta nueva y aceptaba esta posicion con `feasible=True`
    de todos modos -- ahora debe descartarla y usar la siguiente mas barata
    que si respeta a A.

    Geometria elegida a proposito: insertar B entre el pickup y el dropoff
    de A cuesta ~0.008 min de desvio (va casi en linea recta); insertarla
    despues del dropoff de A cuesta ~4.45 min. Sin el fix, la posicion barata
    gana aunque atrase a A por encima de su ventana; con el fix, se descarta
    y gana la posicion cara pero factible.
    """
    b_detour_point = (25.6685, -100.3025)  # casi sobre la linea TEC->CENTRO

    dropoff_eta_a = PROVIDER.travel_time(TEC, CENTRO)
    # Ventana de A mas ajustada que el desvio que provocaria B en medio
    # (~0.008 min) pero suficiente para no tocarla si B se inserta al final.
    a = make_offer("A", TEC, CENTRO, received_at=0.0, window=(0.0, dropoff_eta_a + 0.005))
    route = [
        RouteStop(offer_id="A", kind="pickup", eta=0.0),
        RouteStop(offer_id="A", kind="dropoff", eta=dropoff_eta_a),
    ]
    accepted = {"A": a}

    b = make_offer("B", b_detour_point, b_detour_point, received_at=0.0, window=(0.0, 1000.0))

    result = cheapest_insertion(
        route, b, frozen_index=1, accepted_offers=accepted, distance_provider=PROVIDER
    )

    assert result.feasible
    stops = [(s.offer_id, s.kind) for s in result.new_route]
    # B debe quedar DESPUES del dropoff de A, no en medio (la posicion barata
    # que rompia la ventana de A).
    assert stops == [("A", "pickup"), ("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]

    a_dropoff_stop = result.new_route[1]
    assert a_dropoff_stop.eta <= a.time_window[1]
    # Verifica que efectivamente no se movio del ETA original -- B no la toco.
    assert a_dropoff_stop.eta == pytest.approx(dropoff_eta_a)
