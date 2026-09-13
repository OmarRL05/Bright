"""Rellena data/route_cache.json con la geometria de calle de todos los pares.

Se corre UNA vez, con internet, y el archivo resultante se commitea. A partir
de ahi el mapa traza calle real sin tocar la red -- incluido el ensayo de la
seccion 7 del protocolo, que consiste justamente en apagarla.

    python scripts/warm_routes.py            # los 240 pares
    python scripts/warm_routes.py --pares 2-11,9-7
    python scripts/warm_routes.py --forzar   # rehace los que ya estaban

Imprime ademas la razon calle/haversine de cada par. No la aplica: eso seria
calibrar `ROAD_DETOUR_FACTOR` con un numero que solo existe si hay internet.
Se imprime para que la eleccion del factor tenga evidencia detras y para poder
revisarla cuando cambien las zonas.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.route import CACHE_PATH, clave_de  # noqa: E402
from core.models import DEFAULT_ZONE_MAP  # noqa: E402
from core.routing.euclidean import ROAD_DETOUR_FACTOR, _haversine_km  # noqa: E402
from core.routing.osrm import RouteCache, fetch_route  # noqa: E402

#: El servidor publico de OSRM pide uso moderado. Un cuarto de segundo entre
#: peticiones deja el calentado completo en ~2 minutos sin castigarlo.
PAUSA_S = 0.25


def parsear_pares(texto: str | None) -> list[tuple[int, int]]:
    if texto:
        pares = []
        for trozo in texto.split(","):
            desde, hasta = trozo.strip().split("-")
            pares.append((int(desde), int(hasta)))
        return pares
    ids = [z.zone_id for z in DEFAULT_ZONE_MAP.zones]
    return [(a, b) for a in ids for b in ids if a != b]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pares", help="lista 'desde-hasta' separada por comas")
    parser.add_argument("--forzar", action="store_true", help="rehacer los ya cacheados")
    args = parser.parse_args()

    cache = RouteCache(CACHE_PATH)
    pares = parsear_pares(args.pares)
    print(f"{len(pares)} pares · cache actual: {len(cache)} entradas\n")

    nuevos = fallidos = 0
    razones: list[float] = []

    for desde, hasta in pares:
        clave = clave_de(desde, hasta)
        if clave in cache and not args.forzar:
            ruta = cache.get(clave)
        else:
            origen = DEFAULT_ZONE_MAP.by_id(desde).coord
            destino = DEFAULT_ZONE_MAP.by_id(hasta).coord
            ruta = fetch_route([origen, destino])
            time.sleep(PAUSA_S)
            if ruta is None:
                fallidos += 1
                print(f"  FALLO {clave}")
                continue
            cache.put(clave, ruta)
            nuevos += 1

        assert ruta is not None
        recta = _haversine_km(
            DEFAULT_ZONE_MAP.by_id(desde).coord, DEFAULT_ZONE_MAP.by_id(hasta).coord
        )
        if recta > 0:
            razones.append(ruta.distance_km / recta)

    cache.save()
    tam_kb = CACHE_PATH.stat().st_size / 1024

    print(f"\n{nuevos} nuevos · {fallidos} fallidos · {len(cache)} en cache · {tam_kb:.0f} KB")

    if razones:
        razones.sort()
        mediana = razones[len(razones) // 2]
        media = sum(razones) / len(razones)
        print(
            f"\nrazon calle/haversine sobre {len(razones)} pares:\n"
            f"  min {min(razones):.3f} · mediana {mediana:.3f} · "
            f"media {media:.3f} · max {max(razones):.3f}\n"
            f"  ROAD_DETOUR_FACTOR en uso: {ROAD_DETOUR_FACTOR}"
        )
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
