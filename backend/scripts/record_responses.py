"""Graba una tanda de respuestas de /decide y las valida con el validador oficial.

    python3 scripts/record_responses.py                    # en proceso, sin red
    python3 scripts/record_responses.py --validate         # ...y corre el validador
    python3 scripts/record_responses.py --endpoint http://localhost:8000/decide
    python3 scripts/record_responses.py --seed 101 --shift-orders 60

Por que existe este script
---------------------------
`validate_format.py` tiene tres banderas y el equipo solo corria dos:

    --event-log    un turno grabado        (cubierto: run_evaluation.py --event-log)
    --endpoint     UN ping de forma        (cubierto: se corre a mano)
    --responses    una TANDA de respuestas (esto)

La diferencia entre `--endpoint` y `--responses` no es de volumen, es de
cobertura. `--endpoint` manda el PROBE -- una sola oferta bien formada, sin
estado -- y comprueba la forma de la respuesta feliz. `--responses` valida un
lote entero, asi que es la unica de las tres que puede demostrar que los
**refusals** tambien estan bien formados: que el `reason` de una violacion de
capacidad no se pasa de 40 palabras, que el `binding_constraint` de cada
constraint esta en el enum, y que la latencia se mantiene en presupuesto
cuando el gate si tiene trabajo que hacer.

Que se graba, y por que esas tres fuentes
------------------------------------------
1. **El PROBE oficial**, literal, copiado de `validate_format.py`. Si el lote
   no incluye lo mismo que el validador manda, el lote no prueba lo que el
   validador prueba.
2. **Sondas de frontera**, una por cada constraint del protocolo (seccion 4)
   mas los dos caminos economicos. Son las que meten refusals en el lote: sin
   ellas, un turno normal puede no disparar `mandatory_break` ni una sola vez
   y el archivo validaria sin haber ejercitado nada.
3. **Un turno real del arnes**, tal como lo ve `OurAgent`. Es el trafico
   honesto: estados encadenados, mochila que se llena, ventana de turno que se
   acaba. Las sondas prueban los bordes; esto prueba el medio.

Sin red por defecto
--------------------
El modo por defecto habla con la app EN PROCESO (`TestClient`), sin abrir un
socket. Es deliberado: el protocolo exige poder reproducir una corrida con la
conectividad apagada (seccion 7), asi que la herramienta que graba la evidencia
no puede necesitar red para funcionar. `--endpoint` existe para grabar contra
un servidor de verdad cuando se quiere medir tambien el round trip HTTP.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

REPO = BACKEND.parent
VALIDATOR = REPO / "student-materials" / "courier" / "validate_format.py"

from core.evaluation.policies import OurAgent  # noqa: E402
from core.evaluation.shift import ShiftConfig, ShiftRunner  # noqa: E402
from core.simulation.engine import DEFAULT_SHIFT_START_TIME  # noqa: E402

#: Copiado literal de `validate_format.py`. Si el validador cambia su PROBE,
#: esta constante tiene que seguirlo -- por eso se copia entera y no se
#: reconstruye campo por campo.
OFFICIAL_PROBE = {
    "order_id": "FORMAT-PROBE-001",
    "platform": "rappi",
    "sim_time": "2026-03-21T18:42:00",
    "zone_pickup": 7,
    "zone_dropoff": 11,
    "distance_pickup_km": 1.4,
    "distance_delivery_km": 6.5,
    "base_pay_mxn": 58.0,
    "est_tip_mxn": 12.0,
    "surge_multiplier": 1.3,
    "restaurant_prep_min": 9,
    "weight_kg": 2.1,
    "volume_liters": 6.0,
    "vehicle": "moto",
}


def _probe(order_id: str, sim_time: datetime, **campos) -> dict:
    """Oferta bien formada a la que se le mueve solo lo que la sonda prueba."""
    payload = {
        "order_id": order_id,
        "platform": "rappi",
        "sim_time": sim_time.isoformat(),
        "zone_pickup": 0,
        "zone_dropoff": 1,
        "distance_pickup_km": 1.5,
        "distance_delivery_km": 4.0,
        "base_pay_mxn": 120.0,
        "est_tip_mxn": 15.0,
        "surge_multiplier": 1.0,
        "restaurant_prep_min": 6.0,
        "weight_kg": 2.0,
        "volume_liters": 6.0,
        "vehicle": "moto",
    }
    payload.update(campos)
    return payload


def boundary_probes() -> list[dict]:
    """Una sonda por constraint del protocolo (seccion 4) y por camino economico.

    El objetivo de estas no es que ACEPTEN: es que el lote grabado contenga al
    menos un refusal de cada tipo, para que `--responses` valide la forma de
    los `reason` y los `binding_constraint` que un turno tranquilo podria no
    producir nunca.
    """
    mediodia = datetime(2026, 3, 21, 13, 0)
    noche = datetime(2026, 3, 21, 21, 50)
    tarde = datetime(2026, 3, 21, 18, 42)

    return [
        # --- Constraint 1: zona marcada de noche --------------------------
        # Zona 2 (Centro) es la unica marcada en el ZoneMap por defecto. Se
        # pinga a las 21:50 con un trayecto largo: la llegada cae despues de
        # las 22:00, que es contra lo que la regla se evalua.
        _probe("PROBE-FLAGGED-ZONE", noche, zone_dropoff=2, distance_delivery_km=9.0),
        # --- Constraint 2: pausa obligatoria ------------------------------
        _probe(
            "PROBE-MANDATORY-BREAK",
            tarde,
            courier_state_overrides={"continuous_riding_min": 245.0},
        ),
        # --- Constraint 3: regla de calor ---------------------------------
        # Dentro de 12:00-16:00, con 80 min continuos: el pedido proyecta por
        # encima del tope de 90.
        _probe(
            "PROBE-HEAT-RULE",
            mediodia,
            distance_delivery_km=8.0,
            courier_state_overrides={"continuous_riding_min": 80.0},
        ),
        # --- Constraint 4: fin de turno -----------------------------------
        _probe(
            "PROBE-SHIFT-END",
            tarde,
            distance_delivery_km=10.0,
            courier_state_overrides={
                "shift_end_time": (tarde + timedelta(minutes=10)).isoformat()
            },
        ),
        # --- Constraint 5: capacidad --------------------------------------
        # Peso solo: 40 kg en una moto de 15.
        _probe("PROBE-CAPACITY-WEIGHT", tarde, weight_kg=40.0, volume_liters=5.0),
        # Y capacidad ACUMULADA: el pedido cabe solo, no con la mochila llena.
        _probe(
            "PROBE-CAPACITY-STACKED",
            tarde,
            weight_kg=3.0,
            courier_state_overrides={
                "in_flight_orders": [
                    {"order_id": "IN-FLIGHT-1", "weight_kg": 13.0, "volume_liters": 30.0}
                ]
            },
        ),
        # --- Camino economico: rechazo ------------------------------------
        _probe("PROBE-LOW-PAY", tarde, base_pay_mxn=35.0, est_tip_mxn=0.0, distance_pickup_km=6.0),
        # --- Camino economico: aceptacion ---------------------------------
        _probe("PROBE-GOOD-PAY", tarde, base_pay_mxn=220.0, est_tip_mxn=40.0, distance_pickup_km=0.4),
        # --- Stacking: en vuelo que empuja la cola ------------------------
        _probe(
            "PROBE-STACKED-ROUTE",
            tarde,
            courier_state_overrides={
                "shift_end_time": (tarde + timedelta(hours=3)).isoformat(),
                "in_flight_orders": [
                    {
                        "order_id": "IN-FLIGHT-2",
                        "weight_kg": 1.0,
                        "volume_liters": 3.0,
                        "eta_dropoff": (tarde + timedelta(minutes=35)).isoformat(),
                    }
                ],
            },
        ),
        # --- Los tres vehiculos -------------------------------------------
        # El protocolo exige perfiles distintos; el lote tiene que mostrarlo.
        _probe("PROBE-VEHICLE-CAR", tarde, vehicle="car", weight_kg=20.0, volume_liters=80.0),
        _probe("PROBE-VEHICLE-BIKE", tarde, vehicle="bike", weight_kg=5.0, volume_liters=15.0),
        # --- Datos faltantes ----------------------------------------------
        # `weight_kg`/`volume_liters` son opcionales en el schema: una
        # constraint sin datos no dispara, y el endpoint responde igual.
        _probe("PROBE-NO-WEIGHT", tarde, weight_kg=None, volume_liters=None),
    ]


class _RecordingAgent(OurAgent):
    """`OurAgent` que ademas guarda los requests que vio.

    Envolver la politica en vez de reconstruir el loop garantiza que lo
    grabado sean los requests EXACTOS que el arnes evalua -- que es el punto:
    grabar un lote parecido al real no prueba nada del real.
    """

    name = "RecordingAgent"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list = []

    def decide(self, request, runner, state, sim_time):
        self.requests.append(request)
        return super().decide(request, runner, state, sim_time)


def shift_probes(seed: int, limit: int, vehicle: str, shift_hours: float) -> list[dict]:
    """Requests de un turno real del arnes, en orden cronologico."""
    config = ShiftConfig(seed=seed, shift_hours=shift_hours, vehicle=vehicle, start_location_zone=0)
    agent = _RecordingAgent()
    ShiftRunner(config).run(agent)

    payloads = []
    for request in agent.requests[:limit]:
        payload = json.loads(request.model_dump_json())
        # `model_dump_json` serializa None explicitos; quitarlos deja el
        # payload como el de un juez que simplemente no manda ese campo.
        payloads.append({k: v for k, v in payload.items() if v is not None})
    return payloads


# ==========================================================================
# Transporte: en proceso (sin red) o contra un endpoint real
# ==========================================================================


def post_in_process(payloads: list[dict]) -> list[dict]:
    """Habla con la app sin abrir un socket. Es el modo por defecto."""
    from fastapi.testclient import TestClient

    from main import app

    respuestas = []
    with TestClient(app) as client:
        for payload in payloads:
            response = client.post("/decide", json=payload)
            response.raise_for_status()
            respuestas.append(response.json())
    return respuestas


def post_http(payloads: list[dict], endpoint: str) -> list[dict]:
    """Graba contra un servidor de verdad. Stdlib: sin dependencias nuevas."""
    import urllib.request

    respuestas = []
    for payload in payloads:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            respuestas.append(json.loads(response.read().decode()))
    return respuestas


# ==========================================================================
# Informe
# ==========================================================================


def coverage(respuestas: list[dict]) -> dict[str, int]:
    """Cuantas veces aparecio cada `binding_constraint` en el lote.

    Es el numero que hace util este archivo mas alla de pasar el validador:
    una constraint con 0 aqui es una constraint que el lote no ejercito, y el
    protocolo penaliza explicitamente una constraint que existe en el codigo
    pero nunca se ve disparar.
    """
    conteo: dict[str, int] = {}
    for r in respuestas:
        clave = r.get("binding_constraint") or "(ninguna: decidio el pago)"
        conteo[clave] = conteo.get(clave, 0) + 1
    return conteo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="out/responses.json")
    parser.add_argument(
        "--endpoint",
        help="graba contra un servidor corriendo (por defecto: en proceso, sin red)",
    )
    parser.add_argument("--seed", type=int, default=101, help="seed del turno que aporta trafico real")
    parser.add_argument("--shift-orders", type=int, default=40, help="cuantos requests del turno grabar")
    parser.add_argument("--vehicle", default="moto", choices=["moto", "car", "bike"])
    parser.add_argument("--shift-hours", type=float, default=8.0)
    parser.add_argument("--validate", action="store_true", help="corre validate_format.py al terminar")
    args = parser.parse_args()

    payloads = [
        OFFICIAL_PROBE,
        *boundary_probes(),
        *shift_probes(args.seed, args.shift_orders, args.vehicle, args.shift_hours),
    ]

    print(f"\nGrabando {len(payloads)} requests")
    print(f"  1 PROBE oficial + {len(boundary_probes())} sondas de frontera + turno seed={args.seed}")
    print(f"  transporte: {args.endpoint if args.endpoint else 'en proceso (sin red)'}")

    if args.endpoint:
        respuestas = post_http(payloads, args.endpoint)
    else:
        respuestas = post_in_process(payloads)

    destino = Path(args.out)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(respuestas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    aceptadas = sum(1 for r in respuestas if r.get("decision") == "ACCEPT")
    latencias = [r["latency_ms"] for r in respuestas if isinstance(r.get("latency_ms"), (int, float))]
    peor = max(latencias) if latencias else 0.0

    print(f"\n{len(respuestas)} respuestas escritas en {destino}")
    print(f"  {aceptadas} ACCEPT / {len(respuestas) - aceptadas} SKIP")
    print(f"  latencia maxima reportada: {peor:.2f} ms (presupuesto: 50 ms)")
    print("\n  binding_constraint:")
    for clave, veces in sorted(coverage(respuestas).items()):
        print(f"    {clave:<32} {veces}")

    if not args.validate:
        print(f"\nValidar con:\n  python3 {VALIDATOR.relative_to(REPO)} --responses {destino}")
        return 0

    proceso = subprocess.run(
        [sys.executable, str(VALIDATOR), "--responses", str(destino)],
        capture_output=True,
        text=True,
    )
    print(proceso.stdout, end="")
    if proceso.stderr:
        print(proceso.stderr, end="", file=sys.stderr)
    return proceso.returncode


if __name__ == "__main__":
    raise SystemExit(main())
