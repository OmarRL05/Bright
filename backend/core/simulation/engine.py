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

Corregido tras el merge a B5 (ver docs/03_Integracion_API_Decide.md): el
event log JSONL ahora emite EXACTAMENTE los 8 tipos y campos que exige
student-materials/courier/event_log_schema.json -- antes usaba un
vocabulario interno (tick/offer_received/offer_accepted/offer_rejected/
stop_completed/road_event/route_optimized) que no era ninguno de los 8
oficiales y que `validate_format.py --event-log` rechazaba linea por linea.
Consecuencias concretas de ese fix:
- `sim_time` se serializa como ISO 8601 (no como minuto float): se ancla en
  `shift_start_time` (parametro nuevo, determinista -- nunca
  `datetime.now()`, para no romper el replay del protocolo de evaluacion).
- `zone_pickup`/`zone_dropoff` de cada oferta se resuelven a `Zone.zone_id`
  via `zone_map.nearest_zone(coord)` -- el contrato oficial identifica
  zonas con enteros, no con coordenadas.
- El tick de reloj (antes un evento `tick` propio) ya no se loguea: el
  contrato oficial no tiene ese tipo de evento, y cada `order_offered`/
  `shock` ya lleva su propio `sim_time`.
- `log_stop_completed`/`log_route_optimized` (antes `stop_completed`/
  `route_optimized`) se quitaron: no tienen equivalente oficial y nada en
  produccion los llamaba. `log_offer_decision` ahora emite el evento
  `decision` unico (ACCEPT/SKIP), no dos eventos separados.
- `position_update`, `earnings_update` y `strategy_update` siguen sin
  productor: le corresponden a CourierStateManager/DecisionEngine (P0.1/
  Bloque 3), no a este generador de stream. No se inventaron aqui.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from datetime import datetime, timedelta
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
from core.routing.euclidean import EuclideanDistanceProvider

# Ancla determinista del reloj de simulacion -- NUNCA datetime.now() (el
# protocolo de evaluacion corre replay y compara decisiones byte a byte,
# seccion 6; leer el reloj de pared rompe esa garantia). Solo importa como
# punto de partida para producir timestamps ISO legibles; el offset en
# minutos (self.current_time) sigue siendo la fuente de verdad interna.
DEFAULT_SHIFT_START_TIME = datetime(2026, 1, 1, 8, 0, 0)


class SimulationEngine:

    def __init__(
        self,
        seed: int,
        shift_duration: float,
        zone_map: ZoneMap | None = None,
        vehicle_type: VehicleType = VehicleType.MOTO,
        agent_id: str = "ai",
        log_file: IO[str] | None = None,
        shift_start_time: datetime = DEFAULT_SHIFT_START_TIME,
        start_location_zone: int | None = None,
    ) -> None:
        """
        Args:
            seed:                Semilla fija para reproducibilidad del stream.
            shift_duration:      Duración del turno en minutos.
            zone_map:            Catálogo de zonas. None → DEFAULT_ZONE_MAP.
            vehicle_type:        Perfil de vehículo (moto/car/bike, P0.5).
            agent_id:            "ai" o "baseline" — etiqueta en el event log.
            log_file:            Archivo abierto para escribir el log JSONL
                                  (P1.1). None → no se escribe log. Caller es
                                  responsable de abrir/cerrar el archivo.
            shift_start_time:    Ancla determinista para convertir minutos de
                                  turno a `sim_time` ISO 8601. Fija por
                                  defecto -- nunca leer el reloj de pared.
            start_location_zone: zone_id donde arranca el courier (evento
                                  shift_start). None → primera zona de
                                  zone_map.
        """
        self.seed = seed
        self.shift_duration = shift_duration
        self._rng = random.Random(seed)
        self.zone_map = zone_map if zone_map is not None else DEFAULT_ZONE_MAP
        self.vehicle: VehicleProfile = VEHICLE_PROFILES[vehicle_type]
        self.agent_id = agent_id
        self._log_file = log_file
        self.shift_start_time = shift_start_time
        self.start_location_zone = (
            start_location_zone if start_location_zone is not None else self.zone_map.zones[0].zone_id
        )
        self._distance = EuclideanDistanceProvider()

        self.current_time = 0.0
        self.offer_counter = 0

        # Campos extra del evento `shift_end`. Quien corre el turno los va
        # llenando (ganancias, entregas, violaciones); el generador no los
        # conoce. Como `event_stream` es perezoso y `shift_end` se escribe al
        # final, el dict ya esta completo cuando se serializa.
        self.shift_end_stats: dict = {}

        # Mantener la lista de coordenadas para que los tests de retrocompatibilidad
        # que accedan a _zones sigan funcionando.
        self._zones = self.zone_map.coords

    def _iso(self, minute_offset: float) -> str:
        """`sim_time` ISO 8601 para un offset en minutos desde el inicio del turno."""
        return (self.shift_start_time + timedelta(minutes=minute_offset)).isoformat()

    # ------------------------------------------------------------------
    # Generadores de eventos individuales
    # ------------------------------------------------------------------

    # Proporcion de pedidos "voluminosos" (muebles chicos, despensa grande,
    # pedidos corporativos). Existen porque sin ellos la constraint
    # `vehicle_capacity` no se dispara nunca en un turno y los tres perfiles de
    # vehiculo se vuelven indistinguibles en los resultados.
    BULKY_ORDER_RATE = 0.12

    # Proporcion de ofertas que llegan con surge activo.
    SURGE_ORDER_RATE = 0.25

    # Proporcion de pedidos sin propina.
    NO_TIP_RATE = 0.35

    # Litros por kilogramo: comida y despensa son voluminosas para su peso.
    LITERS_PER_KG = (2.5, 5.0)

    def _generate_offer(self) -> Offer:
        """Genera una oferta nueva reproducible.

        Emite las dos vistas de la misma oferta (coordenadas para el motor
        VRPTW, zonas enteras + peso/volumen/propina/surge para el contrato
        oficial). El orden de las extracciones del RNG es parte del contrato de
        determinismo: cambiarlo cambia el stream de una seed dada, asi que si
        se agrega un campo nuevo, va al final.
        """
        self.offer_counter += 1

        # Las ofertas NO se reparten uniformemente entre zonas: nacen donde hay
        # demanda. Sin esto, `demand_score` es un numero que el ZoneMap declara
        # y que nada usa, y "terminar en una zona caliente" no puede valer nada
        # porque el siguiente pedido sale igual de probable en cualquier lado.
        # Es lo que hace medible la categoria de sondeo "Dropoff location
        # value" del protocolo.
        pesos = [z.demand_score for z in self.zone_map.zones]
        pickup_zone = self._rng.choices(self.zone_map.zones, weights=pesos, k=1)[0]
        dropoff_zone = self._rng.choices(self.zone_map.zones, weights=pesos, k=1)[0]

        pay = float(self._rng.randint(40, 150))

        # Ventana de tiempo: el repartidor tiene entre 20 y 40 min para entregar.
        window_minutes = float(self._rng.randint(20, 40))

        # demand_percentile viene del score de la zona del pickup (P0.2)
        demand_percentile = pickup_zone.demand_score

        # --- campos del contrato oficial -----------------------------------
        if self._rng.random() < self.BULKY_ORDER_RATE:
            weight_kg = round(self._rng.uniform(6.0, 25.0), 1)
        else:
            weight_kg = round(self._rng.uniform(0.3, 6.0), 1)

        volume_liters = round(weight_kg * self._rng.uniform(*self.LITERS_PER_KG), 1)

        if self._rng.random() < self.SURGE_ORDER_RATE:
            surge_multiplier = round(self._rng.uniform(1.1, 2.2), 1)
        else:
            surge_multiplier = 1.0

        # La propina escala con la tarifa, no con el surge: es lo que deja el
        # cliente, no lo que pone la plataforma (ver core/agent/economics.py).
        if self._rng.random() < self.NO_TIP_RATE:
            est_tip_mxn = 0.0
        else:
            est_tip_mxn = round(pay * self._rng.uniform(0.05, 0.25), 1)

        restaurant_prep_min = float(self._rng.randint(3, 20))
        platform = self._rng.choice(["rappi", "didi", "uber"])

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
            zone_pickup=pickup_zone.zone_id,
            zone_dropoff=dropoff_zone.zone_id,
            weight_kg=weight_kg,
            volume_liters=volume_liters,
            est_tip_mxn=est_tip_mxn,
            surge_multiplier=surge_multiplier,
            restaurant_prep_min=restaurant_prep_min,
            platform=platform,
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
    # P1.1 — Event log JSONL (contrato oficial)
    # ------------------------------------------------------------------

    def log_event(self, event_type: EventType, sim_time_min: float, payload: dict) -> None:
        """Escribe un evento oficial al log. Punto de entrada para quien corre
        el turno: los eventos `decision`, `position_update` y `earnings_update`
        no los produce este generador (no sabe lo que el agente decidio ni
        donde esta el repartidor), pero tienen que salir por el mismo archivo y
        en el mismo orden cronologico."""
        self._log(event_type, sim_time_min, payload)

    def _log(self, event_type: EventType, sim_time_min: float, payload: dict) -> None:
        """Escribe una linea JSONL plana si hay archivo configurado.

        La linea final es `{"event": ..., "sim_time": ..., **payload,
        "agent_id": ...}` -- aplanada, tal como exige
        event_log_schema.json (ver nota de modulo).
        """
        if self._log_file is None:
            return
        entry = EventLogEntry(
            event_type=event_type,
            sim_time=self._iso(sim_time_min),
            payload=payload,
            agent_id=self.agent_id,
        )
        record = {
            "event": entry.event_type.value,
            "sim_time": entry.sim_time,
            **entry.payload,
            "agent_id": entry.agent_id,
        }
        self._log_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _log_shift_start(self) -> None:
        self._log(EventType.SHIFT_START, 0.0, {
            "seed": self.seed,
            "shift_hours": self.shift_duration / 60.0,
            "vehicle": self.vehicle.type.value,
            "start_location_zone": self.start_location_zone,
            "shift_end_time": self._iso(self.shift_duration),
            "fuel_mxn_per_km": self.vehicle.cost_per_km,
        })

    def _log_order_offered(self, offer: Offer, distance_pickup_km: float | None = None) -> None:
        """Evento `order_offered`.

        `distance_pickup_km` es el deadhead, y depende de DONDE ESTA el
        repartidor -- cosa que este generador no sabe y no debe saber. Quien
        corre el turno (el arnes de evaluacion, o el loop de simulacion) lo
        calcula desde la posicion actual y lo pasa aqui. Sin argumento queda en
        0.0, que es lo unico honesto que se puede decir sin esa posicion.
        """
        zone_pickup = (
            offer.zone_pickup
            if offer.zone_pickup is not None
            else self.zone_map.nearest_zone(offer.pickup).zone_id
        )
        zone_dropoff = (
            offer.zone_dropoff
            if offer.zone_dropoff is not None
            else self.zone_map.nearest_zone(offer.dropoff).zone_id
        )
        self._log(EventType.ORDER_OFFERED, self.current_time, {
            "order_id": offer.id,
            "platform": offer.platform,
            "zone_pickup": zone_pickup,
            "zone_dropoff": zone_dropoff,
            "distance_pickup_km": distance_pickup_km if distance_pickup_km is not None else 0.0,
            "distance_delivery_km": self._distance.travel_distance(offer.pickup, offer.dropoff),
            "base_pay_mxn": offer.pay,
            "est_tip_mxn": offer.est_tip_mxn,
            "surge_multiplier": offer.surge_multiplier,
            "restaurant_prep_min": offer.restaurant_prep_min,
            "weight_kg": offer.weight_kg,
            "volume_liters": offer.volume_liters,
            "vehicle": self.vehicle.type.value,
        })

    def _log_shock(self, event: RoadEvent) -> None:
        location = event.location[0] if isinstance(event.location, list) else event.location
        zone = self.zone_map.nearest_zone(location)
        shock_type = "surge" if event.type == "surge" else ("closure" if event.type == "closure" else "delay")
        self._log(EventType.SHOCK, self.current_time, {
            "shock_type": shock_type,
            "zone": zone.zone_id,
            **({"multiplier": event.multiplier} if event.multiplier is not None else {}),
        })

    def log_offer_decision(
        self,
        offer: Offer,
        accepted: bool,
        reason: str,
        latency_ms: float = 0.0,
    ) -> None:
        """Llamado por Bloque 3 (DecisionEngine) después de evaluar una oferta.

        Emite el evento oficial `decision` (ACCEPT/SKIP) -- antes eran dos
        eventos internos (offer_accepted/offer_rejected) sin equivalente en
        event_log_schema.json.
        """
        self._log(EventType.DECISION, self.current_time, {
            "order_id": offer.id,
            "decision": "ACCEPT" if accepted else "SKIP",
            "reason": reason,
            "latency_ms": latency_ms,
        })

    # ------------------------------------------------------------------
    # Loop principal de simulación
    # ------------------------------------------------------------------

    def event_stream(self) -> Iterator[Offer | RoadEvent]:
        """Genera el stream reproducible de eventos del turno.

        Retrocompatible con la versión v1: sigue siendo un Iterator de
        Offer | RoadEvent, y ahora también escribe el log JSONL oficial si
        se configuró log_file en el constructor.
        """
        self._log_shift_start()

        while self.current_time < self.shift_duration:
            self.current_time += 1

            chance = self._rng.random()

            # 40% probabilidad de nueva oferta
            if chance < 0.40:
                offer = self._generate_offer()
                self._log_order_offered(offer)
                yield offer

            # 10% probabilidad de evento externo
            elif chance < 0.50:
                event = self._generate_road_event()
                self._log_shock(event)
                yield event

        # Fin del turno -- orders_offered es lo unico que este generador
        # conoce por si solo; orders_completed/earnings_mxn/safety_violations
        # viven en CourierStateManager, no aqui.
        self._log(EventType.SHIFT_END, self.shift_duration, {
            "orders_offered": self.offer_counter,
            **self.shift_end_stats,
        })
