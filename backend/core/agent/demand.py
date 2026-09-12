"""Señal de demanda historica por zona (placeholder).

El reparto de tareas pone esta señal dentro de Bloque 3 ("comparar costo
marginal contra ... la señal de demanda historica de la zona"), pero ningun
bloque la produce todavia: `Offer` no trae campo de zona/demanda y
`data/kaggle_orders.csv` (Bloque 1) sigue sin existir en el repo. Ver
docs/Bloque 3/01_Plan.md seccion 7.

StaticDemandSignal es el default para no bloquear el resto del motor de
decision: usa las mismas 4 zonas que ya genera core.simulation.engine para
que el log de explicabilidad y el mapa del dashboard hablen de los mismos
lugares. Se reemplaza por una version real (Kaggle) si el tiempo alcanza,
sin cambiar la firma de DemandSignal.
"""

from typing import Protocol


class DemandSignal(Protocol):
    def zone_score(self, location: tuple[float, float]) -> float:
        """Score de demanda historica de la zona a la que pertenece `location`.

        Rango 0.0 (zona fria) - 1.0 (zona caliente).
        """
        ...


# Mismas coordenadas que core.simulation.engine.SimulationEngine._zones.
# Si Persona A las cambia, actualizar aqui (unico lugar que las duplica).
_ZONES: dict[str, tuple[float, float]] = {
    "Tec": (25.651, -100.289),
    "San Pedro": (25.657, -100.402),
    "Centro": (25.680, -100.310),
    "Apodaca": (25.780, -100.180),
}

# Scores iniciales arbitrarios (Centro/Tec mas "calientes" que Apodaca por
# densidad urbana esperada) -- placeholder a calibrar con datos reales en la
# Fase 4 del cronograma, igual que MIN_PAY_PER_KM en decision.py.
_ZONE_SCORES: dict[str, float] = {
    "Centro": 0.9,
    "Tec": 0.7,
    "San Pedro": 0.5,
    "Apodaca": 0.3,
}


def _squared_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


class StaticDemandSignal:
    """Placeholder de DemandSignal: score fijo por la zona conocida mas cercana."""

    def zone_score(self, location: tuple[float, float]) -> float:
        nearest_zone = min(_ZONES, key=lambda name: _squared_distance(_ZONES[name], location))
        return _ZONE_SCORES[nearest_zone]
