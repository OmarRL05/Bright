"""
Bloque 4 - Optimizador Global
=============================
Proyecto: The Courier (HackMTY 2026)

Este módulo implementa el optimizador de rutas en background para el
simulador de entregas de última milla. Corre en un hilo secundario,
escucha una cola de prioridad de eventos (Override / Batch / Idle) y
resuelve un VRPTW (Vehicle Routing Problem with Time Windows) de un
solo vehículo (el repartidor) usando Google OR-Tools.

Puntos de inyección de dependencias:
    - Bloque 2 (State Manager): objeto que cumple `StateManagerProtocol`.
    - Bloque 5 (Topology Engine): objeto que cumple `TopologyEngineProtocol`.

Ninguna implementación real de esos bloques vive aquí: solo se define
el contrato (Protocol) que deben cumplir, marcado explícitamente en el
código con comentarios "INYECCIÓN BLOQUE X".
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Protocol, Tuple

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ImportError as exc:  # pragma: no cover - guía de instalación
    raise ImportError(
        "OR-Tools no está instalado. Ejecuta: pip install ortools"
    ) from exc


logger = logging.getLogger("the_courier.global_optimizer")


# ---------------------------------------------------------------------------
# Modelos de datos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Node:
    """Representa una parada (o el depósito virtual) para el VRPTW."""

    node_id: str
    time_window_start: int  # segundos desde el inicio de la simulación
    time_window_end: int
    service_time: int = 0
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass(frozen=True)
class EstadoRuta:
    """
    Contrato de lo que el Bloque 2 (State Manager) debe devolver en
    `snapshot()`. El Bloque 4 solo lee estos campos; nunca los muta.
    """

    driver_current_position: Node
    # Parada a la que el repartidor YA se dirige (tramo comprometido).
    active_leg_destination: Node
    # Paradas aceptadas pero aún no asignadas a una ruta fija.
    pending_stops: List[Node]
    remaining_route_distance: float
    remaining_route_time: float
    # True si un evento topológico bloqueó el tramo activo (solo relevante
    # junto con un evento de tipo OVERRIDE).
    segment_blocked: bool = False


@dataclass(frozen=True)
class RutaOptimizada:
    """Payload que se intenta hacer commit en el State Manager."""

    depot: Node
    ordered_stop_ids: List[str]
    total_distance: float
    total_time: float
    # True cuando `depot` es el tramo comprometido real (active_leg_destination,
    # caso normal) y por lo tanto debe conservarse como primer elemento de la
    # ruta. False cuando `depot` fue la posición viva del repartidor por un
    # bypass de horizonte congelado (Override con segmento bloqueado) y NO
    # es una parada que deba insertarse en la ruta.
    depot_is_pending_stop: bool = True
    generated_at: float = field(default_factory=time.time)


class EventType(IntEnum):
    """Prioridad numérica: menor valor = mayor prioridad en la cola."""

    OVERRIDE = 0  # Emergencia: calle cerrada / tráfico invalidó la ruta.
    BATCH = 1     # 3+ pedidos nuevos aceptados sin optimizar.
    IDLE = 1      # Repartidor por iniciar el siguiente tramo.


@dataclass(frozen=True)
class OptimizationEvent:
    event_type: EventType
    payload: dict
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Contratos (Protocols) para Bloque 2 y Bloque 5
# ---------------------------------------------------------------------------

class StateManagerProtocol(Protocol):
    """Contrato esperado del Bloque 2 (State Manager)."""

    def snapshot(self) -> Tuple[EstadoRuta, int]:
        """Devuelve (estado_actual, version) de forma atómica."""
        ...

    def apply_optimized_route(
        self, version: int, nueva_ruta: RutaOptimizada
    ) -> bool:
        """
        Intenta aplicar la nueva ruta si `version` sigue vigente
        (concurrencia optimista). Devuelve True si tuvo éxito, False si
        el estado cambió mientras tanto y el commit fue rechazado.
        """
        ...


class TopologyEngineProtocol(Protocol):
    """Contrato esperado del Bloque 5 (Topology Engine)."""

    def get_distance_matrix(self, nodos: List[Node]) -> List[List[int]]:
        """
        Devuelve una matriz asimétrica NxN de tiempos/distancias (enteros)
        entre `nodos`, en el mismo orden que la lista recibida.
        """
        ...


# ---------------------------------------------------------------------------
# Optimizador Global
# ---------------------------------------------------------------------------

class GlobalOptimizer(threading.Thread):
    """
    Worker en background que escucha eventos de optimización y recalcula
    la ruta restante del repartidor respetando el horizonte congelado y
    el umbral de histéresis.
    """

    def __init__(
        self,
        state_manager: StateManagerProtocol,
        topology_engine: TopologyEngineProtocol,
        hysteresis_distance_pct: float = 0.05,
        hysteresis_time_seconds: float = 120.0,
        solver_time_limit_seconds: int = 3,
        name: str = "GlobalOptimizerThread",
    ) -> None:
        super().__init__(name=name, daemon=True)
        self._state_manager = state_manager
        self._topology_engine = topology_engine
        self._hysteresis_distance_pct = hysteresis_distance_pct
        self._hysteresis_time_seconds = hysteresis_time_seconds
        self._solver_time_limit_seconds = solver_time_limit_seconds

        self._queue: "queue.PriorityQueue[Tuple[int, int, Optional[OptimizationEvent]]]" = (
            queue.PriorityQueue()
        )
        self._counter = itertools.count()  # desempate FIFO en misma prioridad
        self._stop_event = threading.Event()

    # -- API pública ---------------------------------------------------

    def submit_event(self, event_type: EventType, payload: Optional[dict] = None) -> None:
        """
        Encola un disparador. Llamado desde otros hilos (p.ej. el motor
        de simulación principal o el listener de eventos topológicos).
        """
        event = OptimizationEvent(event_type=event_type, payload=payload or {})
        self._queue.put((int(event_type), next(self._counter), event))
        logger.debug("Evento encolado: %s (payload=%s)", event_type.name, payload)

    def stop(self, wait: bool = True) -> None:
        """Señala apagado ordenado del worker."""
        self._stop_event.set()
        # Sentinel para desbloquear un get() en espera.
        self._queue.put((-1, next(self._counter), None))
        if wait:
            self.join(timeout=5.0)

    # -- Loop principal --------------------------------------------------

    def run(self) -> None:
        logger.info("GlobalOptimizer iniciado.")
        while not self._stop_event.is_set():
            try:
                _priority, _seq, event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if event is None:  # sentinel de apagado
                self._queue.task_done()
                break

            try:
                self._process_event(event)
            except Exception:
                logger.exception(
                    "Error no manejado procesando evento %s", event.event_type.name
                )
            finally:
                self._queue.task_done()

        logger.info("GlobalOptimizer detenido.")

    # -- Lógica de negocio -------------------------------------------------

    def _process_event(self, event: OptimizationEvent) -> None:
        is_override = event.event_type == EventType.OVERRIDE

        # === INYECCIÓN BLOQUE 2: snapshot atómico del estado ===
        estado, version = self._state_manager.snapshot()

        # El horizonte congelado solo se rompe si es un Override Y el
        # tramo actual está efectivamente bloqueado.
        bypass_frozen_horizon = is_override and estado.segment_blocked

        depot_node, movable_stops = self._compute_frozen_horizon(
            estado, bypass_frozen_horizon
        )

        if len(movable_stops) == 0:
            logger.debug(
                "Sin paradas reordenables (evento=%s); se omite optimización.",
                event.event_type.name,
            )
            return

        nodes: List[Node] = [depot_node] + movable_stops

        # === INYECCIÓN BLOQUE 5: matriz de distancias/tiempos ===
        try:
            distance_matrix = self._topology_engine.get_distance_matrix(nodes)
        except Exception:
            logger.exception("Fallo obteniendo matriz de distancias del topology_engine.")
            return

        self._validate_matrix(distance_matrix, len(nodes))

        solved = self._solve_vrptw(nodes, distance_matrix)
        if solved is None:
            logger.warning(
                "OR-Tools no encontró solución factible (evento=%s).",
                event.event_type.name,
            )
            return

        new_route_ids, new_distance, new_time = solved

        aplica = bypass_frozen_horizon or self._passes_hysteresis(
            current_distance=estado.remaining_route_distance,
            current_time=estado.remaining_route_time,
            new_distance=new_distance,
            new_time=new_time,
        )

        if not aplica:
            logger.info(
                "Ruta descartada por umbral de histéresis "
                "(dist_actual=%.2f, dist_nueva=%.2f, t_actual=%.2f, t_nueva=%.2f).",
                estado.remaining_route_distance,
                new_distance,
                estado.remaining_route_time,
                new_time,
            )
            return

        nueva_ruta = RutaOptimizada(
            depot=depot_node,
            ordered_stop_ids=new_route_ids,
            total_distance=new_distance,
            total_time=new_time,
            depot_is_pending_stop=not bypass_frozen_horizon,
        )

        # === INYECCIÓN BLOQUE 2: commit optimista ===
        exito = self._state_manager.apply_optimized_route(version, nueva_ruta)
        if exito:
            logger.info(
                "Ruta aplicada (%d paradas, evento=%s).",
                len(new_route_ids),
                event.event_type.name,
            )
        else:
            logger.info(
                "Commit optimista rechazado: la versión del estado cambió "
                "mientras se optimizaba (version_usada=%s). Ruta descartada.",
                version,
            )

    def _compute_frozen_horizon(
        self, estado: EstadoRuta, bypass_frozen_horizon: bool
    ) -> Tuple[Node, List[Node]]:
        """
        Determina el nodo "depósito" del modelo VRPTW y la lista de
        paradas que sí pueden reordenarse.

        - Caso normal: el depósito virtual es la posición del repartidor
          DESPUÉS de completar la parada inmediata (tramo comprometido).
          Solo se reordenan las paradas pendientes restantes.
        - Caso Override con tramo bloqueado: se ignora el tramo
          comprometido; el depósito es la posición real actual del
          repartidor y TODAS las paradas pendientes (incluida la que
          estaba en curso) vuelven a estar en juego.
        """
        if bypass_frozen_horizon:
            depot_node = estado.driver_current_position
            movable_stops = list(estado.pending_stops)
            # La parada que estaba en curso también debe poder reordenarse.
            if estado.active_leg_destination not in movable_stops:
                movable_stops = [estado.active_leg_destination] + movable_stops
            return depot_node, movable_stops

        depot_node = estado.active_leg_destination
        movable_stops = list(estado.pending_stops)
        return depot_node, movable_stops

    def _passes_hysteresis(
        self,
        current_distance: float,
        current_time: float,
        new_distance: float,
        new_time: float,
    ) -> bool:
        """
        Regla de umbral de histéresis: se acepta la ruta nueva si mejora
        la distancia restante en más de 5% O ahorra más de 2 minutos.
        """
        if current_distance is None or current_time is None or current_distance <= 0:
            # Sin línea base confiable para comparar: se acepta por defecto.
            return True

        distance_improvement_pct = (current_distance - new_distance) / current_distance
        time_savings_seconds = current_time - new_time

        pasa_distancia = distance_improvement_pct > self._hysteresis_distance_pct
        pasa_tiempo = time_savings_seconds > self._hysteresis_time_seconds

        return pasa_distancia or pasa_tiempo

    # -- OR-Tools ------------------------------------------------------------

    def _solve_vrptw(
        self, nodes: List[Node], distance_matrix: List[List[int]]
    ) -> Optional[Tuple[List[str], float, float]]:
        """
        Resuelve un VRPTW de un solo vehículo. `nodes[0]` es el depósito
        (posición congelada del repartidor). Devuelve
        (ids_de_paradas_en_orden, distancia_total, tiempo_total) o None
        si no hay solución factible.
        """
        num_nodes = len(nodes)
        num_vehicles = 1
        depot_index = 0

        manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, depot_index)
        routing = pywrapcp.RoutingModel(manager)

        def transit_callback(from_index: int, to_index: int) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return distance_matrix[from_node][to_node]

        transit_callback_index = routing.RegisterTransitCallback(transit_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        # Dimensión de tiempo para ventanas de tiempo (VRPTW).
        max_wait_time = 30 * 60  # 30 min de espera permitida en cada nodo
        max_route_time = 24 * 60 * 60  # techo de seguridad (1 día simulado)
        routing.AddDimension(
            transit_callback_index,
            max_wait_time,
            max_route_time,
            False,  # no forzar el cumul del tiempo a 0 en cada vehículo
            "Time",
        )
        time_dimension = routing.GetDimensionOrDie("Time")

        for node_index, node in enumerate(nodes):
            index = manager.NodeToIndex(node_index)
            time_dimension.CumulVar(index).SetRange(
                node.time_window_start, node.time_window_end
            )

        # Minimizar el "slack" de tiempo mejora la estabilidad de la solución.
        for vehicle_id in range(num_vehicles):
            routing.AddVariableMinimizedByFinalizer(
                time_dimension.CumulVar(routing.Start(vehicle_id))
            )
            routing.AddVariableMinimizedByFinalizer(
                time_dimension.CumulVar(routing.End(vehicle_id))
            )

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.FromSeconds(self._solver_time_limit_seconds)

        try:
            solution = routing.SolveWithParameters(search_parameters)
        except Exception:
            logger.exception("OR-Tools lanzó una excepción durante el solve.")
            return None

        if solution is None:
            return None

        ordered_ids: List[str] = []
        total_distance = 0
        index = routing.Start(0)
        while not routing.IsEnd(index):
            node_index = manager.IndexToNode(index)
            if node_index != depot_index:
                ordered_ids.append(nodes[node_index].node_id)
            next_index = solution.Value(routing.NextVar(index))
            if not routing.IsEnd(next_index):
                total_distance += distance_matrix[
                    manager.IndexToNode(index)
                ][manager.IndexToNode(next_index)]
            else:
                total_distance += distance_matrix[
                    manager.IndexToNode(index)
                ][manager.IndexToNode(next_index)]
            index = next_index

        end_index = routing.End(0)
        total_time = solution.Value(time_dimension.CumulVar(end_index)) - solution.Value(
            time_dimension.CumulVar(routing.Start(0))
        )

        return ordered_ids, float(total_distance), float(total_time)

    @staticmethod
    def _validate_matrix(distance_matrix: List[List[int]], expected_size: int) -> None:
        if len(distance_matrix) != expected_size:
            raise ValueError(
                f"La matriz de distancias tiene {len(distance_matrix)} filas, "
                f"se esperaban {expected_size}."
            )
        for row in distance_matrix:
            if len(row) != expected_size:
                raise ValueError(
                    f"Fila de la matriz con {len(row)} columnas, "
                    f"se esperaban {expected_size}."
                )


# ---------------------------------------------------------------------------
# Ayudas para disparar los 3 tipos de eventos desde otros bloques
# ---------------------------------------------------------------------------

def trigger_override(optimizer: GlobalOptimizer, blocked_edge: str) -> None:
    """Bloque de topología llama esto cuando detecta cierre/tráfico."""
    optimizer.submit_event(EventType.OVERRIDE, payload={"blocked_edge": blocked_edge})


def trigger_batch(optimizer: GlobalOptimizer, pending_orders_count: int) -> None:
    """Se llama cuando se acumulan 3+ pedidos nuevos sin optimizar."""
    if pending_orders_count >= 3:
        optimizer.submit_event(EventType.BATCH, payload={"count": pending_orders_count})


def trigger_idle(optimizer: GlobalOptimizer, driver_id: str) -> None:
    """Se llama cuando el repartidor termina una entrega y va a iniciar la siguiente."""
    optimizer.submit_event(EventType.IDLE, payload={"driver_id": driver_id})


# ---------------------------------------------------------------------------
# Demo mínima con mocks (NO usar en producción; solo para smoke-test local)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    class MockStateManager:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._version = 1
            depot = Node("driver_pos", 0, 10_000)
            active_leg = Node("stop_A", 0, 10_000)
            pending = [
                Node("stop_B", 0, 10_000),
                Node("stop_C", 0, 10_000),
                Node("stop_D", 0, 10_000),
            ]
            self._estado = EstadoRuta(
                driver_current_position=depot,
                active_leg_destination=active_leg,
                pending_stops=pending,
                remaining_route_distance=1000.0,
                remaining_route_time=1200.0,
                segment_blocked=False,
            )

        def snapshot(self) -> Tuple[EstadoRuta, int]:
            with self._lock:
                return self._estado, self._version

        def apply_optimized_route(self, version: int, nueva_ruta: RutaOptimizada) -> bool:
            with self._lock:
                if version != self._version:
                    return False
                self._version += 1
                logger.info("[MOCK STATE MANAGER] Nueva ruta comprometida: %s", nueva_ruta)
                return True

    class MockTopologyEngine:
        def get_distance_matrix(self, nodos: List[Node]) -> List[List[int]]:
            n = len(nodos)
            # Matriz asimétrica ficticia solo para smoke-test.
            return [[0 if i == j else abs(i - j) * 100 + 50 for j in range(n)] for i in range(n)]

    optimizer = GlobalOptimizer(MockStateManager(), MockTopologyEngine(), solver_time_limit_seconds=1)
    optimizer.start()

    trigger_batch(optimizer, pending_orders_count=3)
    time.sleep(2)

    optimizer.stop()