"""Generacion/formato de explicaciones de decisiones.

Genera logs cortos y legibles (ej. "Rechazado: desvio de 15 min por $20 en
zona fria"). Ver docs/01_Arquitectura.md seccion 3 (Bloque 3, explicabilidad).

La extension opcional de formatear con un LLM ligero (docs/02, seccion 6)
va aqui, detras de la misma firma de funcion, sin tocar decision.py.
"""


def format_decision_log(accepted: bool, offer_id: str, reason: str) -> str:
    verdict = "Aceptado" if accepted else "Rechazado"
    return f"{verdict} [{offer_id}]: {reason}"
