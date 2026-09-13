"""Tests del endpoint oficial POST /decide y GET /explain_decision (Bloque 6).

Verifica los requerimientos del protocolo de evaluación oficial:
- Latencia dentro del presupuesto de 50 ms.
- Cumplimiento estricto del formato oficial (validate_format.py).
- Manejo de zonas marcadas nocturnas (curfew >= 22:00 en zonas 2 y 11).
- Manejo resiliente de zonas no conocidas (demanda neutral, zone_known: false en explain_inputs).
"""

import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from main import app

# Importar el validador oficial del reto
VALIDATOR_DIR = Path(__file__).resolve().parents[2] / "student-materials" / "courier"
sys.path.append(str(VALIDATOR_DIR))
from validate_format import PROBE, check_response  # noqa: E402

client = TestClient(app)


def test_decide_official_probe_passes_validator():
    """El PROBE oficial debe responder 200 y pasar check_response con 0 errores."""
    resp = client.post("/decide", json=PROBE)
    assert resp.status_code == 200
    data = resp.json()

    # Latencia dentro del presupuesto de 50 ms
    assert data["latency_ms"] < 50.0

    errs = check_response(data, "/decide", data["latency_ms"])
    assert not errs, f"Errores en formato oficial: {errs}"


def test_decide_rejects_flagged_zone_11_at_night():
    """Un dropoff en zona marcada 11 a las 23:00 (>= 22:00) debe disparar
    flagged_zone_night y responder SKIP.
    """
    payload = dict(PROBE)
    payload["order_id"] = "PROBE-NIGHT-ZONE-11"
    payload["zone_dropoff"] = 11
    payload["sim_time"] = "2026-03-21T23:00:00"

    resp = client.post("/decide", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["decision"] == "SKIP"
    assert data["binding_constraint"] == "flagged_zone_night"
    assert "Zona 11 marcada" in data["reason"]

    errs = check_response(data, "/decide", data["latency_ms"])
    assert not errs


def test_decide_rejects_flagged_zone_2_at_night():
    """Un dropoff en Centro (zona 2) a las 23:00 (>= 22:00) también dispara
    flagged_zone_night y responde SKIP.
    """
    payload = dict(PROBE)
    payload["order_id"] = "PROBE-NIGHT-ZONE-2"
    payload["zone_dropoff"] = 2
    payload["sim_time"] = "2026-03-21T23:15:00"

    resp = client.post("/decide", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["decision"] == "SKIP"
    assert data["binding_constraint"] == "flagged_zone_night"
    assert "Zona 2 marcada" in data["reason"]


def test_decide_accepts_non_flagged_zone_at_night():
    """Un dropoff en zona no marcada (ej. Tec, zona 0) a las 23:00 NO dispara
    flagged_zone_night.
    """
    payload = dict(PROBE)
    payload["order_id"] = "PROBE-NIGHT-ZONE-0"
    payload["zone_dropoff"] = 0
    payload["sim_time"] = "2026-03-21T23:00:00"

    resp = client.post("/decide", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["binding_constraint"] != "flagged_zone_night"


def test_decide_handles_unknown_zone_gracefully_and_reports_zone_known_false():
    """Si un juez manda una zona desconocida (ej. 9999), no debe haber 500:
    se aplica demanda neutral (0.50) y el explain log reporta zone_known: false.
    """
    payload = dict(PROBE)
    payload["order_id"] = "PROBE-UNKNOWN-ZONE"
    payload["zone_pickup"] = 9999
    # Tambien el dropoff: en el motor actual la demanda que mueve la economia
    # es la de la zona DONDE TERMINAS, no la de donde recoges. Dejar el
    # dropoff en una zona conocida haria que el test pasara por casualidad
    # sin haber ejercitado nunca el camino de "zona desconocida".
    payload["zone_dropoff"] = 9998

    resp = client.post("/decide", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    errs = check_response(data, "/decide", data["latency_ms"])
    assert not errs

    # Consultar explain_decision para verificar trazabilidad
    explain_resp = client.get("/explain_decision/PROBE-UNKNOWN-ZONE")
    assert explain_resp.status_code == 200
    explain_data = explain_resp.json()

    inputs = explain_data["inputs"]
    # Se reportan las DOS zonas por separado: "zone_known" a secas era
    # ambiguo (¿pickup o dropoff?) y la zona que mueve la economia es la de
    # dropoff, no la de pickup.
    assert inputs["offer"]["zone_pickup_known"] is False
    assert inputs["offer"]["zone_dropoff_known"] is False
    # Demanda neutral: no podemos afirmar que una zona que no conocemos sea
    # caliente ni fria, asi que no se premia ni se castiga.
    assert inputs["economics"]["dropoff_demand_score"] == pytest.approx(0.50)
