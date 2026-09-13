"""Construccion de los strings `reason` del contrato oficial.

Reemplaza a `llm_log.py`. El cambio no es cosmetico: bajo el contrato oficial
`reason` dejo de ser un log interno y paso a ser un campo **evaluado**.

    "Every decision carries a reason under 40 words that names the constraint
     that actually bound. ... A correct decision with a generic or wrong
     reason does not earn the Judgment credit."
    -- student-materials/courier/README.md

Y ademas:

    "This string is read aloud during your demo."
    -- decision_response_schema.json

De ahi las dos reglas de este modulo:

1. **El limite de 40 palabras se garantiza aqui, no se confia.** Toda salida
   pasa por `cap_words()`. `validate_format.py` rechaza el response completo
   si se pasa, asi que no puede depender de que alguien cuente a ojo al
   editar una plantilla.
2. **Cada reason nombra su constraint y trae sus numeros.** "No cumple" no
   vale; "245 min de manejo continuo contra un limite de 240" si. El juez
   tiene que poder oir el string y saber que regla mordio y con que margen.

Sin acentos, igual que el resto del backend: estos strings viajan por JSON
hacia el validador y hacia el dashboard.
"""

from __future__ import annotations

from datetime import datetime

#: Limite duro del protocolo. Lo revisa validate_format.py sobre cada
#: respuesta y sobre cada evento `decision` del event log.
MAX_REASON_WORDS = 40

#: Margen de seguridad al truncar: si una plantilla se pasa, se corta antes
#: del limite para que quede claro en los tests que algo se corto.
_TRUNCATION_SUFFIX = "..."


def cap_words(text: str, max_words: int = MAX_REASON_WORDS) -> str:
    """Garantiza que `text` no exceda `max_words` palabras.

    Contar con `split()` es exactamente como cuenta `validate_format.py`
    (`len(reason.split())`), asi que esta funcion no puede discrepar del
    validador.
    """
    words = text.split()
    if not words:
        return "sin motivo registrado"
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[: max_words - 1]) + " " + _TRUNCATION_SUFFIX


def _hhmm(moment: datetime | None) -> str:
    return moment.strftime("%H:%M") if moment else "hora desconocida"


# --------------------------------------------------------------------------
# Una funcion por constraint. La firma recibe numeros crudos a proposito: el
# modulo no conoce OrderRequest ni CourierRuntimeState, asi que se puede
# testear sola y no puede introducir una dependencia circular con safety.py.
# --------------------------------------------------------------------------


def mandatory_break(riding_min: float, limit_min: float, break_min: float) -> str:
    return cap_words(
        f"Descanso obligatorio: {riding_min:.0f} min de manejo continuo superan el "
        f"limite de {limit_min:.0f}. Toca una pausa de {break_min:.0f} min antes de "
        f"aceptar otro pedido."
    )


def heat_rule(projected_min: float, limit_min: float, start_hour: int, end_hour: int) -> str:
    """El numero es el manejo continuo PROYECTADO (actual + duracion del
    pedido), no el actual. Decir "proyectados" evita que un juez compare el
    reason contra `continuous_riding_min` del estado y vea una discrepancia
    que no existe."""
    return cap_words(
        f"Regla de calor ({start_hour:02d}:00-{end_hour:02d}:00): aceptarla dejaria "
        f"{projected_min:.0f} min continuos proyectados sobre un tope de "
        f"{limit_min:.0f} min en esa franja."
    )


def flagged_zone_night(zone: int | None, arrival: datetime | None, curfew_hour: int) -> str:
    return cap_words(
        f"Zona {zone} marcada: la entrega llegaria {_hhmm(arrival)}, dentro del "
        f"toque de queda que empieza {curfew_hour:02d}:00. No se entrega de noche "
        f"en zonas marcadas."
    )


def vehicle_capacity(
    vehicle: str,
    dimension: str,
    required: float,
    limit: float,
    unit: str,
    in_flight: float,
    incoming: float,
) -> str:
    return cap_words(
        f"Capacidad de {vehicle} excedida en {dimension}: {required:.1f} {unit} contra "
        f"un limite de {limit:.1f} (mochila {in_flight:.1f} mas pedido {incoming:.1f})."
    )


def shift_end_infeasible(
    completion: datetime | None, shift_end: datetime | None, margin_min: float
) -> str:
    return cap_words(
        f"No alcanza el fin de turno: la entrega terminaria {_hhmm(completion)} y el "
        f"turno cierra {_hhmm(shift_end)} con margen de {margin_min:.0f} min."
    )


def reservation_wage(adjusted_rate: float, reservation: float, deadhead_km: float) -> str:
    return cap_words(
        f"Paga poco: ${adjusted_rate:.0f}/hr efectivos contra un minimo de "
        f"${reservation:.0f}/hr, con {deadhead_km:.1f} km de traslado en vacio."
    )


def accepted(adjusted_rate: float, reservation: float) -> str:
    return cap_words(
        f"Conviene: ${adjusted_rate:.0f}/hr efectivos contra un minimo de "
        f"${reservation:.0f}/hr, y ninguna constraint de seguridad la bloquea."
    )


def internal_error() -> str:
    """Reason de ultimo recurso.

    El protocolo trata un crash en la ventana de decision como fallo duro, asi
    que el endpoint responde SKIP con esto antes que propagar una excepcion.
    Honesto a proposito: un reason inventado seria peor que admitir la falla.
    """
    return "Error interno al evaluar la oferta: se rechaza por precaucion."
