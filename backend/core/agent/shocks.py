"""Shocks de media corrida: lo que los jueces inyectan en vivo.

    "At least one shock during the demo is required by the brief. Judges may
     inject shocks live, using the `shock` event format in
     event_log_schema.json: `surge`, `closure`, `rain`, `delay`.
     Your system must react without stalling the decision loop."
    -- student-materials/courier/evaluation_protocol.md, seccion 5

Las dos mitades de ese requisito tiran en direcciones opuestas y por eso este
modulo existe: **reaccionar** significa que un shock tiene que cambiar
decisiones posteriores, y **sin estancar el loop** significa que leerlos no
puede bloquear ni tomar un lock dentro de la ventana de 50 ms. La solucion es
la misma que ya usa `strategy.py`: una tupla inmutable que se REEMPLAZA entera
al inyectar, y que el fast path lee como atributo.

    tier1 (ventana de 50 ms)          POST /shock (hilo del request)
    ------------------------          ------------------------------
    SHOCKS.effects(...)  --lectura--> _shocks : tuple[Shock, ...]
      sin lock                          ^
      sin red                           | reemplazo atomico de la tupla
      pliegue sobre <10 elementos       |

Que cambia cada tipo de shock, y por que ese y no otro
-------------------------------------------------------
Cada efecto se eligio para que sea **observable en una decision** y defendible
en una frase. Un shock que no mueve ningun numero no se puede demostrar en
vivo, y "lo registramos" no es reaccionar.

| tipo | efecto | por que |
|---|---|---|
| `surge` | sube el multiplicador de la zona | es lo que la plataforma paga de mas; sube la tasa efectiva y desbloquea ofertas que estaban bajo el salario de reserva |
| `closure` | alarga distancia en la zona afectada | un cierre no borra el pedido, te obliga a rodear: mas km, mas combustible, mas tiempo |
| `rain` | baja la velocidad efectiva (global) | lluvia no cambia la geografia, cambia cuanto tardas en recorrerla |
| `delay` | suma minutos de preparacion a UN pedido | es el "what if this order's restaurant is running 15 minutes late?" que el material lista entre las preguntas escritas de los jueces |

Donde entra cada efecto, y la invariante que NO se rompe
---------------------------------------------------------
`surge` alimenta solo a la economia. `closure`, `rain` y `delay` alimentan al
TIEMPO y a la DISTANCIA, y por lo tanto tambien al gate de seguridad: una
tormenta puede empujar una entrega mas alla del fin de turno y hacerla
`shift_end_infeasible`, o pasarse del tope de calor. Eso es deseado.

Lo que sigue siendo cierto es que **`safety.py` nunca ve el pago**: los
efectos viajan como numeros de tiempo y distancia, no como dinero, y
`ShockEffects` no tiene un solo campo en MXN que el gate pueda mirar. La
invariante *Safety-over-pay* no depende de este modulo y este modulo no puede
debilitarla: un surge de 3x no puede convertir un refusal de seguridad en
ACCEPT porque el gate no recibe el multiplicador.

Determinismo y replay
---------------------
Un shock es una entrada EXTERNA, igual que una oferta: no se inventa aqui y no
se lee del reloj de pared. Su ventana de vigencia se mide en `sim_time`, asi
que reinyectar los eventos `shock` de un log grabado reproduce exactamente las
mismas decisiones (protocolo, seccion 6) -- para eso esta `apply_recorded`.
Un shock sin `sim_time` se trata como vigente siempre: es la lectura honesta
cuando no hay contra que medir la ventana, y no introduce un reloj.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

ShockType = Literal["surge", "closure", "rain", "delay"]

#: Los cuatro tipos del contrato oficial. No inventar valores nuevos: el
#: evento `shock` del event log los valida contra esta lista.
SHOCK_TYPES: frozenset[str] = frozenset({"surge", "closure", "rain", "delay"})


# ==========================================================================
# LIMITES  --  cuanto mueve cada shock. Igual que en safety.py: juntos,
# arriba, con nombre, para que "abre el archivo donde esta el numero" se
# conteste señalando esta seccion y para poder editarlos en vivo si un juez
# pregunta "¿que pasa si muevo esto?".
# ==========================================================================

#: Cuanto dura un shock si el juez no dice. El ejemplo oficial usa 25 y 45
#: minutos; 30 esta en medio y es lo bastante largo para que se vea en varias
#: ofertas seguidas durante una demo.
DEFAULT_SHOCK_DURATION_MIN = 30.0

#: Un `delay` no es un estado del mundo que pasa, es un hecho sobre UN pedido:
#: si el restaurante va tarde, sigue yendo tarde cuando ese pedido se decida.
#: Por eso su ventana por defecto es larga en vez de los 30 minutos genericos.
DELAY_DEFAULT_DURATION_MIN = 120.0

#: Tope del multiplicador de surge que se acepta inyectar. Misma disciplina
#: que el recorte del salario de reserva en strategy.py: una entrada externa
#: absurda (100x) queda recortada y el recorte se registra, en vez de volver
#: rentable cualquier pedido y dejar la demo sin sentido.
MAX_SURGE_MULTIPLIER = 3.0
MIN_SURGE_MULTIPLIER = 1.0

#: Un cierre obliga a rodear. 1.35 = 35% mas de kilometraje en la zona
#: afectada. No es infinito a proposito: un cierre hace el pedido caro, no
#: imposible -- modelarlo como imposible seria inventar una constraint de
#: seguridad que el protocolo no lista.
CLOSURE_DETOUR_FACTOR = 1.35

#: Lluvia: se circula al 75% de la velocidad del perfil. Afecta el tiempo, no
#: la distancia -- llover no mueve las calles.
RAIN_SPEED_FACTOR = 0.75


@dataclass(frozen=True)
class Shock:
    """Un shock inyectado. Espeja el evento `shock` del contrato oficial."""

    shock_type: str
    #: Momento de simulacion en que empieza. None = vigente siempre (ver
    #: nota de modulo).
    sim_time: datetime | None = None
    zone: int | None = None          # surge y closure
    multiplier: float | None = None  # surge
    road: str | None = None          # closure
    order_id: str | None = None      # delay
    slip_min: float | None = None    # delay
    duration_min: float = DEFAULT_SHOCK_DURATION_MIN

    def active_at(self, moment: datetime | None) -> bool:
        """¿Sigue vigente en `moment`?

        Ventana semiabierta `[sim_time, sim_time + duration_min)`: un shock de
        25 minutos que arranca 18:20 ya no aplica a las 18:45 en punto.
        """
        if self.sim_time is None or moment is None:
            return True
        if moment < self.sim_time:
            return False
        return moment < self.sim_time + timedelta(minutes=max(0.0, self.duration_min))

    def expires_at(self) -> datetime | None:
        if self.sim_time is None:
            return None
        return self.sim_time + timedelta(minutes=max(0.0, self.duration_min))

    def to_event(self) -> dict[str, Any]:
        """Evento `shock` del event log.

        Requeridos por validate_format.py: event, sim_time, shock_type. Los
        opcionales solo se emiten si aplican al tipo, para no ensuciar el log
        con `multiplier: null` en un evento de lluvia.
        """
        event: dict[str, Any] = {
            "event": "shock",
            "sim_time": self.sim_time.isoformat() if self.sim_time else None,
            "shock_type": self.shock_type,
            "duration_min": self.duration_min,
        }
        if self.zone is not None:
            event["zone"] = self.zone
        if self.multiplier is not None:
            event["multiplier"] = self.multiplier
        if self.road is not None:
            event["road"] = self.road
        if self.order_id is not None:
            event["order_id"] = self.order_id
        if self.slip_min is not None:
            event["slip_min"] = self.slip_min
        return event

    def describe(self) -> str:
        """Etiqueta corta para el `reason` que un juez oye en voz alta."""
        if self.shock_type == "surge":
            return f"surge {self.multiplier:.1f}x zona {self.zone}"
        if self.shock_type == "closure":
            return f"cierre zona {self.zone}"
        if self.shock_type == "delay":
            return f"retraso {self.slip_min:.0f} min"
        return "lluvia"


@dataclass(frozen=True)
class ShockEffects:
    """Lo que los shocks vigentes le hacen a UNA oferta concreta.

    Deliberadamente sin un solo campo en MXN: lo unico economico que sale de
    aqui es `surge_multiplier`, que es un multiplicador de tarifa y lo consume
    la capa de economia, nunca el gate de seguridad (ver nota de modulo).
    """

    #: Multiplicador de surge a aplicar (>= el que ya traia el request).
    surge_multiplier: float = 1.0
    #: Minutos extra de preparacion del restaurante (delay).
    extra_prep_min: float = 0.0
    #: Factor sobre los km del pedido (closure).
    distance_factor: float = 1.0
    #: Factor sobre la velocidad del vehiculo (rain). <1 = mas lento.
    speed_factor: float = 1.0
    #: Descripciones cortas de los shocks que efectivamente mordieron.
    applied: tuple[str, ...] = ()

    @property
    def any_applied(self) -> bool:
        return bool(self.applied)


def _clamp_surge(value: float) -> tuple[float, bool]:
    """Recorta a las cotas. Devuelve (valor, se_recorto)."""
    clamped = max(MIN_SURGE_MULTIPLIER, min(MAX_SURGE_MULTIPLIER, value))
    return clamped, clamped != value


def _default_duration(shock_type: str) -> float:
    return DELAY_DEFAULT_DURATION_MIN if shock_type == "delay" else DEFAULT_SHOCK_DURATION_MIN


class ShockRegistry:
    """Shocks vigentes del proceso. Escritura con lock, lectura sin el.

    El lock protege la INYECCION (dos jueces pinchando a la vez) y la
    contabilidad del historial. La lectura del fast path no lo toma: lee la
    tupla como atributo, igual que `StrategyLayer.snapshot()`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Unico atributo que lee el fast path. Se REEMPLAZA, nunca se muta.
        self._shocks: tuple[Shock, ...] = ()
        self._history: list[Shock] = []

    # -- escritura (hilo del request de /shock) ----------------------------

    def inject(self, shock: Shock) -> Shock:
        """Registra un shock y lo devuelve ya normalizado (surge recortado).

        Nunca lanza por un multiplicador absurdo: lo recorta. Un 500 aqui
        seria un fallo duro igual que en la ventana de decision, y ademas
        dejaria al juez sin saber si su inyeccion entro.
        """
        if shock.multiplier is not None:
            value, _ = _clamp_surge(float(shock.multiplier))
            shock = Shock(**{**shock.__dict__, "multiplier": value})

        with self._lock:
            self._history.append(shock)
            self._shocks = self._shocks + (shock,)
        return shock

    def apply_recorded(self, event: dict[str, Any]) -> Shock | None:
        """Reinyecta un evento `shock` grabado. None si el evento no es uno.

        Es la contraparte de `StrategyLayer.apply_recorded`: al reproducir un
        turno, los shocks vuelven a entrar desde el log en vez de depender de
        que alguien los pinche otra vez a mano en el mismo minuto.
        """
        if event.get("event") != "shock":
            return None
        shock = shock_from_payload(event)
        return self.inject(shock) if shock else None

    def clear(self) -> int:
        """Borra los shocks vigentes (no el historial). Devuelve cuantos eran.

        Existe para el ensayo: entre dos pasadas de la demo hay que poder
        volver al estado limpio sin reiniciar el proceso.
        """
        with self._lock:
            count = len(self._shocks)
            self._shocks = ()
        return count

    def reset(self) -> None:
        """Borra vigentes E historial. Estado de proceso recien arrancado.

        Distinto de `clear()` a proposito: `clear()` es para el ensayo (vuelve
        al estado limpio conservando el registro de lo que se inyecto), y esto
        es para empezar de cero -- un turno nuevo, o un test que no puede
        heredar los shocks del anterior.
        """
        with self._lock:
            self._shocks = ()
            self._history.clear()

    # -- lectura (dentro de la ventana de decision) ------------------------

    def active(self, moment: datetime | None) -> tuple[Shock, ...]:
        """Shocks vigentes en `moment`. Sin lock: pliegue sobre una tupla."""
        return tuple(s for s in self._shocks if s.active_at(moment))

    def all_shocks(self) -> tuple[Shock, ...]:
        return self._shocks

    def history(self) -> tuple[Shock, ...]:
        with self._lock:
            return tuple(self._history)

    def __len__(self) -> int:
        return len(self._shocks)

    def effects(
        self,
        sim_time: datetime | None,
        *,
        zone_pickup: int | None = None,
        zone_dropoff: int | None = None,
        order_id: str | None = None,
    ) -> ShockEffects:
        """Efecto combinado de los shocks vigentes sobre UNA oferta.

        Reglas de combinacion, escritas porque son justo lo que un juez
        pregunta cuando inyecta dos shocks seguidos:

        - **El surge no se apila: se toma el mayor.** Dos surges en la misma
          zona son el mismo evento visto dos veces, no 1.6x * 1.6x. Apilar
          multiplicadores vuelve rentable cualquier cosa con dos clics.
        - **Los cierres si se componen**, porque rodear dos zonas cerradas es
          efectivamente dos desvios.
        - **La lluvia es global y no se apila**: dos avisos de lluvia siguen
          siendo una lluvia.
        - **Un `delay` solo toca al pedido que nombra.** Un shock de retraso
          sin `order_id` no puede atribuirse a nadie y se ignora en el
          calculo (queda en el historial, pero no mueve numeros).
        """
        surge = 1.0
        extra_prep = 0.0
        distance_factor = 1.0
        speed_factor = 1.0
        applied: list[str] = []

        for shock in self.active(sim_time):
            if shock.shock_type == "surge":
                if shock.zone is not None and shock.zone not in (zone_pickup, zone_dropoff):
                    continue
                value = float(shock.multiplier or 1.0)
                if value <= surge:
                    continue
                surge = value
                applied.append(shock.describe())

            elif shock.shock_type == "closure":
                if shock.zone is not None and shock.zone not in (zone_pickup, zone_dropoff):
                    continue
                distance_factor *= CLOSURE_DETOUR_FACTOR
                applied.append(shock.describe())

            elif shock.shock_type == "rain":
                if speed_factor <= RAIN_SPEED_FACTOR:
                    continue
                speed_factor = RAIN_SPEED_FACTOR
                applied.append(shock.describe())

            elif shock.shock_type == "delay":
                if not shock.order_id or shock.order_id != order_id:
                    continue
                extra_prep += float(shock.slip_min or 0.0)
                applied.append(shock.describe())

        return ShockEffects(
            surge_multiplier=surge,
            extra_prep_min=extra_prep,
            distance_factor=distance_factor,
            speed_factor=speed_factor,
            applied=tuple(applied),
        )


def shock_from_payload(payload: dict[str, Any]) -> Shock | None:
    """Construye un `Shock` desde un dict crudo. None si el tipo no es valido.

    Total salvo en el tipo: tolera campos extra, ausentes y basura, igual que
    `/decide` con su body. Lo unico que se rechaza es un `shock_type` fuera
    del enum oficial -- inventar uno nuevo haria que el evento del log fallara
    el validador, y ese si es un error que conviene devolver en la cara.
    """
    shock_type = payload.get("shock_type")
    if shock_type not in SHOCK_TYPES:
        return None

    duration = _coerce_float(payload.get("duration_min"))
    return Shock(
        shock_type=str(shock_type),
        sim_time=_coerce_datetime(payload.get("sim_time")),
        zone=_coerce_int(payload.get("zone")),
        multiplier=_coerce_float(payload.get("multiplier")),
        road=str(payload["road"]) if payload.get("road") else None,
        order_id=str(payload["order_id"]) if payload.get("order_id") else None,
        slip_min=_coerce_float(payload.get("slip_min")),
        duration_min=duration if duration is not None else _default_duration(str(shock_type)),
    )


def _coerce_float(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _coerce_int(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _coerce_datetime(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip().rstrip("Zz")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


# ==========================================================================
# Instancia compartida del proceso, igual que JOURNAL y STRATEGY.
#
# POST /shock escribe aqui y POST /decide lee de aqui. Si cada uno construyera
# la suya, el shock que el juez inyecta en vivo no cambiaria ninguna decision
# -- que es exactamente el requisito que se esta demostrando.
# ==========================================================================

SHOCKS = ShockRegistry()
