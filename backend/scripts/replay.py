"""Reproduce un turno grabado y difea las decisiones contra las del log.

    python3 scripts/replay.py --seed 101                  # en proceso
    python3 scripts/replay.py --log logs/shift.jsonl      # un archivo cualquiera
    python3 scripts/replay.py --seed 101 --endpoint http://localhost:8000

Sale con codigo 1 si alguna decision difiere: se puede meter en el build.

    "Judges may ask you to record a complete shift to an event log, replay
     that log against your running system, and diff the decisions."
    -- evaluation_protocol.md, seccion 6

Los dos transportes, y por que hay dos
---------------------------------------
**En proceso** (por defecto) llama a `api.decide.decide_request`, exactamente
la misma funcion que sirve el endpoint. Es rapido y no necesita servidor, asi
que corre en los tests.

**`--endpoint`** habla HTTP contra el servidor levantado. Es mas lento y es el
que vale delante de un juez: prueba el proceso que esta corriendo, con su
estado acumulado y su capa de estrategia viva, no una instancia limpia creada
para la ocasion. Antes de empezar le pide al servidor que reinyecte los
`strategy_update` y `shock` del log y que **clave** los parametros; al
terminar lo devuelve a modo vivo.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.evaluation.replay import (  # noqa: E402
    ReplayReport,
    load_log,
    replay_log,
)

VERDE, ROJO, GRIS, NEGRITA, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"

DEFAULT_LOG_DIR = BACKEND / "logs"


def _http_post(url: str, payload: dict | None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def http_decide(base_url: str):
    """Transporte HTTP: cada decision es un POST /decide de verdad."""

    def decide(request) -> dict:
        return _http_post(f"{base_url}/decide", json.loads(request.model_dump_json()))

    return decide


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, help="turno grabado en logs/replay_seed_N.jsonl")
    parser.add_argument("--log", help="ruta explicita a un event log JSONL")
    parser.add_argument(
        "--endpoint",
        help="reproducir contra un servidor vivo (ej. http://localhost:8000)",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="graba el turno antes de reproducirlo (necesita --seed)",
    )
    args = parser.parse_args()

    if not args.log and args.seed is None:
        parser.error("da --seed o --log")

    ruta = Path(args.log) if args.log else DEFAULT_LOG_DIR / f"replay_seed_{args.seed}.jsonl"

    if args.record:
        if args.seed is None:
            parser.error("--record necesita --seed")
        from core.evaluation.policies import OurAgent
        from core.evaluation.shift import ShiftConfig, ShiftRunner

        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("w", encoding="utf-8") as handle:
            ShiftRunner(ShiftConfig(seed=args.seed), log_file=handle).run(OurAgent())
        print(f"{GRIS}turno seed={args.seed} grabado en {ruta}{FIN}")

    if not ruta.exists():
        print(f"{ROJO}no existe {ruta}{FIN}")
        print("Grabalo con:  python3 scripts/replay.py --seed N --record")
        return 2

    eventos = load_log(ruta)
    print(f"\n{NEGRITA}{'─' * 70}{FIN}")
    print(f"{NEGRITA}  REPLAY  {ruta.name}  ({len(eventos)} eventos){FIN}")
    print(f"{GRIS}  transporte: {'HTTP ' + args.endpoint if args.endpoint else 'en proceso'}{FIN}")
    print(f"{NEGRITA}{'─' * 70}{FIN}")

    if args.endpoint:
        reporte = _replay_http(eventos, ruta, args)
    else:
        from core.evaluation.replay import in_process_decide

        reporte = replay_log(eventos, in_process_decide())

    print(reporte.render())
    return _veredicto(reporte)


def _replay_http(eventos: list[dict], ruta: Path, args) -> ReplayReport:
    """Reproduce contra el servidor vivo, cebandolo y devolviendolo a vivo."""
    base = args.endpoint.rstrip("/")
    cebado = _http_post(f"{base}/replay/prime", {"events": eventos})
    print(
        f"{GRIS}  cebado: {cebado.get('shocks', 0)} shocks, "
        f"{cebado.get('strategy_events', 0)} strategy_update, "
        f"parametros clavados={cebado.get('pinned')}{FIN}"
    )
    try:
        # `prime_layers=False`: ya lo hizo el servidor, y hacerlo tambien aqui
        # tocaria las capas de ESTE proceso, que no son las que deciden.
        reporte = replay_log(eventos, http_decide(base), prime_layers=False)
        reporte.shocks_replayed = int(cebado.get("shocks", 0))
        reporte.strategy_events = int(cebado.get("strategy_events", 0))
        return reporte
    finally:
        _http_post(f"{base}/replay/resume", None)
        print(f"{GRIS}  servidor devuelto a modo vivo{FIN}")


def _veredicto(reporte: ReplayReport) -> int:
    print()
    if reporte.identical:
        print(
            f"  {VERDE}{NEGRITA}✔ IDENTICO{FIN}  "
            f"{reporte.compared} decisiones reproducidas sin una sola diferencia"
        )
        print(
            f"  {GRIS}Es el criterio del protocolo seccion 6: mismas decisiones "
            f"del fast path sobre la misma entrada.{FIN}\n"
        )
        return 0

    print(f"  {ROJO}{NEGRITA}✘ HAY DIFERENCIAS{FIN}")
    print(
        f"  {GRIS}El protocolo dice que una discrepancia apunta a estado mutable "
        f"oculto,\n  una dependencia del reloj de pared, o una carrera entre capas "
        f"-- y que\n  van a preguntar cual de las tres.{FIN}\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
