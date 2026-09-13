"""Replay determinista: reproduce un turno grabado y difea las decisiones.

    "Judges may ask you to record a complete shift to an event log, replay
     that log against your running system, and diff the decisions.
     **Requirement:** identical fast-path accept/skip decisions on identical
     input. ... A mismatch indicates hidden mutable state, a wall-clock
     dependency, or a race between layers. You will be asked to identify
     which."
    -- evaluation_protocol.md, seccion 6

Que se reinyecta, y que se reconstruye
---------------------------------------
El log **es la entrada**. De cada `order_offered` se toman los numeros tal
como el sistema los vio entonces -- distancias, tarifa, surge, peso -- en vez
de recalcularlos. Recalcular convertiria el replay en "volver a correr el
simulador", que es otra cosa y mas facil de aprobar.

Lo unico que el log no lleva es el **estado del repartidor**, porque
`order_offered` no tiene ese campo. Se reconstruye avanzandolo con las mismas
operaciones que el turno original: al aceptar se ocupa al repartidor, se le
mueve de zona y se le suma manejo continuo; entre ofertas se drena la mochila
y se toman las pausas. Si la reconstruccion divergiera, las decisiones
divergirian, y **eso es justo lo que el diff detecta** -- no es un supuesto
que se cuela, es la hipotesis bajo prueba.

Los `strategy_update` y los `shock` del log se reinyectan en sus capas antes
de la primera oferta y en su `sim_time` respectivamente. Reinyectar el
primero ademas **clava** la capa de estrategia (ver
`StrategyLayer.apply_recorded`): sin eso, tier2 podria publicar una revision
a media reproduccion y el diff saldria distinto por diseño, no por un bug.

Que cuenta como discrepancia
-----------------------------
- **`decision`** (ACCEPT/SKIP): discrepancia dura. Es lo que el protocolo
  exige identico.
- **`binding_constraint`**: discrepancia dura tambien. Es el campo que los
  jueces leen por maquina para distinguir un refusal de seguridad de uno de
  pago; una decision que coincide con un binding distinto es una decision
  correcta por el motivo equivocado.
- **`reason`**: se reporta aparte, como aviso. Deberia coincidir (los numeros
  del reason salen de la misma aritmetica), pero una diferencia solo de prosa
  no incumple el requisito.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from api.schemas import CourierStateOverrides, DecideRequest
from core.agent.shocks import SHOCKS
from core.agent.strategy import STRATEGY
from core.evaluation.shift import ShiftConfig, ShiftRunner, ShiftState
from core.models import Offer

#: Ventana de entrega que se asume al reconstruir una oferta del log.
#: `order_offered` no lleva `time_window`, y ninguna decision depende de ella
#: (solo la metrica `deadline_misses`). Se usa una ventana amplia para no
#: inventar incumplimientos que el turno original no tuvo.
ASSUMED_WINDOW_MIN = 10_000.0

#: Como se pide una decision. Recibe el request y devuelve el dict de
#: respuesta. Hay dos: en proceso y contra el endpoint HTTP.
DecideFn = Callable[[DecideRequest], dict[str, Any]]


def load_log(path: str | Path) -> list[dict[str, Any]]:
    """Lee un event log JSONL. Ignora lineas vacias y no parseables."""
    eventos: list[dict[str, Any]] = []
    for linea in Path(path).read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            eventos.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return eventos


def _parse(moment: Any) -> datetime | None:
    if isinstance(moment, datetime):
        return moment.replace(tzinfo=None)
    if not isinstance(moment, str) or not moment.strip():
        return None
    try:
        return datetime.fromisoformat(moment.strip().rstrip("Zz")).replace(tzinfo=None)
    except ValueError:
        return None


@dataclass(frozen=True)
class Mismatch:
    order_id: str
    field: str
    recorded: Any
    replayed: Any

    @property
    def hard(self) -> bool:
        """¿Incumple el requisito del protocolo, o es solo prosa distinta?"""
        return self.field in ("decision", "binding_constraint")


@dataclass
class ReplayReport:
    seed: int | None = None
    orders: int = 0
    compared: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    #: Ofertas del log sin su evento `decision` correspondiente.
    unmatched: list[str] = field(default_factory=list)
    shocks_replayed: int = 0
    strategy_events: int = 0
    accepted: int = 0

    @property
    def hard_mismatches(self) -> list[Mismatch]:
        return [m for m in self.mismatches if m.hard]

    @property
    def identical(self) -> bool:
        """El criterio del protocolo: mismas decisiones sobre la misma entrada."""
        return not self.hard_mismatches and not self.unmatched and self.compared > 0

    def render(self) -> str:
        lineas = [
            f"  turno grabado : seed={self.seed}",
            f"  ofertas       : {self.orders}  ({self.accepted} aceptadas al reproducir)",
            f"  comparadas    : {self.compared}",
            f"  shocks        : {self.shocks_replayed} reinyectados",
            f"  strategy      : {self.strategy_events} reinyectados"
            + ("  (parametros clavados)" if self.strategy_events else "  (ninguno en el log)"),
        ]
        duras = self.hard_mismatches
        suaves = [m for m in self.mismatches if not m.hard]

        if duras:
            lineas.append(f"\n  DISCREPANCIAS DURAS: {len(duras)}")
            for m in duras[:20]:
                lineas.append(
                    f"    {m.order_id}  {m.field}: grabado={m.recorded!r} reproducido={m.replayed!r}"
                )
            if len(duras) > 20:
                lineas.append(f"    ... y {len(duras) - 20} mas")
        if suaves:
            lineas.append(f"\n  avisos (solo prosa): {len(suaves)}")
            for m in suaves[:3]:
                lineas.append(f"    {m.order_id}  {m.field}")
        if self.unmatched:
            lineas.append(
                f"\n  ofertas sin decision grabada: {len(self.unmatched)} "
                f"({', '.join(self.unmatched[:5])}...)"
            )
        return "\n".join(lineas)


def _config_from_shift_start(evento: dict[str, Any] | None) -> ShiftConfig:
    """Reconstruye la configuracion del turno desde `shift_start`.

    `shift_end_time` del log manda sobre `shift_hours` si ambos estan: es el
    campo que el schema marca como "read from state, never hardcoded".
    """
    evento = evento or {}
    inicio = _parse(evento.get("sim_time")) or datetime(2026, 1, 1, 8, 0)
    fin = _parse(evento.get("shift_end_time"))
    horas = float(evento.get("shift_hours") or 8.0)
    if fin is not None:
        horas = max(0.0, (fin - inicio).total_seconds() / 3600.0)

    return ShiftConfig(
        seed=int(evento.get("seed") or 0),
        shift_hours=horas,
        vehicle=str(evento.get("vehicle") or "moto"),
        start_location_zone=int(evento.get("start_location_zone") or 0),
        shift_start_time=inicio,
    )


def offer_from_event(evento: dict[str, Any], inicio: datetime) -> Offer:
    """`Offer` reconstruida desde un `order_offered` grabado.

    Solo se usa para avanzar el estado del repartidor al aceptar (tiempos,
    kilometros, mochila). Las coordenadas quedan en el origen porque el camino
    oficial habla de zonas enteras y no las consulta.
    """
    momento = _parse(evento.get("sim_time")) or inicio
    minuto = (momento - inicio).total_seconds() / 60.0
    return Offer(
        id=str(evento.get("order_id", "")),
        pickup=(0.0, 0.0),
        dropoff=(0.0, 0.0),
        pay=float(evento.get("base_pay_mxn") or 0.0),
        time_window=(minuto, minuto + ASSUMED_WINDOW_MIN),
        received_at=minuto,
        zone_pickup=evento.get("zone_pickup"),
        zone_dropoff=evento.get("zone_dropoff"),
        weight_kg=float(evento.get("weight_kg") or 0.0),
        volume_liters=float(evento.get("volume_liters") or 0.0),
        est_tip_mxn=float(evento.get("est_tip_mxn") or 0.0),
        surge_multiplier=float(evento.get("surge_multiplier") or 1.0),
        restaurant_prep_min=float(evento.get("restaurant_prep_min") or 0.0),
        platform=evento.get("platform"),
    )


def request_from_event(
    evento: dict[str, Any], overrides: CourierStateOverrides, vehicle: str
) -> DecideRequest:
    """`DecideRequest` con los numeros del log y el estado reconstruido.

    Las distancias salen del log, **no se recalculan**: son la entrada que el
    sistema vio entonces, y recalcularlas convertiria el replay en volver a
    correr el simulador.
    """
    return DecideRequest(
        order_id=str(evento.get("order_id", "")),
        platform=evento.get("platform"),
        sim_time=_parse(evento.get("sim_time")),
        zone_pickup=int(evento.get("zone_pickup") or 0),
        zone_dropoff=int(evento.get("zone_dropoff") or 0),
        distance_pickup_km=float(evento.get("distance_pickup_km") or 0.0),
        distance_delivery_km=float(evento.get("distance_delivery_km") or 0.0),
        base_pay_mxn=float(evento.get("base_pay_mxn") or 0.0),
        est_tip_mxn=float(evento.get("est_tip_mxn") or 0.0),
        surge_multiplier=float(evento.get("surge_multiplier") or 1.0),
        restaurant_prep_min=float(evento.get("restaurant_prep_min") or 0.0),
        weight_kg=evento.get("weight_kg"),
        volume_liters=evento.get("volume_liters"),
        vehicle=str(evento.get("vehicle") or vehicle),
        courier_state_overrides=overrides,
    )


def prime(eventos: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Reinyecta `strategy_update` y `shock` del log en sus capas.

    Devuelve (shocks, strategy_events). Reinyectar el strategy_update ademas
    **clava** la capa de estrategia: mientras dure el replay tier2 no publica
    nada, que es lo que hace que el diff mida nuestro determinismo y no el del
    modelo.
    """
    SHOCKS.reset()
    shocks = strategy = 0
    for evento in eventos:
        nombre = evento.get("event")
        if nombre == "shock":
            if SHOCKS.apply_recorded(evento) is not None:
                shocks += 1
        elif nombre == "strategy_update":
            STRATEGY.apply_recorded(evento)
            strategy += 1
    return shocks, strategy


def replay_log(
    eventos: list[dict[str, Any]],
    decide: DecideFn,
    *,
    prime_layers: bool = True,
) -> ReplayReport:
    """Reproduce el turno y difea las decisiones contra las grabadas.

    `decide` abstrae el transporte: en proceso o contra el endpoint HTTP. El
    diff es el mismo en los dos casos, que es lo que permite afirmar que el
    replay prueba el sistema que corre y no una copia.
    """
    inicio_evento = next((e for e in eventos if e.get("event") == "shift_start"), None)
    config = _config_from_shift_start(inicio_evento)
    grabadas = {
        str(e.get("order_id")): e for e in eventos if e.get("event") == "decision"
    }

    reporte = ReplayReport(seed=config.seed)
    if prime_layers:
        reporte.shocks_replayed, reporte.strategy_events = prime(eventos)

    runner = ShiftRunner(config)
    estado = ShiftState(
        config=config,
        position_zone=config.start_location_zone,
        busy_until=config.shift_start_time,
    )

    for evento in eventos:
        if evento.get("event") != "order_offered":
            continue

        momento = _parse(evento.get("sim_time")) or config.shift_start_time
        reporte.orders += 1

        runner.drain_delivered(estado, momento)
        runner.take_break_if_due(estado, momento)

        request = request_from_event(evento, estado.overrides(momento), config.vehicle)
        respuesta = decide(request)

        grabada = grabadas.get(request.order_id)
        if grabada is None:
            reporte.unmatched.append(request.order_id)
        else:
            reporte.compared += 1
            for campo in ("decision", "binding_constraint", "reason"):
                esperado = grabada.get(campo)
                obtenido = respuesta.get(campo)
                if esperado != obtenido:
                    reporte.mismatches.append(
                        Mismatch(request.order_id, campo, esperado, obtenido)
                    )

        if respuesta.get("decision") == "ACCEPT":
            reporte.accepted += 1
            runner.accept(request, offer_from_event(evento, config.shift_start_time), estado, momento)

    return reporte


def in_process_decide() -> DecideFn:
    """Transporte en proceso: llama a la misma funcion que usa el endpoint.

    Sirve para los tests y para reproducir sin levantar el servidor. El
    transporte HTTP vive en `scripts/replay.py`.
    """
    from api.decide import decide_sync

    return decide_sync
