"""Ensayo de la demo: guiones ejecutables contra el endpoint en vivo.

    "A constraint that exists in code but is never demonstrated triggering
     during your demo scores low. Rehearse at least two as live demo moments."
    -- evaluation_protocol.md, seccion 4

    "Answering from a decision log in under ten seconds is itself scored.
     Re-deriving the answer live is the wrong answer even when it turns out
     to be right."
    -- student-materials/courier/README.md

Por que esto es un script y no un documento
--------------------------------------------
Un guion escrito en Markdown se desincroniza del codigo en silencio: alguien
mueve `HEAT_RULE_MAX_CONTINUOUS_MIN` y el guion sigue prometiendo un refusal
que ya no ocurre. Aqui **cada escena declara el resultado que espera**, asi
que correr el ensayo es a la vez un ensayo y una prueba de regresion. Si algo
dejo de disparar, falla en la terminal y no en el escenario.

Uso
---
    # en una terminal
    cd backend && source .venv/bin/activate
    uvicorn main:app --port 8000

    # en otra
    python3 scripts/demo.py             # ensaya todas las escenas
    python3 scripts/demo.py --scene A   # una sola
    python3 scripts/demo.py --list

La escena E (caida del modelo) necesita que apagues la red a mano: el script
te dice cuando y espera. Es deliberado -- el protocolo dice "rehearse this
with the network off", y un mock no ensaya nada.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

BASE_URL = "http://localhost:8000"

# El presupuesto del protocolo. Cada escena lo verifica de paso.
LATENCY_BUDGET_MS = 50

VERDE, ROJO, GRIS, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"


# ==========================================================================
# Cliente HTTP minimo (stdlib: el ensayo no debe depender de nada instalable)
# ==========================================================================


def post_json(path: str, payload: dict | None, base_url: str, method: str = "POST") -> dict:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def limpiar_shocks(base_url: str) -> None:
    """Deja el registro de shocks vacio. Una escena que empieza con shocks de
    una corrida anterior no demuestra lo que dice demostrar."""
    post_json("/shocks", None, base_url, method="DELETE")


def inyectar_surge(zona: int, multiplicador: float, sim_time: str):
    def accion(base_url: str) -> None:
        limpiar_shocks(base_url)
        post_json(
            "/shock",
            {
                "event": "shock",
                "sim_time": sim_time,
                "shock_type": "surge",
                "zone": zona,
                "multiplier": multiplicador,
                "duration_min": 30,
            },
            base_url,
        )
        print(f"  {GRIS}POST /shock  surge {multiplicador}x en zona {zona}{FIN}")

    return accion


def post_decide(payload: dict, base_url: str) -> dict:
    request = urllib.request.Request(
        f"{base_url}/decide",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def get_json(path: str, base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as response:
        return json.loads(response.read().decode())


# ==========================================================================
# Ofertas de referencia
# ==========================================================================


def offer(**overrides) -> dict:
    """Oferta base. Cada escena cambia solo lo que quiere demostrar."""
    payload = {
        "order_id": "ORD-DEMO",
        "platform": "didi",
        "sim_time": "2026-03-21T19:00:00",
        "zone_pickup": 0,
        "zone_dropoff": 1,
        "distance_pickup_km": 1.0,
        "distance_delivery_km": 3.0,
        "base_pay_mxn": 150.0,
        "est_tip_mxn": 30.0,
        "surge_multiplier": 1.0,
        "restaurant_prep_min": 5,
        "weight_kg": 3.0,
        "volume_liters": 9.0,
        "vehicle": "moto",
    }
    payload.update(overrides)
    return payload


def mochila(peso_kg: float, volumen_l: float = 0.0) -> list[dict]:
    return [{"order_id": "ORD-ENCURSO", "weight_kg": peso_kg, "volume_liters": volumen_l}]


# ==========================================================================
# Escenas
# ==========================================================================


@dataclass
class Paso:
    """Un ping y lo que debe contestar."""

    titulo: str
    payload: dict
    espera_decision: str
    espera_binding: str | None
    #: Lo que el presentador dice mientras sale en pantalla.
    guion: str = ""
    #: Accion que se ejecuta ANTES del ping (inyectar un shock, limpiarlos).
    #: Recibe la base_url. Es lo que permite que una escena demuestre un
    #: efecto externo real en vez de simularlo cambiando el payload.
    antes: Callable[[str], None] | None = None


@dataclass
class Escena:
    clave: str
    titulo: str
    #: Que requisito del protocolo demuestra. Va en la matriz de trazabilidad.
    demuestra: str
    pasos: list[Paso] = field(default_factory=list)
    #: Escenas que no son una secuencia de pings (degradado, explain).
    especial: Callable[[str], bool] | None = None
    #: La pregunta de juez que esta escena contesta.
    pregunta: str = ""
    cierre: str = ""


def _escena_a() -> Escena:
    """La oferta mas jugosa del turno, rechazada por calor."""
    caliente = offer(
        order_id="ORD-DEMO-A",
        sim_time="2026-03-21T13:10:00",
        base_pay_mxn=210.0,
        est_tip_mxn=90.0,
        surge_multiplier=2.4,
        courier_state_overrides={
            "continuous_riding_min": 118.0,
            "shift_end_time": "2026-03-21T23:00:00",
        },
    )
    return Escena(
        clave="A",
        titulo="Seguridad sobre paga — la regla de calor",
        demuestra="constraint 3 (heat_rule) disparando en vivo; safety-over-pay invariance",
        pregunta="«¿Por qué saltaste ese pedido?»",
        pasos=[
            Paso(
                "La mejor oferta del turno: surge 2.4x, $210 + $90 de propina",
                caliente,
                "SKIP",
                "heat_rule",
                guion=(
                    "Son las 13:10 y el repartidor lleva 118 minutos seguidos sobre la moto. "
                    "Esta es la oferta que más paga en todo el turno. La rechazamos."
                ),
            ),
        ],
        cierre=(
            "El número del reason es el manejo continuo PROYECTADO, no el actual: "
            "la pregunta no es si ya te pasaste, es si aceptar esto te pasa. "
            "El límite vive en core/agent/safety.py, sección LIMITES."
        ),
    )


def _escena_b() -> Escena:
    """Un solo campo cambia y la decision se invierte."""
    base = offer(
        order_id="ORD-DEMO-B1",
        courier_state_overrides={"shift_end_time": "2026-03-21T23:00:00"},
    )
    lleno = offer(
        order_id="ORD-DEMO-B2",
        courier_state_overrides={
            "shift_end_time": "2026-03-21T23:00:00",
            "in_flight_orders": mochila(13.0, 30.0),
        },
    )
    return Escena(
        clave="B",
        titulo="«¿Qué pasa si cambio este input?» — capacidad acumulada",
        demuestra="constraint 5 (vehicle_capacity) disparando en vivo; capacidad sobre la mochila",
        pregunta="«¿Qué pasa si cambio este input?»",
        pasos=[
            Paso(
                "Mochila vacía: el mismo pedido de 3 kg",
                base,
                "ACCEPT",
                None,
                guion="Tres kilos en una moto que aguanta 15. Entra sin problema.",
            ),
            Paso(
                "Mochila con 13 kg ya encima: el MISMO pedido",
                lleno,
                "SKIP",
                "vehicle_capacity",
                guion=(
                    "No cambié el pedido. Cambié lo que el repartidor ya trae cargando. "
                    "Tres kilos son inofensivos solos e imposibles sobre trece."
                ),
            ),
        ],
        cierre=(
            "El límite es acumulado sobre la mochila, no por pedido suelto: "
            "es lo que importa cuando el repartidor va a estar cargando las dos cosas a la vez."
        ),
    )


def _escena_c() -> Escena:
    """La frontera de las 22:00 se evalua contra la LLEGADA."""
    temprano = offer(
        order_id="ORD-DEMO-C1",
        sim_time="2026-03-21T21:15:00",
        zone_dropoff=2,
        courier_state_overrides={"shift_end_time": "2026-03-22T02:00:00"},
    )
    tarde = offer(
        order_id="ORD-DEMO-C2",
        sim_time="2026-03-21T21:50:00",
        zone_dropoff=2,
        courier_state_overrides={"shift_end_time": "2026-03-22T02:00:00"},
    )
    return Escena(
        clave="C",
        titulo="La frontera de las 22:00 — se mide la llegada, no el ping",
        demuestra="constraint 1 (flagged_zone_night); threshold consistency",
        pregunta="«¿Y si el pedido entra justo antes del corte?»",
        pasos=[
            Paso(
                "21:15, entrega en zona marcada: llega antes del toque de queda",
                temprano,
                "ACCEPT",
                None,
                guion="Nueve y cuarto. La entrega cae antes de las diez. Se acepta.",
            ),
            Paso(
                "21:50, MISMA zona: la llegada ya cruza las 22:00",
                tarde,
                "SKIP",
                "flagged_zone_night",
                guion=(
                    "Treinta y cinco minutos después. El pedido entra antes de las diez, "
                    "pero la entrega no. Y la regla habla de dónde termina el repartidor."
                ),
            ),
        ],
        cierre=(
            "Si midiéramos la hora del ping, la regla se burlaría sola: aceptaríamos a las 21:50 "
            "algo que deja al repartidor en la zona marcada a las 22:06."
        ),
    )


def _escena_d(base_url: str) -> bool:
    """Responder desde el log en menos de 10 segundos."""
    _titulo("D", "Respuesta desde la bitácora en menos de 10 segundos")
    print(f"{GRIS}  demuestra: explain_decision con los números del momento de decidir{FIN}")
    print(f"{GRIS}  pregunta:  «¿Por qué saltaste ese pedido?» / «¿Y si arreglo esa constraint?»{FIN}\n")

    t0 = time.perf_counter()
    try:
        payload = get_json("/explain_decision/ORD-DEMO-A", base_url)
    except urllib.error.HTTPError:
        print(f"  {ROJO}FALLA{FIN}  no hay decisión registrada: corre antes la escena A\n")
        return False
    transcurrido = time.perf_counter() - t0

    print(f"  GET /explain_decision/ORD-DEMO-A   {VERDE}{transcurrido * 1000:.0f} ms{FIN}")
    print(f"\n  decision: {NEGRITA}{payload['decision']}{FIN}")
    print(f"  reason:   {payload['reason']}")
    print("\n  alternatives_considered:")
    for alternativa in payload["alternatives_considered"]:
        print(f"    · {NEGRITA}{alternativa['option']}{FIN}")
        print(f"        {alternativa['rejected_because']}")

    entradas = payload["inputs"]
    print("\n  inputs (extracto):")
    print(f"    manejo continuo efectivo : {entradas['courier_state']['effective_continuous_riding_min']:.0f} min")
    print(f"    tiempo hasta completar   : {entradas['time_to_completion_min']:.1f} min")
    print(f"    límite de calor vigente  : {entradas['active_limits']['heat_rule_max_continuous_min']:.0f} min")
    print(f"    latencia de la decisión  : {entradas['latency_ms']:.3f} ms")

    ok = transcurrido < 10.0 and len(payload["alternatives_considered"]) >= 2
    _veredicto(
        ok,
        f"contestado en {transcurrido:.2f} s con {len(payload['alternatives_considered'])} alternativas",
    )
    print(
        f"\n  {GRIS}Lo segundo de la lista contesta «¿y si arreglo el descanso?» sin re-correr nada:{FIN}"
        f"\n  {GRIS}el journal guardó TODAS las constraints que violaban, no solo la que mandó.{FIN}\n"
    )
    return ok


def _escena_e(base_url: str) -> bool:
    """Caida del modelo, con la red apagada de verdad."""
    _titulo("E", "Caída del modelo — modo degradado")
    print(f"{GRIS}  demuestra: protocolo sección 7 (fast path sigue decidiendo, y lo señala){FIN}")
    print(f"{GRIS}  pregunta:  «¿Qué hace cuando se equivoca?» / «¿Y si se cae el modelo?»{FIN}\n")

    estado = get_json("/status", base_url)
    print(f"  estado inicial: degraded={estado['degraded']}  advisor={estado['advisor']}  "
          f"salario de reserva ${estado['reservation_wage_mxn_hr']:.0f}/hr")

    if estado["advisor"] != "claude":
        # Sin credencial al arrancar, main.py no conecta tier2 a proposito:
        # "no hay modelo configurado" no es lo mismo que "el modelo se cayo", y
        # arrancar degradado destruye justo la transicion que hay que demostrar.
        print(f"\n  {ROJO}No se puede ensayar: tier2 no esta conectado (advisor={estado['advisor']}).{FIN}")
        print("  El servidor tiene que arrancar CON credencial para que haya un modelo que caer:")
        print(f"    {GRIS}export ANTHROPIC_API_KEY=...  &&  uvicorn main:app --port 8000{FIN}")
        _veredicto(False, "escena no ensayada")
        return False

    if estado["degraded"]:
        print(f"\n  {ROJO}Ya arranca degradado: la transicion sano->degradado no se puede mostrar.{FIN}")
        _veredicto(False, "revisa la credencial antes de ensayar")
        return False

    print(f"\n  {NEGRITA}>>> APAGA LA RED AHORA (wifi off) y pulsa Enter <<<{FIN}")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print(f"  {GRIS}sin terminal interactiva: se omite la escena E{FIN}\n")
        return True

    # Dos pings separados en tiempo de SIMULACION: el segundo dispara el
    # refresco de tier2, que ya no puede alcanzar al modelo.
    print("\n  ping con sim_time avanzado 30 min -> tier2 intenta refrescar...")
    post_decide(offer(order_id="ORD-DEMO-E1", sim_time="2026-03-21T19:00:00"), base_url)
    post_decide(offer(order_id="ORD-DEMO-E2", sim_time="2026-03-21T19:40:00"), base_url)
    time.sleep(2.0)

    estado = get_json("/status", base_url)
    respuesta = post_decide(offer(order_id="ORD-DEMO-E3", sim_time="2026-03-21T20:20:00"), base_url)

    print(f"\n  GET /status     degraded={NEGRITA}{estado['degraded']}{FIN}  "
          f"fallos={estado['consecutive_model_failures']}")
    print(f"                  {estado['last_model_error']}")
    print(f"  salario de reserva CONSERVADO: ${estado['reservation_wage_mxn_hr']:.0f}/hr")
    print(f"\n  POST /decide    {respuesta['decision']}  degraded={NEGRITA}{respuesta['degraded']}{FIN}  "
          f"{respuesta['latency_ms']:.3f} ms")

    ok = (
        estado["degraded"] is True
        and respuesta["degraded"] is True
        and respuesta["latency_ms"] < LATENCY_BUDGET_MS
    )
    _veredicto(ok, "sigue decidiendo dentro del presupuesto, y lo dice")
    print(f"\n  {GRIS}Ninguna oferta se encoló esperando al modelo: el fast path no lo conoce.{FIN}")
    print(f"  {NEGRITA}>>> VUELVE A ENCENDER LA RED y pulsa Enter para ver la recuperación <<<{FIN}")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        return ok

    post_decide(offer(order_id="ORD-DEMO-E4", sim_time="2026-03-21T21:00:00"), base_url)
    time.sleep(6.0)
    estado = get_json("/status", base_url)
    print(f"\n  GET /status     degraded={NEGRITA}{estado['degraded']}{FIN}  "
          f"revisión {estado['strategy_revision']}  fuente {estado['strategy_source']}")
    print(f"  {GRIS}(si sigue en true, tier2 aún no alcanzó a contestar: espera unos segundos más){FIN}\n")
    return ok


def _escena_f() -> Escena:
    """Un surge inyectado EN VIVO cambia la decision. El shock que el brief exige."""
    pedido = offer(
        order_id="ORD-DEMO-F1",
        zone_dropoff=3,
        base_pay_mxn=110.0,
        est_tip_mxn=0.0,
        surge_multiplier=1.0,
        distance_pickup_km=3.0,
        distance_delivery_km=6.0,
        restaurant_prep_min=6,
        courier_state_overrides={"shift_end_time": "2026-03-21T23:00:00"},
    )
    con_shock = dict(pedido, order_id="ORD-DEMO-F2")

    return Escena(
        clave="F",
        titulo="Entra un surge en vivo — el shock de media demo",
        demuestra="protocolo seccion 5: shock inyectado por el juez, con efecto medible",
        pregunta="«¿Que haria si ahora mismo entra un surge?»",
        pasos=[
            Paso(
                "El pedido, sin shocks activos",
                pedido,
                "SKIP",
                "reservation_wage",
                guion="Nueve kilometros por ciento diez pesos. Hoy no sale.",
                antes=limpiar_shocks,
            ),
            Paso(
                "Se inyecta el surge por POST /shock. MISMO pedido.",
                con_shock,
                "ACCEPT",
                None,
                guion=(
                    "No toco el pedido. Mando un shock al endpoint, igual que lo "
                    "mandarian ustedes, con el mismo formato del evento del log."
                ),
                antes=inyectar_surge(zona=3, multiplicador=2.2, sim_time="2026-03-21T18:55:00"),
            ),
        ],
        cierre=(
            "El body del shock es el mismo objeto que el evento `shock` del event log: "
            "una linea copiada de un log entra tal cual. El reason lo anota entre corchetes, "
            "y el binding_constraint pasa de 'reservation_wage' a null -- la maquina distingue "
            "un rechazo por dinero de uno por seguridad sin leer la prosa."
        ),
    )


# ==========================================================================
# Presentacion
# ==========================================================================


def _titulo(clave: str, titulo: str) -> None:
    print(f"\n{NEGRITA}{'─' * 78}{FIN}")
    print(f"{NEGRITA}  ESCENA {clave} — {titulo}{FIN}")
    print(f"{NEGRITA}{'─' * 78}{FIN}")


def _veredicto(ok: bool, detalle: str) -> None:
    marca = f"{VERDE}✔ ENSAYO OK{FIN}" if ok else f"{ROJO}✘ ENSAYO FALLA{FIN}"
    print(f"\n  {marca}  {detalle}")


def _correr_escena(escena: Escena, base_url: str) -> bool:
    _titulo(escena.clave, escena.titulo)
    print(f"{GRIS}  demuestra: {escena.demuestra}{FIN}")
    if escena.pregunta:
        print(f"{GRIS}  pregunta:  {escena.pregunta}{FIN}")

    todo_ok = True
    for paso in escena.pasos:
        print(f"\n  {NEGRITA}{paso.titulo}{FIN}")
        if paso.guion:
            print(f'  {GRIS}"{paso.guion}"{FIN}')
        if paso.antes is not None:
            paso.antes(base_url)

        respuesta = post_decide(paso.payload, base_url)
        decision = respuesta["decision"]
        binding = respuesta.get("binding_constraint")
        latencia = respuesta["latency_ms"]

        ok = (
            decision == paso.espera_decision
            and binding == paso.espera_binding
            and latencia < LATENCY_BUDGET_MS
        )
        todo_ok = todo_ok and ok

        color = VERDE if ok else ROJO
        print(f"    -> {color}{NEGRITA}{decision}{FIN}   binding={binding}   "
              f"{latencia:.3f} ms   tier={respuesta.get('tier')}   degraded={respuesta.get('degraded')}")
        print(f"    {respuesta['reason']}")

        economia = respuesta.get("economics")
        if economia:
            print(
                f"    {GRIS}bruto ${economia['gross_pay_mxn']:.0f} − combustible "
                f"${economia['fuel_cost_mxn']:.0f} = neto ${economia['net_pay_mxn']:.0f}  |  "
                f"{economia['total_km']:.1f} km  |  ${economia['adjusted_rate_mxn_hr']:.0f}/hr "
                f"vs mínimo ${economia['reservation_wage_mxn_hr']:.0f}/hr{FIN}"
            )
        if not ok:
            print(f"    {ROJO}esperaba {paso.espera_decision} / {paso.espera_binding}{FIN}")

    if escena.cierre:
        print(f"\n  {GRIS}{escena.cierre}{FIN}")
    _veredicto(todo_ok, f"{len(escena.pasos)} paso(s)")
    return todo_ok


ESCENAS_PING = {e.clave: e for e in (_escena_a(), _escena_b(), _escena_c(), _escena_f())}
ESCENAS_ESPECIALES = {"D": _escena_d, "E": _escena_e}
ORDEN = ("A", "B", "C", "D", "F", "E")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", action="append", help="A, B, C, D, E o F (repetible)")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--list", action="store_true", help="lista las escenas y sale")
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="omite la escena E (la que pide apagar la red)",
    )
    args = parser.parse_args()

    if args.list:
        for clave in ORDEN:
            escena = ESCENAS_PING.get(clave)
            titulo = escena.titulo if escena else {"D": "Respuesta desde la bitácora", "E": "Caída del modelo"}[clave]
            print(f"  {clave}  {titulo}")
        return 0

    claves = args.scene or [c for c in ORDEN if not (args.no_interactive and c == "E")]

    try:
        get_json("/status", args.base_url)
    except Exception as exc:
        print(f"{ROJO}No hay servidor en {args.base_url}: {exc}{FIN}")
        print("Arráncalo con:  cd backend && source .venv/bin/activate && uvicorn main:app --port 8000")
        return 2

    resultados: dict[str, bool] = {}
    for clave in claves:
        clave = clave.upper()
        if clave in ESCENAS_PING:
            resultados[clave] = _correr_escena(ESCENAS_PING[clave], args.base_url)
        elif clave in ESCENAS_ESPECIALES:
            resultados[clave] = ESCENAS_ESPECIALES[clave](args.base_url)
        else:
            print(f"{ROJO}escena desconocida: {clave}{FIN}")
            return 2

    print(f"\n{NEGRITA}{'═' * 78}{FIN}")
    for clave, ok in resultados.items():
        print(f"  escena {clave}: {VERDE + 'OK' + FIN if ok else ROJO + 'FALLA' + FIN}")
    fallidas = [c for c, ok in resultados.items() if not ok]
    print(f"{NEGRITA}{'═' * 78}{FIN}\n")
    return 1 if fallidas else 0


if __name__ == "__main__":
    sys.exit(main())
