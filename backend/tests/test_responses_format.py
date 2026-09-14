"""`validate_format.py --responses`: la tercera bandera del validador.

El equipo corria `--event-log` y `--endpoint` y nunca `--responses`. La
diferencia no es de volumen, es de cobertura:

- `--endpoint` manda el PROBE, **una** oferta bien formada y sin estado, y
  comprueba la forma de la respuesta feliz.
- `--responses` valida un **lote**, asi que es la unica de las tres que puede
  demostrar que los *refusals* tambien estan bien formados -- que el `reason`
  de una violacion de capacidad no se pasa de 40 palabras, que cada
  `binding_constraint` esta en el enum oficial, y que la latencia aguanta
  cuando el gate si tiene trabajo.

Estos tests corren `student-materials/courier/validate_format.py` **de
verdad**, en un subproceso, sobre respuestas que produjo la app real. No
comprueban nuestra idea del formato.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.agent.shocks import SHOCKS

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
VALIDATOR = REPO / "student-materials" / "courier" / "validate_format.py"
RECORDER = BACKEND / "scripts" / "record_responses.py"

#: Los seis ids del enum oficial. Si el lote grabado no dispara uno, es que
#: esa constraint no se esta ejercitando -- y el protocolo penaliza
#: explicitamente una constraint que existe en el codigo y nunca se ve
#: disparar.
CONSTRAINTS_ESPERADAS = {
    "flagged_zone_night",
    "mandatory_break",
    "heat_rule",
    "shift_end_infeasible",
    "vehicle_capacity",
    "reservation_wage",
}


@pytest.fixture(scope="module")
def lote(tmp_path_factory) -> list[dict]:
    """Graba una tanda con el script real y devuelve las respuestas.

    Se usa el script y no una reimplementacion: si el script se rompe, estos
    tests tienen que enterarse, porque es el que produce la evidencia que se
    enseña.
    """
    SHOCKS.reset()  # un shock heredado moveria los numeros del lote
    destino = tmp_path_factory.mktemp("responses") / "responses.json"

    proceso = subprocess.run(
        [
            sys.executable,
            str(RECORDER),
            "--out",
            str(destino),
            "--seed",
            "101",
            "--shift-orders",
            "25",
        ],
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
    )
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr
    return json.loads(destino.read_text(encoding="utf-8"))


def test_el_lote_pasa_el_validador_oficial(lote, tmp_path):
    destino = tmp_path / "responses.json"
    destino.write_text(json.dumps(lote), encoding="utf-8")

    proceso = subprocess.run(
        [sys.executable, str(VALIDATOR), "--responses", str(destino)],
        capture_output=True,
        text=True,
    )
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr
    assert "PASS" in proceso.stdout


def test_el_lote_ejercita_las_seis_constraints(lote):
    """Un lote que pasa el validador sin disparar nada no prueba gran cosa."""
    vistas = {r.get("binding_constraint") for r in lote}
    faltantes = CONSTRAINTS_ESPERADAS - vistas
    assert not faltantes, f"el lote no ejercita: {sorted(faltantes)}"


def test_el_lote_tiene_accepts_y_skips(lote):
    """Si todo fuera SKIP, el lote no validaria la forma del camino feliz."""
    decisiones = {r["decision"] for r in lote}
    assert decisiones == {"ACCEPT", "SKIP"}


def test_toda_respuesta_cabe_en_el_presupuesto(lote):
    """50 ms es el presupuesto del fast path y el validador lo comprueba
    respuesta por respuesta, no en promedio."""
    peor = max(r["latency_ms"] for r in lote)
    assert peor < 50, f"latencia maxima {peor:.2f} ms"


def test_ningun_reason_se_pasa_de_40_palabras(lote):
    """Se cuenta igual que `validate_format.py` (`len(reason.split())`), asi
    que este test no puede discrepar del validador."""
    largos = {
        r["order_id"]: len(r["reason"].split())
        for r in lote
        if len(r["reason"].split()) > 40
    }
    assert not largos, largos


def test_el_probe_oficial_esta_en_el_lote(lote):
    """Si el lote no incluye lo mismo que `--endpoint` manda, no prueba lo
    mismo que el validador prueba."""
    assert any(r["order_id"] == "FORMAT-PROBE-001" for r in lote)
