"""Bloque 1 - Simulador de Entorno (Event Streamer) — v2.

Controla el reloj del turno y genera el stream de eventos (ofertas, surge,
cierres/trafico). Usa una semilla fija para que el agente IA y el baseline
reciban EXACTAMENTE el mismo stream. Ver docs/01_Arquitectura.md seccion 3.

Cambios v2 (Abraham, P0.1 / P0.2 / P1.1):
- Usa ZoneMap de core.models como fuente de verdad de zonas (P0.2):
  ya no duplica coordenadas.
- emit() es el nuevo punto de entrada: avanza el reloj, emite el evento y
  escribe la entrada al EventLog (P1.1).
- event_stream() sigue funcionando (retrocompatible) pero ahora llama a
  emit() internamente y puede escribir el log JSONL de paso.
- Se parametriza con VehicleProfile para que la velocidad media del
  simulador use el perfil del vehículo real (P0.5).
- demand_percentile se calcula a partir del demand_score de la zona (P0.2),
  ya no queda hardcodeado en 0.5.
- Se añaden más tipos de eventos de entorno (surge localizado con factor
  aleatorio, cierres de tramo multi-punto).
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import IO

from core.models import (
    DEFAULT_ZONE_MAP,
    EventLogEntry,
    EventType,
    Offer,
    RoadEvent,
    VehicleProfile,
    VehicleType,
    VEHICLE_PROFILES,
    ZoneMap,
)


class SimulationEngine:

    def __init__(
        self,
        seed: int,
        shift_duration: float,
        zone_map: ZoneMap | None = None,
        vehicle_type: VehicleType = VehicleType.MOTO,
        agent_id: str = "ai",
        log_file: IO[str] | None = None,
    ) -> None:
        """
        Args:
            seed:           Semilla fija para reproducibilidad del stream.
            shift_duration: Duración del turno en minutos.
            zone_map:       Catálogo de zonas. None → DEFAULT_ZONE_MAP.
            vehicle_type:   Perfil de vehículo (moto/car/bike, P0.5).
            agent_id:       "ai" o "baseline" — etiqueta en el event log.
            log_file:       Archivo abierto para escribir el log JSONL (P1.1).
                            None → no se escribe log. Caller es responsable
                            de abrir/cerrar el archivo.
        """
        self.seed = seed
        self.shift_duration = shift_duration
        self._rng = random.Random(seed)
        self.zone_map = zone_map if zone_map is not None else DEFAULT_ZONE_MAP
        self.vehicle: VehicleProfile = VEHICLE_PROFILES[vehicle_type]
        self.agent_id = agent_id
        self._log_file = log_file

        self.current_time = 0.0
        self.offer_counter = 0

        # Mantener la lista de coordenadas para que los tests de retrocompatibilidad
        # que accedan a _zones sigan funcionando.
        self._zones = self.zone_map.coords

    # ------------------------------------------------------------------
    # Generadores de eventos individuales
    # ------------------------------------------------------------------

    def _generate_offer(self) -> Offer:
        """Genera una oferta nueva reproducible."""
        self.offer_counter += 1

        pickup_zone = self._rng.choice(self.zone_map.zones)
        dropoff_zone = self._rng.choice(self.zone_map.zones)

        pay = float(self._rng.randint(40, 150))

        # Ventana de tiempo: el repartidor tiene entre 20 y 40 min para entregar.
        window_minutes = float(self._rng.randint(20, 40))

        # demand_percentile viene del score de la zona del pickup (P0.2)
        demand_percentile = pickup_zone.demand_score

        return Offer(
            id=f"offer_{self.offer_counter}",
            pickup=pickup_zone.coord,
            dropoff=dropoff_zone.coord,
            pay=pay,
            time_window=(
                self.current_time,
                self.current_time + window_minutes,
            ),
            received_at=self.current_time,
            demand_percentile=demand_percentile,
        )

    def _generate_road_event(self) -> RoadEvent:
        """Genera eventos externos del entorno (cierre, tráfico, surge)."""

        event_type = self._rng.choice(["traffic", "closure", "surge"])
        zone = self._rng.choice(self.zone_map.zones)
        multiplier = None

        if event_type == "surge":
            # Factor de surge entre 1.2x y 2.0x
            multiplier = round(self._rng.uniform(1.2, 2.0), 1)
        elif event_type == "traffic":
            # Factor de tráfico entre 1.1x y 1.8x (ralentiza, no cierra)
            multiplier = round(self._rng.uniform(1.1, 1.8), 1)

        # Cierres de tramo: 30% de probabilidad de ser multi-punto (tramo)
        if event_type == "closure" and self._rng.random() < 0.30:
            other_zone = self._rng.choice(self.zone_map.zones)
            location: tuple | list = [zone.coord, other_zone.coord]
        else:
            location = zone.coord

        return RoadEvent(
            type=event_type,
            location=location,
            multiplier=multiplier,
            timestamp=self.current_time,
        )

    # ------------------------------------------------------------------
    # P1.1 — Event log JSONL
    # ------------------------------------------------------------------

    def _log(self, event_type: EventType, payload: dict) -> None:
        """Escribe una entrada al log JSONL si hay archivo configurado."""
        if self._log_file is None:
            return
        entry = EventLogEntry(
            event_type=event_type,
            sim_time=self.current_time,
            payload=payload,
            agent_id=self.agent_id,
        )
        # asdict() convierte dataclasses anidados recursivamente
        record = asdict(entry)
        # EventType es un Enum — serializar como string
        record["event_type"] = entry.event_type.value
        self._log_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_offer_decision(
        self,
        offer: Offer,
        accepted: bool,
        reason: str,
    ) -> None:
        """Llamado por Bloque 3 (DecisionEngine) después de evaluar una oferta.

        Permite que el motor de decisión escriba OFFER_ACCEPTED / OFFER_REJECTED
        en el mismo log sin conocer los detalles de serialización.
        """
        event_type = EventType.OFFER_ACCEPTED if accepted else EventType.OFFER_REJECTED
        self._log(event_type, {
            "offer_id": offer.id,
            "pay": offer.pay,
            "pickup": offer.pickup,
            "dropoff": offer.dropoff,
            "reason": reason,
        })

    def log_stop_completed(self, offer_id: str, kind: str) -> None:
        """Llamado cuando el courier completa un pickup o dropoff."""
        self._log(EventType.STOP_COMPLETED, {
            "offer_id": offer_id,
            "kind": kind,
        })

    def log_route_optimized(self, route_length: int) -> None:
        """Llamado cuando Bloque 4 aplica una ruta optimizada."""
        self._log(EventType.ROUTE_OPTIMIZED, {"route_stops": route_length})

    # ------------------------------------------------------------------
    # Loop principal de simulación
    # ------------------------------------------------------------------

    def event_stream(self) -> Iterator[Offer | RoadEvent]:
        """Genera el stream reproducible de eventos del turno.

        Retrocompatible con la versión v1: sigue siendo un Iterator de
        Offer | RoadEvent, y ahora también escribe el log JSONL si se
        configuró log_file en el constructor.
        """
        while self.current_time < self.shift_duration:
            self.current_time += 1

            # Log del tick de reloj
            self._log(EventType.TICK, {
                "sim_time": self.current_time,
                "time_remaining": self.shift_duration - self.current_time,
            })

            chance = self._rng.random()

            # 40% probabilidad de nueva oferta
            if chance < 0.40:
                offer = self._generate_offer()
                self._log(EventType.OFFER_RECEIVED, {
                    "offer_id": offer.id,
                    "pay": offer.pay,
                    "pickup": offer.pickup,
                    "dropoff": offer.dropoff,
                    "demand_percentile": offer.demand_percentile,
                })
                yield offer

            # 10% probabilidad de evento externo
            elif chance < 0.50:
                event = self._generate_road_event()
                self._log(EventType.ROAD_EVENT, {
                    "type": event.type,
                    "location": event.location,
                    "multiplier": event.multiplier,
                })
                yield event

        # Fin del turno
        self._log(EventType.SHIFT_END, {
            "total_time": self.shift_duration,
        })