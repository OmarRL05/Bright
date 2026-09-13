"""Capa de estrategia (tier2) y modo degradado.

    "When the model is unreachable the fast path keeps deciding on the last
     known strategy, within budget, and signals that it is degraded. A silent
     fallback is partial credit; a stall or crash is a hard failure."
    -- student-materials/courier/README.md

    "Judges may disable your model connection mid-shift - typically by
     invalidating the API credential in the process environment - then restore
     it. ... No order is held or queued waiting for the model. The strategy
     layer recovers when connectivity returns. ... Rehearse this with the
     network off."
    -- evaluation_protocol.md, seccion 7

Arquitectura en una frase
-------------------------
El fast path **lee** parametros; nunca los **pide**. Tier2 los publica cuando
puede, y cuando no puede, lo que ya estaba publicado sigue sirviendo.

    tier1 (ventana de 50 ms)          tier2 (hilo aparte, entre pings)
    ------------------------          --------------------------------
    STRATEGY.snapshot()  --lectura--> _params : StrategyParams
      sin lock                          ^
      sin red                           | reemplazo atomico del objeto
      sin esperas                       |
                                      ClaudeAdvisor.propose()  (puede fallar)

Cuatro invariantes, y como cada una esta garantizada
----------------------------------------------------

**1. El fast path nunca se bloquea.** `snapshot()` devuelve una referencia a un
objeto inmutable y no toma el lock: en CPython leer un atributo es atomico, asi
que o se ve la version vieja o se ve la nueva, nunca una a medias. No hay
camino de codigo por el que una decision espere a la red.

**2. Ningun pedido se encola esperando al modelo.** El fast path no conoce al
advisor: no puede pedirle nada aunque quisiera. `maybe_refresh()` *despacha* a
un worker y regresa de inmediato, y con un solo vuelo en curso a la vez para
que las llamadas no se apilen.

**3. El fallo no es silencioso.** Una caida marca `degraded=True`, que viaja en
la respuesta de /decide, en el evento `strategy_update` del event log y en
`status()`. El protocolo da credito parcial a un fallback silencioso; esto no
lo es.

**4. Un modelo alucinado no puede romper al agente.** Toda propuesta se
recorta a `[MIN_RESERVATION_WAGE_MXN_HR, MAX_RESERVATION_WAGE_MXN_HR]` antes de
publicarse. Tier2 aconseja; no manda.

Sobre la credencial: hay que leerla en cada llamada
----------------------------------------------------
Los jueces invalidan la credencial **en el entorno del proceso, a media
corrida**. Un `anthropic.Anthropic()` construido al importar el modulo se queda
con la key vieja en memoria y seguiria funcionando: el ensayo saldria bien y la
demo real fallaria. Por eso `ClaudeAdvisor` construye el cliente **dentro de
cada llamada**, leyendo `os.environ` en ese momento. Es la diferencia entre
haber implementado el requisito y haberlo simulado.

Replay
------
El protocolo permite que los parametros de tier2 varien por no-determinismo del
modelo, siempre que ninguna decision del fast path cambie por eso. Para el diff
de replay exacto se usa `apply_recorded()`, que fija los parametros desde los
eventos `strategy_update` del log grabado en vez de volver a llamar al modelo.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Protocol

from core.agent import reasons

# ==========================================================================
# LIMITES de la capa de estrategia.
#
# Las cotas del salario de reserva no son afinado fino: son el cinturon de
# seguridad contra una propuesta absurda del modelo. Un tier2 que proponga
# $5/hr o $5000/hr queda recortado y el recorte se registra.
# ==========================================================================

#: Punto de partida antes de que el modelo opine nunca. Tambien es el valor al
#: que se vuelve si nadie logra proponer nada en todo el turno.
#:
#: Calibrado sobre TUNING_SEEDS con el arnes de evaluacion (ver
#: docs/Bloque 3/RESULTADOS.md). Parece alto para un repartidor real, y lo es:
#: el cuello de botella del turno es el TIEMPO, no la oferta. Con ~200 ofertas
#: en 8 horas y media hora por entrega, solo caben unas 25; ser selectivo gana
#: mas dinero Y completa mas pedidos que aceptar todo lo razonable.
#:
#: Bajo de 400 a 275 al ampliar el ZoneMap de 4 a 16 zonas (Persona 1): el mapa
#: nuevo es mas disperso, los trayectos son mas largos y la misma oferta rinde
#: menos MXN/hr, asi que un umbral de 400 rechazaba casi todo (la tasa de
#: aceptacion se cayo a 5.5%). Es el ejemplo de por que la calibracion no
#: sobrevive a un cambio del mundo simulado: hay que rehacer el barrido.
#:
#: El valor sale de `scripts/calibrate.py`, que hace el barrido de forma
#: reproducible y aplica el criterio del peor vecino en vez del pico -- no de
#: una corrida a mano que nadie pueda repetir.
DEFAULT_RESERVATION_WAGE_MXN_HR = 275.0

MIN_RESERVATION_WAGE_MXN_HR = 60.0
MAX_RESERVATION_WAGE_MXN_HR = 600.0

#: Cada cuanto tiempo **de simulacion** se vuelve a consultar al modelo. En
#: minutos de sim, no de reloj de pared: asi la cadencia de refresco es la
#: misma en una corrida en vivo y en un replay acelerado.
REFRESH_INTERVAL_SIM_MIN = 20.0

#: Techo duro de la llamada al modelo. Corre en un hilo de fondo, pero un hilo
#: colgado para siempre es una fuga: si no contesta en este tiempo, se trata
#: como caida y se entra en degradado.
MODEL_TIMEOUT_SECONDS = 15.0

#: Modelo de la capa de estrategia. Nunca se llama dentro de la ventana de
#: decision -- ver el docstring del modulo.
MODEL_ID = "claude-opus-5"

Confidence = Literal["low", "medium", "high"]
ParamsSource = Literal["bootstrap", "model", "recorded"]


# ==========================================================================
# Datos
# ==========================================================================


@dataclass(frozen=True)
class StrategyParams:
    """Parametros vigentes. Inmutable: se reemplaza entero, no se muta."""

    reservation_wage_mxn_hr: float = DEFAULT_RESERVATION_WAGE_MXN_HR
    target_zone: int | None = None
    reasoning: str = "arranque: salario de reserva por defecto, sin consultar al modelo"
    confidence: Confidence = "low"
    #: True cuando se esta operando sobre una estrategia vieja porque el
    #: modelo no responde. Viaja hasta la respuesta de /decide.
    degraded: bool = False
    #: Sube en cada publicacion. Permite ver en el log cuantas veces cambio la
    #: estrategia durante el turno.
    revision: int = 0
    source: ParamsSource = "bootstrap"
    #: sim_time de la ultima publicacion (no reloj de pared).
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ModelProposal:
    """Lo que tier2 propone. Todavia sin recortar a las cotas."""

    reservation_wage_mxn_hr: float
    target_zone: int | None = None
    reasoning: str = ""
    confidence: Confidence = "medium"


@dataclass(frozen=True)
class StrategyStatus:
    """Salud de la capa. Alimenta el status endpoint del protocolo (seccion 7)."""

    degraded: bool
    revision: int
    consecutive_failures: int
    last_error: str | None
    last_success_sim_time: str | None
    refresh_in_flight: bool
    advisor: str


class ModelAdvisor(Protocol):
    #: False cuando no hay ningun modelo detras. Un advisor no disponible no
    #: se consulta, y por lo tanto **no puede degradar la capa**: "no hay
    #: modelo configurado" y "el modelo se cayo" son estados distintos y el
    #: protocolo solo puntua el segundo. Quien no declare el atributo se
    #: asume disponible.
    available: bool

    def propose(self, context: dict[str, Any]) -> ModelProposal:
        """Propone parametros. Lanza si el modelo no esta disponible."""
        ...


# ==========================================================================
# Advisors
# ==========================================================================


class NullAdvisor:
    """Advisor que nunca propone nada. Es el default.

    Con este advisor el sistema corre entero sin tocar la red, y `degraded`
    queda en False porque no hay modelo que se haya caido: no hay estrategia
    vieja, hay la de arranque. Distinguir "sin modelo configurado" de "el
    modelo se cayo" importa, porque lo segundo es lo que el protocolo puntua.
    """

    name = "null"
    available = False

    def propose(self, context: dict[str, Any]) -> ModelProposal:
        raise ModelUnavailable("no hay advisor configurado")


class ModelUnavailable(RuntimeError):
    """El modelo no esta disponible: sin key, sin red, timeout o respuesta ilegible."""


class ClaudeAdvisor:
    """Tier2 real contra la API de Claude, fuera de la ventana de decision.

    Tres decisiones que importan para el ensayo del protocolo (seccion 7):

    1. **El cliente se construye en cada llamada**, leyendo `os.environ` en ese
       momento. Un cliente cacheado al importar sobreviviria a que los jueces
       invaliden la credencial y el requisito quedaria sin demostrar.
    2. **`max_retries=0` y timeout corto.** En background los reintentos serian
       gratis, pero alargan el hueco entre la caida y la señal de degradado --
       y esa señal es justo lo que se esta evaluando.
    3. **El SDK se importa perezosamente.** Si `anthropic` no esta instalado en
       la maquina de la demo, eso se comporta igual que un modelo inalcanzable:
       degradado, no crash.
    """

    name = "claude"
    available = True

    def __init__(
        self,
        model: str = MODEL_ID,
        timeout_seconds: float = MODEL_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    _SYSTEM = (
        "Eres la capa de estrategia de un agente repartidor. Ajustas el salario "
        "de reserva (MXN/hora) que el motor de decision usa para aceptar o "
        "rechazar pedidos. Nunca decides pedidos individuales.\n"
        "Responde SOLO con un objeto JSON, sin texto alrededor, con las claves: "
        '{"reservation_wage_mxn_hr": number, "target_zone": integer|null, '
        '"reasoning": string de menos de 30 palabras, '
        '"confidence": "low"|"medium"|"high"}'
    )

    def propose(self, context: dict[str, Any]) -> ModelProposal:
        # La credencial primero: es lo que los jueces invalidan, y revisarla
        # antes de importar nada mantiene el diagnostico exacto aunque el SDK
        # tampoco este instalado.
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ModelUnavailable("ANTHROPIC_API_KEY ausente o vacia en el entorno")

        try:
            import anthropic
        except ImportError as exc:  # el SDK no esta instalado: mismo efecto que sin red
            raise ModelUnavailable(f"SDK anthropic no disponible: {exc}") from exc

        try:
            client = anthropic.Anthropic(
                api_key=api_key,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self._SYSTEM,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": json.dumps(context, default=str)}],
            )
        except Exception as exc:
            # Credencial invalida (401), sin red, timeout, sobrecarga: para esta
            # capa son el mismo evento -- "el modelo no esta" -- y se tratan
            # igual. El detalle exacto se conserva en status().
            raise ModelUnavailable(f"{type(exc).__name__}: {exc}") from exc

        return _parse_proposal(response)


def _parse_proposal(response: Any) -> ModelProposal:
    """Extrae la propuesta del response. Una respuesta ilegible es una caida."""
    text = "".join(
        block.text for block in getattr(response, "content", []) if getattr(block, "type", "") == "text"
    ).strip()

    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ModelUnavailable(f"respuesta del modelo ilegible: {exc}") from exc

    if not isinstance(data, dict) or "reservation_wage_mxn_hr" not in data:
        raise ModelUnavailable("respuesta del modelo sin reservation_wage_mxn_hr")

    try:
        wage = float(data["reservation_wage_mxn_hr"])
    except (TypeError, ValueError) as exc:
        raise ModelUnavailable(f"reservation_wage_mxn_hr no numerico: {exc}") from exc

    zone = data.get("target_zone")
    confidence = data.get("confidence")

    return ModelProposal(
        reservation_wage_mxn_hr=wage,
        target_zone=int(zone) if isinstance(zone, (int, float)) and not isinstance(zone, bool) else None,
        reasoning=str(data.get("reasoning") or ""),
        confidence=confidence if confidence in ("low", "medium", "high") else "medium",
    )


# ==========================================================================
# La capa
# ==========================================================================


class StrategyLayer:
    def __init__(
        self,
        advisor: ModelAdvisor | None = None,
        *,
        refresh_interval_sim_min: float = REFRESH_INTERVAL_SIM_MIN,
    ) -> None:
        self._advisor: ModelAdvisor = advisor or NullAdvisor()
        self._refresh_interval = refresh_interval_sim_min

        # Unico atributo que lee el fast path. Se REEMPLAZA, nunca se muta.
        self._params = StrategyParams()

        self._lock = threading.Lock()  # protege solo la contabilidad, no la lectura
        self._in_flight = False
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._last_success_sim_time: datetime | None = None
        self._last_refresh_sim_time: datetime | None = None
        self._worker: threading.Thread | None = None

    # -- lectura del fast path --------------------------------------------

    def snapshot(self) -> StrategyParams:
        """Parametros vigentes. Sin lock, sin red, sin esperas.

        Es lo unico que el fast path llama de este modulo, y es una lectura de
        atributo: no puede bloquear ni fallar.
        """
        return self._params

    @property
    def degraded(self) -> bool:
        return self._params.degraded

    def status(self) -> StrategyStatus:
        """Salud de la capa, para un status endpoint o para el dashboard."""
        with self._lock:
            return StrategyStatus(
                degraded=self._params.degraded,
                revision=self._params.revision,
                consecutive_failures=self._consecutive_failures,
                last_error=self._last_error,
                last_success_sim_time=(
                    self._last_success_sim_time.isoformat() if self._last_success_sim_time else None
                ),
                refresh_in_flight=self._in_flight,
                advisor=getattr(self._advisor, "name", type(self._advisor).__name__),
            )

    # -- refresco (entre pings, nunca dentro) ------------------------------

    def maybe_refresh(self, sim_time: datetime | None, context: dict[str, Any]) -> bool:
        """Dispara un refresco si toca. **No bloquea**: despacha y regresa.

        Devuelve True si se despacho una llamada. Se salta si ya hay una en
        vuelo (un solo vuelo a la vez: sin esto, un modelo lento acumularia
        llamadas y cada una publicaria parametros mas viejos que la anterior).
        """
        if not getattr(self._advisor, "available", True):
            # Sin modelo detras no hay nada que preguntar, y sobre todo no hay
            # nada que pueda fallar: dispararlo igual marcaria `degraded` por
            # una caida que no ocurrio (ver NullAdvisor).
            return False

        if sim_time is not None and self._last_refresh_sim_time is not None:
            elapsed = (sim_time - self._last_refresh_sim_time).total_seconds() / 60.0
            if elapsed < self._refresh_interval:
                return False

        with self._lock:
            if self._in_flight:
                return False
            self._in_flight = True
            self._last_refresh_sim_time = sim_time

        worker = threading.Thread(
            target=self._run_refresh,
            args=(sim_time, context),
            name="strategy-refresh",
            daemon=True,  # nunca impide que el proceso termine
        )
        self._worker = worker
        worker.start()
        return True

    def refresh_now(self, sim_time: datetime | None, context: dict[str, Any]) -> StrategyParams:
        """Version sincrona de `maybe_refresh`, ignorando el intervalo.

        Para tests y para el ensayo en vivo del modo degradado: permite
        provocar la caida y ver el flag cambiar sin esperar a que pase el
        intervalo de simulacion.

        Respeta la misma regla que `maybe_refresh`: un advisor no disponible no
        se consulta, asi que no puede degradar la capa.
        """
        if not getattr(self._advisor, "available", True):
            return self._params
        with self._lock:
            self._in_flight = True
        self._run_refresh(sim_time, context)
        return self._params

    def _run_refresh(self, sim_time: datetime | None, context: dict[str, Any]) -> None:
        try:
            proposal = self._advisor.propose(context)
        except Exception as exc:
            self._on_failure(exc)
        else:
            self._on_success(proposal, sim_time)
        finally:
            with self._lock:
                self._in_flight = False

    def _on_success(self, proposal: ModelProposal, sim_time: datetime | None) -> None:
        wage, clamped = _clamp_wage(proposal.reservation_wage_mxn_hr)

        reasoning = reasons.cap_words(proposal.reasoning or "estrategia actualizada por el modelo")
        if clamped:
            reasoning = reasons.cap_words(
                f"{reasoning} (propuesta {proposal.reservation_wage_mxn_hr:.0f} recortada a {wage:.0f})"
            )

        with self._lock:
            self._consecutive_failures = 0
            self._last_error = None
            self._last_success_sim_time = sim_time

        self._publish(
            replace(
                self._params,
                reservation_wage_mxn_hr=wage,
                target_zone=proposal.target_zone,
                reasoning=reasoning,
                confidence=proposal.confidence,
                degraded=False,  # recuperado: el protocolo exige que vuelva solo
                source="model",
                updated_at=sim_time,
            )
        )

    def _on_failure(self, exc: Exception) -> None:
        """El modelo no esta. Se conservan los ultimos parametros buenos.

        No se toca `reservation_wage_mxn_hr`: operar sobre la ultima estrategia
        conocida es exactamente lo que pide el protocolo. Lo unico que cambia
        es que ahora se dice en voz alta.
        """
        with self._lock:
            self._consecutive_failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            fallos = self._consecutive_failures

        if self._params.degraded:
            return  # ya estaba degradado: no hay nada nuevo que publicar

        self._publish(
            replace(
                self._params,
                degraded=True,
                confidence="low",
                reasoning=reasons.cap_words(
                    f"modelo inalcanzable ({fallos} intento(s)): se sigue decidiendo con "
                    f"la ultima estrategia conocida, ${self._params.reservation_wage_mxn_hr:.0f}/hr"
                ),
            )
        )

    def _publish(self, params: StrategyParams) -> None:
        """Publicacion atomica: una sola asignacion de atributo.

        El fast path que lea justo aqui vera o el objeto viejo completo o el
        nuevo completo. No existe un instante con parametros a medio aplicar.
        """
        self._params = replace(params, revision=self._params.revision + 1)

    # -- replay -------------------------------------------------------------

    def apply_recorded(self, event: dict[str, Any]) -> StrategyParams:
        """Fija los parametros desde un evento `strategy_update` grabado.

        Es la respuesta al check de replay del protocolo (seccion 6): al
        reproducir un turno no se vuelve a llamar al modelo, se reinyecta lo
        que el modelo dijo entonces. Asi el no-determinismo del modelo no puede
        mover ni una decision del fast path.
        """
        wage, _ = _clamp_wage(float(event.get("reservation_wage_mxn_hr", DEFAULT_RESERVATION_WAGE_MXN_HR)))
        zone = event.get("target_zone")
        confidence = event.get("confidence")

        self._publish(
            replace(
                self._params,
                reservation_wage_mxn_hr=wage,
                target_zone=int(zone) if isinstance(zone, (int, float)) and not isinstance(zone, bool) else None,
                reasoning=reasons.cap_words(str(event.get("reasoning") or "parametros reinyectados del log")),
                confidence=confidence if confidence in ("low", "medium", "high") else "low",
                degraded=bool(event.get("degraded", False)),
                source="recorded",
            )
        )
        return self._params

    # -- event log ---------------------------------------------------------

    def to_strategy_update_event(self, sim_time: datetime | None = None) -> dict[str, Any]:
        """Evento `strategy_update` del event log.

        Requeridos por validate_format.py: event, sim_time,
        reservation_wage_mxn_hr.
        """
        params = self._params
        moment = sim_time or params.updated_at
        return {
            "event": "strategy_update",
            "sim_time": moment.isoformat() if moment else None,
            "reservation_wage_mxn_hr": params.reservation_wage_mxn_hr,
            "target_zone": params.target_zone,
            "reasoning": params.reasoning,
            "confidence": params.confidence,
            "degraded": params.degraded,
            "revision": params.revision,
            "source": params.source,
        }

    def use_advisor(self, advisor: ModelAdvisor) -> None:
        """Conecta (o cambia) el advisor de tier2.

        Cambiarlo no toca los parametros vigentes: el fast path sigue leyendo
        lo ultimo publicado mientras el advisor nuevo todavia no propone nada.
        """
        self._advisor = advisor

    def join(self, timeout: float | None = None) -> None:
        """Espera al worker en vuelo. Solo para tests y apagado ordenado."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)


def _clamp_wage(value: float) -> tuple[float, bool]:
    """Recorta a las cotas. Devuelve (valor, se_recorto)."""
    clamped = max(MIN_RESERVATION_WAGE_MXN_HR, min(MAX_RESERVATION_WAGE_MXN_HR, value))
    return clamped, clamped != value


# ==========================================================================
# Instancia compartida del proceso, igual que JOURNAL.
#
# Arranca con NullAdvisor: el sistema entero corre sin red y sin key. Para
# activar tier2 de verdad: STRATEGY.use_advisor(ClaudeAdvisor()) en el arranque.
# ==========================================================================

STRATEGY = StrategyLayer()
