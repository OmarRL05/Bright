"""Heuristica de insercion mas barata (cheapest insertion), O(n) por oferta.

Usada por Bloque 3 para decidir "en segundos" sin resolver el VRP completo.
Ver docs/02_Documentacion_Tecnica.md seccion 1 y docs/Bloque 3/01_Plan.md
seccion 4.

Nota de diseno: el stub original solo recibia (current_route, offer,
frozen_index) -- sin forma de resolver coordenadas de las paradas ya en
route (RouteStop solo guarda offer_id/kind, no lat/lon) ni de calcular
tiempos/distancias. Se extendio la firma con `accepted_offers` (mochila
actual, para resolver esas coordenadas) y `distance_provider` (Protocol de
core.routing.distance_provider, hoy satisfecho por EuclideanDistanceProvider).
InsertionResult gano un campo `feasible` para poder reportar "no insertable
en ninguna posicion" sin lanzar una excepcion. Ver docs/Bloque 3/doc-scripts/
para el desglose completo.

La excepcion de "costo marginal ~0" del frozen horizon (docs/01_Arquitectura.md
seccion 6) es responsabilidad de decision.py, no de esta funcion: aqui
`frozen_index` simplemente acota el rango de busqueda.
"""

from dataclasses import dataclass

from core.models import Offer, RouteStop
from core.routing.distance_provider import DistanceProvider


@dataclass
class InsertionResult:
    extra_distance: float
    extra_time: float
    new_route: list[RouteStop] | None
    feasible: bool = True


def _location_of(stop: RouteStop, accepted_offers: dict[str, Offer]) -> tuple[float, float]:
    offer = accepted_offers[stop.offer_id]
    return offer.pickup if stop.kind == "pickup" else offer.dropoff


def _pickup_dropoff_eta(
    offer: Offer,
    prev_stop: RouteStop | None,
    prev_location: tuple[float, float] | None,
    distance_provider: DistanceProvider,
) -> tuple[float, float]:
    if prev_stop is None:
        pickup_eta = offer.received_at
    else:
        # max(...) evita un pickup_eta anterior a offer.received_at: la
        # oferta no puede recogerse antes de haber llegado.
        pickup_eta = max(
            offer.received_at,
            prev_stop.eta + distance_provider.travel_time(prev_location, offer.pickup),
        )

    dropoff_eta = pickup_eta + distance_provider.travel_time(offer.pickup, offer.dropoff)
    return pickup_eta, dropoff_eta


def _insertion_cost(
    offer: Offer,
    prev_location: tuple[float, float] | None,
    next_location: tuple[float, float] | None,
    distance_provider: DistanceProvider,
) -> tuple[float, float]:
    """Costo marginal (extra_time, extra_distance) de insertar `offer` entre
    `prev_location` y `next_location` (cualquiera de los dos puede ser None
    si se inserta al inicio o al final de la ruta)."""

    extra_time = distance_provider.travel_time(offer.pickup, offer.dropoff)
    extra_distance = distance_provider.travel_distance(offer.pickup, offer.dropoff)

    if prev_location is not None:
        extra_time += distance_provider.travel_time(prev_location, offer.pickup)
        extra_distance += distance_provider.travel_distance(prev_location, offer.pickup)

    if next_location is not None:
        extra_time += distance_provider.travel_time(offer.dropoff, next_location)
        extra_distance += distance_provider.travel_distance(offer.dropoff, next_location)

    if prev_location is not None and next_location is not None:
        # Se resta el tramo directo prev->next que la insercion reemplaza;
        # sin esto el costo contaria dos veces ese segmento.
        extra_time -= distance_provider.travel_time(prev_location, next_location)
        extra_distance -= distance_provider.travel_distance(prev_location, next_location)

    return extra_time, extra_distance


def _build_route_with_insertion(
    current_route: list[RouteStop],
    offer: Offer,
    index: int,
    accepted_offers: dict[str, Offer],
    distance_provider: DistanceProvider,
) -> list[RouteStop]:
    prev_stop = current_route[index - 1] if index > 0 else None
    prev_location = _location_of(prev_stop, accepted_offers) if prev_stop else None

    pickup_eta, dropoff_eta = _pickup_dropoff_eta(offer, prev_stop, prev_location, distance_provider)

    new_route = (
        current_route[:index]
        + [
            RouteStop(offer_id=offer.id, kind="pickup", eta=pickup_eta),
            RouteStop(offer_id=offer.id, kind="dropoff", eta=dropoff_eta),
        ]
        + current_route[index:]
    )

    # Las paradas posteriores al punto de insercion se recalculan en cadena:
    # el desvio de la nueva oferta las retrasa (o adelanta) a todas.
    offers_with_new = dict(accepted_offers)
    offers_with_new[offer.id] = offer

    prev_location, prev_eta = offer.dropoff, dropoff_eta
    for k in range(index + 2, len(new_route)):
        stop = new_route[k]
        location = _location_of(stop, offers_with_new)
        new_eta = prev_eta + distance_provider.travel_time(prev_location, location)
        new_route[k] = RouteStop(offer_id=stop.offer_id, kind=stop.kind, eta=new_eta)
        prev_location, prev_eta = location, new_eta

    return new_route


def cheapest_insertion(
    current_route: list[RouteStop],
    offer: Offer,
    frozen_index: int,
    accepted_offers: dict[str, Offer],
    distance_provider: DistanceProvider,
) -> InsertionResult:
    """Calcula la posicion mas barata para insertar `offer` en `current_route`.

    `frozen_index` marca hasta donde el tramo esta comprometido (frozen
    horizon, ver docs/01_Arquitectura.md seccion 6): no se busca ninguna
    posicion antes de ese indice.

    `accepted_offers` debe contener, como minimo, las ofertas de todas las
    paradas ya presentes en `current_route` (para resolver sus coordenadas
    por `offer_id`) -- normalmente `{o.id: o for o in snapshot.backpack}`.

    Si ninguna posicion respeta `offer.time_window`, devuelve un resultado
    con `feasible=False` y `new_route=None` en vez de lanzar una excepcion:
    decidir que hacer con una oferta no insertable es responsabilidad de
    decision.py, no de esta funcion.
    """

    window_start, window_end = offer.time_window

    if not current_route:
        pickup_eta, dropoff_eta = _pickup_dropoff_eta(offer, None, None, distance_provider)
        if not (window_start <= dropoff_eta <= window_end):
            return InsertionResult(extra_distance=0.0, extra_time=0.0, new_route=None, feasible=False)

        extra_time = dropoff_eta - pickup_eta
        extra_distance = distance_provider.travel_distance(offer.pickup, offer.dropoff)
        new_route = [
            RouteStop(offer_id=offer.id, kind="pickup", eta=pickup_eta),
            RouteStop(offer_id=offer.id, kind="dropoff", eta=dropoff_eta),
        ]
        return InsertionResult(extra_distance, extra_time, new_route, feasible=True)

    best_index: int | None = None
    best_cost: tuple[float, float] | None = None

    for index in range(frozen_index, len(current_route) + 1):
        prev_stop = current_route[index - 1] if index > 0 else None
        next_stop = current_route[index] if index < len(current_route) else None

        prev_location = _location_of(prev_stop, accepted_offers) if prev_stop else None
        next_location = _location_of(next_stop, accepted_offers) if next_stop else None

        _, dropoff_eta = _pickup_dropoff_eta(offer, prev_stop, prev_location, distance_provider)
        if not (window_start <= dropoff_eta <= window_end):
            continue

        extra_time, extra_distance = _insertion_cost(offer, prev_location, next_location, distance_provider)
        cost = (extra_time, extra_distance)  # tupla: desempate por distancia si el tiempo empata

        if best_cost is None or cost < best_cost:
            best_cost, best_index = cost, index

    if best_index is None:
        return InsertionResult(extra_distance=0.0, extra_time=0.0, new_route=None, feasible=False)

    extra_time, extra_distance = best_cost
    new_route = _build_route_with_insertion(current_route, offer, best_index, accepted_offers, distance_provider)

    return InsertionResult(extra_distance, extra_time, new_route, feasible=True)
