"""Bloque 4 - Optimizador Global (hilo en background).

Resuelve el reordenamiento de paradas pendientes como VRPTW con OR-Tools,
sin bloquear el hilo principal. Ver docs/01_Arquitectura.md seccion 3
(Bloque 4) y seccion 6 (reglas de estabilidad).

Supuesto pendiente de confirmar con el equipo (no esta definido en el
contrato compartido, ver core/models.py): `Offer.time_window` se interpreta
aqui como una ventana RELATIVA en minutos desde "ahora" (el momento en que
se resuelve), no como un timestamp absoluto del turno -- porque
`CourierState` no expone el tiempo transcurrido del turno, solo el
restante. Si el equipo decide que es absoluto, hay que ajustar
`_dropoff_deadline`.
"""

from __future__ import annotations

import threading

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from core.models import CourierState, Offer, RouteStop
from core.routing.graph import RoadNetwork
from core.simulation.state import CourierStateManager

# Umbral de histeresis (configurables, ver docs/02_Documentacion_Tecnica.md seccion 5)
MIN_DISTANCE_IMPROVEMENT_PCT = 0.05
MIN_TIME_SAVED_MINUTES = 2.0

# OR-Tools trabaja con enteros: escalamos minutos x100 para no perder
# precision de segundos al truncar.
TIME_SCALE = 100
# Techo para arcos "practicamente cerrados" (eta_min == inf por un cierre):
# no se puede pasar infinito a OR-Tools, pero un numero enorme hace que el
# solver evite ese arco salvo que sea la unica opcion.
LARGE_PENALTY = 10**7
SOLVE_TIME_LIMIT_S = 3


class GlobalOptimizer:
    def __init__(self, state_manager: CourierStateManager, road_network: RoadNetwork) -> None:
        self.state_manager = state_manager
        self.road_network = road_network

    def solve_async(self, override: bool = False) -> threading.Thread:
        """Dispara la resolucion en un thread aparte.

        `override=True` (evento topologico invalida el tramo comprometido)
        salta el freeze y el umbral de histeresis. Devuelve el thread (para
        poder hacer `.join()` en tests; en produccion es fire-and-forget).
        """
        thread = threading.Thread(target=self._solve, args=(override,), daemon=True)
        thread.start()
        return thread

    def _solve(self, override: bool) -> None:
        snapshot = self.state_manager.snapshot()

        # El primer stop de la ruta es el tramo comprometido (frozen
        # horizon, ver docs/01_Arquitectura.md seccion 6): no se reordena.
        # Con 0 o 1 stops no hay nada que reordenar.
        if len(snapshot.route) < 2:
            return

        frozen_stop, *pending_stops = snapshot.route
        locations = [self._stop_location(s, snapshot.backpack) for s in snapshot.route]

        new_pending = self._solve_vrptw(
            frozen_location=locations[0],
            pending_stops=pending_stops,
            pending_locations=locations[1:],
            backpack=snapshot.backpack,
        )
        if new_pending is None:
            # Sin solucion factible (p.ej. un cierre desconecto todo): se
            # conserva la ruta actual y se reintenta en el proximo disparador.
            return

        new_route = [frozen_stop, *new_pending]

        if not override and not self._improves_enough(locations, new_route, snapshot):
            return

        self.state_manager.apply_optimized_route(snapshot.version, new_route)

    def _improves_enough(
        self, current_locations: list[tuple[float, float]], new_route: list[RouteStop], snapshot: CourierState
    ) -> bool:
        new_locations = [self._stop_location(s, snapshot.backpack) for s in new_route]
        current_km = self._route_distance_km(current_locations)
        new_km = self._route_distance_km(new_locations)
        current_min = self._route_time_min(current_locations)
        new_min = self._route_time_min(new_locations)
        return self._passes_hysteresis(current_km, new_km, current_min - new_min)

    def _passes_hysteresis(self, current_distance: float, new_distance: float, time_saved: float) -> bool:
        if current_distance == 0:
            return True
        improvement_pct = (current_distance - new_distance) / current_distance
        return improvement_pct > MIN_DISTANCE_IMPROVEMENT_PCT or time_saved > MIN_TIME_SAVED_MINUTES

    # -- VRPTW con OR-Tools -------------------------------------------------

    def _solve_vrptw(
        self,
        frozen_location: tuple[float, float],
        pending_stops: list[RouteStop],
        pending_locations: list[tuple[float, float]],
        backpack: list[Offer],
    ) -> list[RouteStop] | None:
        n_pending = len(pending_stops)
        if n_pending == 0:
            return []
        if n_pending == 1:
            return pending_stops  # una sola parada: no hay nada que reordenar

        all_locations = [frozen_location, *pending_locations]
        n = len(all_locations)
        end_node = n  # nodo ficticio: ruta abierta, el courier no regresa al origen

        time_matrix = self._time_matrix(all_locations)

        manager = pywrapcp.RoutingIndexManager(n + 1, 1, [0], [end_node])
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_index: int, to_index: int) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            if from_node == end_node or to_node == end_node:
                return 0
            return time_matrix[from_node][to_node]

        transit_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        routing.AddDimension(
            transit_callback_index,
            LARGE_PENALTY,  # slack maximo: se permite esperar si conviene
            LARGE_PENALTY,  # horizonte maximo acumulado
            True,  # el cronometro arranca en 0 en el nodo de partida
            "Time",
        )
        time_dimension = routing.GetDimensionOrDie("Time")

        pending_index_by_stop: dict[tuple[str, str], int] = {}
        for i, stop in enumerate(pending_stops, start=1):
            pending_index_by_stop[(stop.offer_id, stop.kind)] = i
            if stop.kind == "dropoff":
                offer = self._offer_by_id(backpack, stop.offer_id)
                deadline = self._dropoff_deadline(offer)
                node_index = manager.NodeToIndex(i)
                time_dimension.CumulVar(node_index).SetRange(0, deadline)

        # Precedencia: si el pickup y el dropoff de una misma oferta siguen
        # ambos pendientes, el pickup debe ir antes que el dropoff. Si el
        # pickup ya se hizo (esta en el tramo comprometido o ya entregado),
        # solo el dropoff queda pendiente y no aplica esta restriccion.
        for offer in backpack:
            pickup_i = pending_index_by_stop.get((offer.id, "pickup"))
            dropoff_i = pending_index_by_stop.get((offer.id, "dropoff"))
            if pickup_i is not None and dropoff_i is not None:
                pickup_index = manager.NodeToIndex(pickup_i)
                dropoff_index = manager.NodeToIndex(dropoff_i)
                routing.AddPickupAndDelivery(pickup_index, dropoff_index)
                routing.solver().Add(routing.VehicleVar(pickup_index) == routing.VehicleVar(dropoff_index))
                routing.solver().Add(
                    time_dimension.CumulVar(pickup_index) <= time_dimension.CumulVar(dropoff_index)
                )

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search_params.time_limit.FromSeconds(SOLVE_TIME_LIMIT_S)

        solution = routing.SolveWithParameters(search_params)
        if solution is None:
            return None

        ordered_stops: list[RouteStop] = []
        index = solution.Value(routing.NextVar(routing.Start(0)))
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            ordered_stops.append(pending_stops[node - 1])
            index = solution.Value(routing.NextVar(index))
        return ordered_stops

    def _dropoff_deadline(self, offer: Offer) -> int:
        minutes = max(0.0, offer.time_window[1])
        return int(round(minutes * TIME_SCALE))

    def _time_matrix(self, locations: list[tuple[float, float]]) -> list[list[int]]:
        n = len(locations)
        matrix = [[0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                eta = self.road_network.travel_time(locations[i], locations[j])
                if eta == float("inf"):
                    eta = LARGE_PENALTY / TIME_SCALE
                matrix[i][j] = int(round(eta * TIME_SCALE))
        return matrix

    def _route_distance_km(self, locations: list[tuple[float, float]]) -> float:
        total = 0.0
        for a, b in zip(locations, locations[1:]):
            leg = self.road_network.travel_distance(a, b)
            total += leg if leg != float("inf") else LARGE_PENALTY
        return total

    def _route_time_min(self, locations: list[tuple[float, float]]) -> float:
        total = 0.0
        for a, b in zip(locations, locations[1:]):
            leg = self.road_network.travel_time(a, b)
            total += leg if leg != float("inf") else LARGE_PENALTY
        return total

    @staticmethod
    def _offer_by_id(backpack: list[Offer], offer_id: str) -> Offer:
        for offer in backpack:
            if offer.id == offer_id:
                return offer
        raise KeyError(f"Offer {offer_id} no esta en la mochila")

    @classmethod
    def _stop_location(cls, stop: RouteStop, backpack: list[Offer]) -> tuple[float, float]:
        offer = cls._offer_by_id(backpack, stop.offer_id)
        return offer.pickup if stop.kind == "pickup" else offer.dropoff
