# data/

## Lo que SÍ está en el repo

- `route_cache.json` (263 KB) — geometría de calle real entre los 240 pares
  de zonas, resuelta con OSRM. **El mapa del dashboard se dibuja con esto**,
  y por eso se commitea: con el wifi apagado (el ensayo de la sección 7 del
  protocolo) el mapa sigue trazando calles de verdad en vez de líneas rectas.

  Se regenera con internet:
  ```bash
  python scripts/warm_routes.py            # los 240 pares, ~2 min
  python scripts/warm_routes.py --forzar   # rehace los ya cacheados
  ```
  Correrlo si cambian las coordenadas de las zonas en `core/models.py`.

## Lo que NO se commitea (ver `.gitignore`)

- `cache/` — descargas crudas de OSMnx/Overpass, 53 MB y creciendo. Se
  regeneran solas; no hace falta conservarlas.

- `kaggle_orders.csv` — dataset de food-delivery de Kaggle usado para dar
  forma al stream de ofertas y a la señal de demanda histórica. Descargar
  manualmente y colocar aquí.

- `monterrey.graphml` (38 MB) — grafo vial. **Ya no lo usa el mapa**; queda
  sólo para el motor VRPTW de coordenadas (`core/routing/ortools_optimizer.py`)
  y sus tests, que se saltan solos si el archivo no está.

  ### Cuidado con cómo se genera

  La receta obvia produce un grafo **equivocado con apariencia de correcto**:

  ```python
  import osmnx as ox
  g = ox.graph_from_place("Monterrey, Nuevo Leon, Mexico", network_type="drive")
  ```

  Eso descarga el **municipio** de Monterrey, no el área metropolitana, y
  9 de las 16 zonas del simulador quedan fuera: Apodaca a 11 947 m del nodo
  más cercano, Santa Catarina a 6 069 m, Parque Industrial a 6 017 m,
  Escobedo a 4 881 m, San Nicolás a 3 992 m, Valle Oriente a 2 220 m,
  San Pedro a 1 962 m, Del Valle a 1 801 m, Linda Vista a 1 493 m.

  `osmnx.nearest_nodes` nunca falla — devuelve *algo* — así que el grafo
  contesta con toda confianza rutas que terminan a kilómetros del destino.
  Medido: Contry → Escobedo daba 20.94 km contra los 24.83 km reales.

  Si de verdad hace falta el grafo, pedir los municipios del área:

  ```python
  g = ox.graph_from_place(
      ["Monterrey", "San Pedro Garza García", "San Nicolás de los Garza",
       "Guadalupe", "Apodaca", "General Escobedo", "Santa Catarina"],
      network_type="drive",
  )
  ```

  Y verificar la cobertura antes de confiar en él: para cada zona de
  `DEFAULT_ZONE_MAP`, la distancia al nodo más cercano tiene que ser de
  decenas de metros, no de kilómetros.

Comparte los archivos pesados por otro medio (Drive, Slack) para no inflar
el repo.
