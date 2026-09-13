"""Bloque 2 - Gestor de Estado del Agente (Courier State Manager).

Unica fuente de verdad del turno, en memoria. Ver docs/01_Arquitectura.md
seccion 3 (Bloque 2) y seccion 5 (Modelo de concurrencia).

Se instancia una vez por agente (agente IA y baseline usan cada uno la suya).

Cambios v2 (Abraham, P0.1 / P0.6):
- tick() avanza sim_time, descuenta time_remaining Y actualiza position del
  courier interpolando sobre la ruta activa (P0.1).
- complete_stop() retira paradas completadas de la ruta y avisa si el turno
  ya no alcanza para llegar a la última parada (comparación fin-de-turno vs
  ETA final, P0.1).
- validate_windows() re-valida ventanas de tiempo de TODAS las paradas
  pendientes tras un recálculo de ETAs (Hallazgo 1 / P0.6): devuelve la
  lista de ofertas que quedaron infeasible para que el caller las rechace.
- El estado interno incluye ahora position (coordenada actual) y vehicle
  (perfil de vehículo), expuestos en el snapshot.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from core.models import (
    CourierState,
    Offer,
    RouteStop,
    VehicleProfile,
    VehicleType,
    VEHICLE_PROFILES,
)


@dataclass
class StaleWindowOffer:
    """Oferta cuya ventana de tiempo quedó infeasible tras recalcular ETAs."""
    offer: Offer
    stop_kind: str          # "pickup" o "dropoff" que ya no cabe
    actual_eta: float
    window_end: float


class CourierStateManager:
    def __init__(
        self,
        shift_duration: float,
        start_position: tuple[float, float] = (25.680, -100.310),   # Centro por defecto
        vehicle_type: VehicleType = VehicleType.MOTO,
    ) -> None:
        self._lock = threading.Lock()
        self._version = 0
        self._shift_duration = shift_duration
        self._time_remaining = shift_duration
        self._sim_time = 0.0
        self._earnings = 0.0
        self._backpack: list[Offer] = []
        self._route: list[RouteStop] = []
        self._position: tuple[float, float] = start_position
        self._vehicle: VehicleProfile = VEHICLE_PROFILES[vehicle_type]

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    def snapshot(self) -> CourierState:
        """Lectura consistente del estado y su version actual."""
        with self._lock:
            return CourierState(
                version=self._version,
                time_remaining=self._time_remaining,
                earnings=self._earnings,
                backpack=list(self._backpack),
                route=list(self._route),
                position=self._position,
                vehicle=self._vehicle,
                sim_time=self._sim_time,
            )

    # ------------------------------------------------------------------
    # Escritura — Bloque 1 (tick)
    # ------------------------------------------------------------------

    def tick(self, elapsed: float) -> list[StaleWindowOffer]:
        """Avanza el reloj de simulación en `elapsed` minutos.

        Responsabilidades (P0.1):
        1. Descuenta time_remaining.
        2. Avanza sim_time.
        3. Actualiza position interpolando el avance sobre la ruta activa.
        4. Retira paradas cuya ETA ya pasó (stop completado).
        5. Llama validate_windows() internamente y devuelve las ofertas
           cuya ventana quedó infeasible (P0.6) para que el engine las
           rechace/notifique.

        Nota: el lock se toma UNA sola vez para todo el batch de cambios,
        evitando condiciones de carrera con Bloque 4.
        """
        with self._lock:
            self._sim_time += elapsed
            self._time_remaining = max(0.0, self._time_remaining - elapsed)

            # Actualizar posición en la ruta
            self._position = self._interpolate_position(self._sim_time)

            # Retirar paradas completadas (ETA <= sim_time actual)
            self._route = [s for s in self._route if s.eta > self._sim_time]

            # Validar ventanas de las paradas que quedan (Hallazgo 1 / P0.6)
            stale = self._validate_windows_locked()
            if stale:
                self._version += 1

        return stale

    # ------------------------------------------------------------------
    # Escritura — Bloque 3 (acepta oferta)
    # ------------------------------------------------------------------

    def accept_offer(self, offer: Offer, new_route: list[RouteStop]) -> int:
        """Bloque 3 llama esto al aceptar una oferta. Devuelve la nueva version."""
        with self._lock:
            self._backpack.append(offer)
            self._earnings += offer.pay
            self._route = new_route
            self._version += 1
            return self._version

    # ------------------------------------------------------------------
    # Escritura — Bloque 4 (aplica ruta optimizada)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # P0.6 — Validación de ventanas tras recalcular ETAs
    # ------------------------------------------------------------------

    def validate_windows(self) -> list[StaleWindowOffer]:
        """Re-valida las ventanas de tiempo de todas las paradas pendientes.

        Devuelve la lista de ofertas infeasible (ventana ya expirada o ETA
        fuera del rango) para que el caller las rechace/notifique.

        Se llama públicamente desde el engine cuando recibe un RoadEvent que
        puede haber retrasado las ETAs (P0.6). Internamente también se llama
        desde tick() bajo el mismo lock.
        """
        with self._lock:
            return self._validate_windows_locked()

    def _validate_windows_locked(self) -> list[StaleWindowOffer]:
        """Versión sin lock — solo llamar cuando ya se tiene self._lock."""
        offer_map = {o.id: o for o in self._backpack}
        stale: list[StaleWindowOffer] = []

        for stop in self._route:
            offer = offer_map.get(stop.offer_id)
            if offer is None:
                continue
            window_start, window_end = offer.time_window
            if stop.eta > window_end:
                stale.append(StaleWindowOffer(
                    offer=offer,
                    stop_kind=stop.kind,
                    actual_eta=stop.eta,
                    window_end=window_end,
                ))
        return stale

    # ------------------------------------------------------------------
    # P0.1 — Posición actual del courier (interpolación sobre ruta)
    # ------------------------------------------------------------------

    def _interpolate_position(self, sim_time: float) -> tuple[float, float]:
        """Posición estimada del courier en sim_time, interpolando la ruta activa.

        Si la ruta está vacía, el courier queda en su posición actual (no se mueve).
        Si ya pasó la ETA de la última parada, la posición es la del último destino.
        De lo contrario, interpola linealmente entre la parada anterior y la siguiente.
        """
        if not self._route:
            return self._position

        offer_map = {o.id: o for o in self._backpack}

        # Encontrar el segmento activo: la primera parada cuya ETA > sim_time
        prev_time = sim_time     # "ahora"
        prev_pos = self._position

        for stop in self._route:
            stop_pos = _stop_coord(stop, offer_map)
            if stop_pos is None:
                continue
            if stop.eta <= sim_time:
                # Parada ya alcanzada: el courier está ahí o más allá
                prev_time = stop.eta
                prev_pos = stop_pos
            else:
                # stop.eta > sim_time: este es el destino inmediato
                if stop.eta == prev_time:
                    return stop_pos
                fraction = (sim_time - prev_time) / (stop.eta - prev_time)
                lat = prev_pos[0] + fraction * (stop_pos[0] - prev_pos[0])
                lon = prev_pos[1] + fraction * (stop_pos[1] - prev_pos[1])
                return (lat, lon)

        # Ya superó todas las ETAs — courier en la última parada
        return prev_pos

    # ------------------------------------------------------------------
    # P0.1 — Comparación fin-de-turno vs ETA final
    # ------------------------------------------------------------------

    def shift_ends_before_route(self) -> bool:
        """True si el turno terminará antes de que el courier complete su ruta.

        Compara time_remaining con la ETA de la última parada de la ruta.
        Usa el tiempo de simulación actual como referencia.
        """
        with self._lock:
            if not self._route:
                return False
            last_eta = self._route[-1].eta
            # time_remaining = shift_duration - sim_time (ya descontado en tick)
            # El turno termina en sim_time + time_remaining.
            shift_end_at = self._sim_time + self._time_remaining
            return last_eta > shift_end_at


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _stop_coord(
    stop: RouteStop,
    offer_map: dict[str, Offer],
) -> tuple[float, float] | None:
    offer = offer_map.get(stop.offer_id)
    if offer is None:
        return None
    return offer.pickup if stop.kind == "pickup" else offer.dropoff
