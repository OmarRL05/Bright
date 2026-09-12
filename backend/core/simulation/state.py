"""Bloque 2 - Gestor de Estado del Agente (Courier State Manager).

Unica fuente de verdad del turno, en memoria. Ver docs/01_Arquitectura.md
seccion 3 (Bloque 2) y seccion 5 (Modelo de concurrencia).

Se instancia una vez por agente (agente IA y baseline usan cada uno la suya).
"""

import threading

from core.models import CourierState, Offer, RouteStop


class CourierStateManager:
    def __init__(self, shift_duration: float) -> None:
        self._lock = threading.Lock()
        self._version = 0
        self._time_remaining = shift_duration
        self._earnings = 0.0
        self._backpack: list[Offer] = []
        self._route: list[RouteStop] = []

    def snapshot(self) -> CourierState:
        """Lectura consistente del estado y su version actual."""
        with self._lock:
            return CourierState(
                version=self._version,
                time_remaining=self._time_remaining,
                earnings=self._earnings,
                backpack=list(self._backpack),
                route=list(self._route),
            )

    def accept_offer(self, offer: Offer, new_route: list[RouteStop]) -> int:
        """Bloque 3 llama esto al aceptar una oferta. Devuelve la nueva version."""
        with self._lock:
            self._backpack.append(offer)
            self._earnings += offer.pay
            self._route = new_route
            self._version += 1
            return self._version

    def apply_optimized_route(self, solved_version: int, new_route: list[RouteStop]) -> bool:
        """Bloque 4 llama esto al terminar de resolver.

        Solo aplica la ruta si la version no cambio desde que empezo a resolver;
        si cambio, la descarta (evita sobrescribir con una propuesta basada en
        una mochila que ya no existe). Devuelve True si se aplico.
        """
        with self._lock:
            if solved_version != self._version:
                return False
            self._route = new_route
            self._version += 1
            return True

    def tick(self, elapsed: float) -> None:
        """Bloque 1 llama esto en cada tick del reloj de simulacion."""
        with self._lock:
            self._time_remaining = max(0.0, self._time_remaining - elapsed)
