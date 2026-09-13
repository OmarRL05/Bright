# Nota para quien dibuja el mapa

> **Resumen en una línea:** la línea recta del mapa no es un bug tuyo. El
> backend no tiene ruta que darte, y ahora lo dice él mismo por `GET /status`.

## Por qué sale recta

El backend **no expone geometría de ruta**: ni polilínea, ni lista de nodos,
ni waypoints. No hay nada de eso en ninguna respuesta. Lo único disponible son
coordenadas de centroide de zona, así que una línea entre dos puntos es lo
único que se puede pintar.

Eso es fiel al modelo: todo el camino de decisión mide con **haversine entre
centroides de zona**, no con calles. Existe un `RoadNetwork` sobre OSMnx
(`core/routing/graph.py`), pero `osmnx` no está instalado, el `monterrey.graphml`
no existe, y además ese módulo devuelve *longitudes*, no el camino — para una
polilínea haría falta código nuevo.

## Lo que cambió en el backend

Las distancias eran **~30% cortas** y con ellas se calculaban el combustible,
los tiempos y el umbral de aceptación. Ahora se multiplican por un **factor de
rodeo** de 1.35, medido contra distancias reales en coche:

```
Contry -> Escobedo   19.15 km recta  ->  25.86 km con rodeo  (real ~26)
```

Así que **el número ya es honesto aunque el dibujo siga siendo recto**. La
distancia que muestres —`economics.total_km` del response de `/decide`— se
puede enseñar con confianza.

## Lo que te toca a ti

**1. Etiqueta la línea, y saca la etiqueta del sistema.** `GET /status` publica:

```json
{
  "distance_model": "haversine entre centroides de zona x 1.35 (factor de rodeo); sin grafo vial",
  "road_detour_factor": 1.35,
  "route_geometry_available": false
}
```

Está ahí para que el rótulo no sea una cadena escrita a mano que envejece en
silencio el día que enchufemos el grafo. Cuando `route_geometry_available`
pase a `true`, habrá polilínea y el mismo código puede cambiar de modo.

Copia sugerida bajo el mapa, o como tooltip de la línea:

> *Trayecto estimado entre zonas — línea recta con factor de rodeo, no ruta
> por calles.*

**2. Dibújala punteada, no sólida.** Una línea sólida sugiere un camino
medido; una punteada sugiere una conexión. Es la diferencia entre que un juez
piense "su router está roto" y que piense "modelaron zonas, no calles".

**3. No cambies el título de la tarjeta a "Ruta".** Hoy dice *"Ruta en vivo"* y
eso promete algo que no hay. *"Trayecto estimado"* o *"Zonas y destino"* dicen
la verdad y cuestan lo mismo.

## Si alguien pregunta en la demo

> «Modelamos la ciudad como 16 zonas, no como calles. La línea une el centroide
> de origen con el de destino; la distancia sí está corregida por un factor de
> rodeo de 1.35, medido contra recorridos reales. Lo que el sistema sabe es
> **cuánto** se recorre, no **por dónde** — y todas las decisiones dependen del
> cuánto.»

Es una respuesta que se sostiene. Fingir una ruta que no calculamos, no.
