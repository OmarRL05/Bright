"""Bloque 4 - Optimizador Global (hilo en background).

Resuelve el reordenamiento de paradas pendientes como VRPTW con OR-Tools,
sin bloquear el hilo principal. Ver docs/01_Arquitectura.md seccion 3
(Bloque 4) y seccion 6 (reglas de estabilidad).

TODO(equipo): implementar el modelo VRPTW con ortools.constraint_solver.
"""

import threading

from core.models import RouteStop
from core.simulation.state import CourierStateManager

# Umbral de histeresis (configurables, ver docs/02_Documentacion_Tecnica.md seccion 5)
MIN_DISTANCE_IMPROVEMENT_PCT = 0.05
MIN_TIME_SAVED_MINUTES = 2.0


class GlobalOptimizer:
    def __init__(self, state_manager: CourierStateManager) -> None:
        self.state_manager = state_manager

    def solve_async(self, override: bool = False) -> None:
        """Dispara la resolucion en un thread aparte.

        `override=True` (evento topologico invalida el tramo comprometido)
        salta el freeze y el umbral de histeresis.
        """
        thread = threading.Thread(target=self._solve, args=(override,), daemon=True)
        thread.start()

    def _solve(self, override: bool) -> None:
        """TODO(equipo):
        1. snapshot = self.state_manager.snapshot()
        2. armar el modelo VRPTW con OR-Tools tomando como origen fijo la
           posicion futura del courier tras el tramo comprometido
        3. si no es override, aplicar el umbral de histeresis antes de
           intentar aplicar la ruta
        4. self.state_manager.apply_optimized_route(snapshot.version, ruta)
        """
        raise NotImplementedError

    def _passes_hysteresis(self, current_distance: float, new_distance: float, time_saved: float) -> bool:
        if current_distance == 0:
            return True
        improvement_pct = (current_distance - new_distance) / current_distance
        return improvement_pct > MIN_DISTANCE_IMPROVEMENT_PCT or time_saved > MIN_TIME_SAVED_MINUTES
