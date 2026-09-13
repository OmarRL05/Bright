# Nota sobre el mapa: por qué la ruta ya sigue calles

> **Resumen en una línea:** el mapa traza calle real desde una caché en disco
> (`backend/data/route_cache.json`), funciona con el wifi apagado, y cuando no
> puede trazarla lo dice en vez de fingir.

Esta nota tiene historia porque el camino tuvo dos callejones sin salida, y los
dos enseñan algo que conviene no repetir.

## Estado final

| | Decisiones (`/decide`) | Mapa (`/route`) |
|---|---|---|
| Cómo mide | haversine × 1.35 | geometría real de OSRM |
| De dónde sale | cálculo puro | `data/route_cache.json` (240 pares) |
| Necesita red | no | no, si el par está cacheado |
| Dentro del presupuesto de 50 ms | sí | no aplica, nunca se llama ahí |

Las dos velocidades son a propósito. La decisión **no puede** depender de la
red ni del disco: el presupuesto de 50 ms y el replay determinista (protocolo,
secciones 3 y 6) lo prohíben. El mapa sí puede, porque es dibujo.

`GET /status` publica las dos para que el dashboard rotule con la verdad del
sistema y no con una cadena escrita a mano que envejece en silencio:

```json
{
  "distance_model": "decisiones: haversine entre centroides de zona x 1.35 (factor de rodeo, calibrado contra 240 rutas reales). Mapa: geometria de calle real (OSRM)",
  "road_detour_factor": 1.35,
  "route_geometry_available": true,
  "routes_cached": 240
}
```

## Callejón 1: no había geometría, y la línea recta al menos era honesta

Al principio el backend no exponía ruta alguna: ni polilínea, ni nodos, ni
waypoints. Una línea entre centroides era lo único pintable. Eso estaba bien
mientras el mapa **dijera** que era una línea recta, y esa sigue siendo la
regla de fondo de este documento.

Lo que sí se arregló entonces fue el número: las distancias eran ~30% cortas y
con ellas se calculaban combustible, tiempos y umbral de aceptación. De ahí
salió `ROAD_DETOUR_FACTOR`.

## Callejón 2: el grafo local — equivocado con apariencia de correcto

El segundo intento fue descargar el grafo vial con OSMnx:

```python
ox.graph_from_place("Monterrey, Nuevo Leon, Mexico", network_type="drive")
```

38 MB, 29 054 nodos, cargaba en 2.8 s, respondía en 118 ms. Parecía resuelto.

No lo estaba: eso descarga el **municipio** de Monterrey, no el área
metropolitana, y **9 de las 16 zonas caen fuera de él**. Apodaca quedaba a
11 947 m del nodo más cercano; Santa Catarina a 6 069 m; Parque Industrial a
6 017 m; Escobedo a 4 881 m.

Y `osmnx.nearest_nodes` nunca falla — devuelve *algo*. Así que el mapa dibujaba
con total confianza rutas que terminaban a kilómetros del destino. La línea
recta era peor de mirar pero mejor de creer.

**La lección, que es la de toda esta nota:** un fallo que se anuncia (una línea
recta rotulada) es barato. Un fallo silencioso que se ve bien es caro, porque
nadie lo va a buscar. Esa medición equivocada por poco recalibra
`ROAD_DETOUR_FACTOR`: la ruta Contry → Escobedo del grafo daba 20.94 km, y son
24.83 km reales.

## La salida: OSRM, cacheado en disco

`core/routing/osrm.py` pide la geometría al servidor público de OSRM, la
simplifica con Douglas-Peucker a 10 m (782 puntos → 92 en la ruta más larga;
10 m es sub-pixel a zoom 11) y la guarda en `data/route_cache.json`.

Ese archivo **se commitea**, y esa es la decisión de diseño que importa: el
ensayo de la sección 7 del protocolo consiste en apagar el wifi, y con la
caché en el repo el mapa sigue trazando calles. Son 263 KB.

```bash
python scripts/warm_routes.py     # los 240 pares, ~2 min, requiere internet
```

Servido desde caché, `/route` contesta en ~1 ms y no toca la red.

## El factor de rodeo, ahora con evidencia

Calentar la caché mide de paso la razón calle/haversine de los **240 pares**:

```
min 1.057 · mediana 1.293 · media 1.319 · max 4.699
ROAD_DETOUR_FACTOR en uso: 1.35
```

Se deja en 1.35, por encima de la media, a propósito. Subestimar la distancia
subestima el tiempo de viaje, y de ahí salen los `shift_end_infeasible` que no
se detectan a tiempo — errar del lado corto tiene consecuencias de seguridad
que errar del lado largo no tiene. El margen es del 2.4%.

No se calibra automáticamente con esa cifra: sería atar el número que usan las
decisiones a algo que sólo existe si hay internet.

## Lo que el mapa muestra

- **Ruta aceptada**: polilínea verde siguiendo calles. La última va gruesa; las
  anteriores, tenues.
- **Recolección → entrega**: anillo hueco en el origen del último viaje, punto
  sólido en el destino. Sólo el último — treinta pares de marcadores tapan el
  mapa que intentan explicar.
- **El tooltip dice siempre qué es la línea**: `calle real · 24.8 km`, o
  `línea recta — sin geometría vial`. Un trazo que sigue calles y uno que las
  ignora se parecen demasiado como para dejar que el juez adivine cuál ve.
