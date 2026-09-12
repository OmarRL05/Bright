# Documentación Técnica — HackMTY 2026 · Infosys "The Courier"

## 1. Conceptos y conocimientos necesarios

**VRPTW (Vehicle Routing Problem with Time Windows)**
El problema de encontrar la secuencia de paradas de menor costo (distancia/tiempo) que respete ventanas de tiempo de entrega. Es el problema que resuelve OR-Tools en el Bloque 4 sobre los pedidos pendientes del courier.

**Heurística de inserción más barata (cheapest insertion)**
En lugar de resolver el VRP completo cada vez que llega una oferta, se calcula el costo marginal de insertarla en la posición más barata de la ruta actual. Es una construcción golosa (greedy), O(n) por oferta, y es lo que permite decidir "en segundos" como pide el reto.

**Rolling horizon / Frozen horizon**
Técnica de planeación (común en scheduling de producción y control predictivo) donde el tramo inmediato del plan queda "congelado" y no se recalcula, mientras que el resto del horizonte sí se puede reoptimizar. Aquí se usa para no cambiarle al courier el destino inmediato al que ya va en camino.

**Estabilidad de plan / "nervousness"**
Fenómeno donde un optimizador encuentra una solución matemáticamente mejor en cada iteración, pero el plan cambia tan seguido que se vuelve inútil o peligroso de seguir en la práctica (equivalente a un repartidor real recibiendo instrucciones de dar vuelta en U a medio camino). Es la razón detrás de las reglas de disparadores y umbral de histéresis del Bloque 4.

**Histéresis / switching cost**
Regla de decisión que solo acepta un cambio si el beneficio supera un umbral mínimo, para no cambiar de plan por mejoras marginales que no compensan el costo de interrumpir la ejecución actual.

**Concurrencia optimista (versioning)**
En vez de bloquear el estado compartido durante todo el tiempo que tarda el optimizador en resolver, se etiqueta cada versión del estado con un contador. El resultado del optimizador solo se aplica si la versión no cambió mientras resolvía — si cambió, se descarta y se reintenta en el siguiente disparador.

**OR-Tools Routing Library**
Librería open source de Google para VRP/TSP con ventanas de tiempo. Se modela con una matriz de distancias/tiempos entre nodos y restricciones de capacidad/tiempo. Es costosa computacionalmente para correrse en cada evento, por eso se reserva para el Bloque 4 (background, con disparadores controlados).

**OSMnx + NetworkX**
OSMnx descarga y construye el grafo vial real de una ciudad a partir de OpenStreetMap; NetworkX resuelve caminos más cortos sobre ese grafo. Los cierres viales se simulan asignando peso infinito a las aristas afectadas y recalculando.

**FastAPI + WebSockets**
Framework async de Python con soporte nativo de WebSocket, sin necesitar monkey-patching (a diferencia de Flask + Flask-SocketIO), lo cual es importante porque el sistema ya corre un thread de background (Bloque 4) y agregar gevent/eventlet aumentaría el riesgo de conflictos.

---

## 2. Por qué se tomó cada decisión

| Decisión | Alternativa considerada | Por qué se descartó | Por qué se eligió esto |
|---|---|---|---|
| Estado en memoria, un solo proceso | Redis + Celery/APScheduler + Supabase (Postgres/PostGIS) | Overhead de infraestructura sin beneficio en los criterios de juicio para un demo de un solo turno; más piezas que pueden fallar el día de la presentación | Un objeto de estado compartido con locks es suficiente y más confiable para el alcance del hackathon |
| Heurística de inserción + reoptimización periódica con OR-Tools | Resolver el VRP completo con OR-Tools en cada oferta | Un solve completo no es lo bastante rápido para decidir "en segundos" por oferta, y menos con varios agentes corriendo en el demo | Separar la decisión instantánea (heurística) de la mejora global (OR-Tools en background) |
| OSMnx + NetworkX | OSRM en contenedor Docker | Costo de setup alto (descarga de datos, preprocesamiento, tiles) frente al tiempo disponible en el hackathon | Grafo real de Monterrey con shortest path, suficientemente realista y más rápido de tener funcionando |
| FastAPI | Flask + Flask-SocketIO | Flask no soporta WebSocket nativo; las extensiones necesarias (eventlet/gevent) pueden chocar con el thread nativo del optimizador | WebSocket nativo, async, sin monkey-patching |
| Frozen horizon + disparadores + umbral de histéresis | Reoptimizar constantemente ante cualquier información nueva | Genera inestabilidad de ruta ("route thrashing"): cambios de plan inseguros e inútiles para un repartidor real, penaliza Feasibility y Judgment | Un "consultor" en background que solo propone cambios cuando son significativos y respeta el tramo en curso |
| Stream de eventos con semilla fija para ambos agentes | Generar eventos de forma independiente en cada corrida | No sería una comparación justa de "mismo turno fresco" entre el agente IA y el baseline | Mismo seed reproducido exactamente para ambos agentes |

---

## 3. Mapeo a los criterios de evaluación del reto

**Results** — se mide comparando las ganancias del agente IA contra el baseline sobre exactamente el mismo turno (mismo seed de eventos), reportando $/hora.

**Judgment** — las reglas de seguridad (frozen horizon, override inmediato ante cierres, umbral mínimo de $/km) evitan decisiones erráticas o peligrosas; el log de explicabilidad permite defender cada decisión ante la pregunta sorpresa del jurado.

**Feasibility** — al evitar route thrashing y mantener decisiones en tiempo real factibles (heurística rápida + optimización de fondo controlada), el comportamiento del agente se parece al de un repartidor real, no al de un solver que ignora las consecuencias prácticas de cambiar de plan.

**Clarity** — el dashboard en vivo (mapa, métricas, feed de logs) y esta documentación permiten seguir el razonamiento del agente sin ambigüedad.

---

## 4. Datasets y recursos externos

- **OpenStreetMap** (vía OSMnx) — grafo vial real de Monterrey.
- **Google OR-Tools** — librería de ruteo open source, usada en el Bloque 4.
- **Solomon VRPTW benchmark** y **datasets de food-delivery en Kaggle** — para dar forma realista al stream de ofertas del simulador y a la señal de demanda histórica ("zonas calientes") que usa el motor de decisión.

---

## 5. Supuestos y limitaciones

- Se simula un courier a la vez por corrida (agente IA + baseline en paralelo, mismo stream).
- El grafo vial es estático salvo por los eventos explícitos de cierre/tráfico que inyecta el simulador.
- Los parámetros del umbral de histéresis (5% / 2 min) y el mínimo $/km de aceptación son configurables — deben presentarse como decisiones justificadas, no como constantes arbitrarias, si el jurado pregunta por ellos.

---

## 6. Extensiones posibles (si el tiempo alcanza)

- **Posicionamiento proactivo**: el reto pide explícitamente que el agente "posicione para el surge evitando zonas lentas o inseguras" — esto implica moverse hacia zonas de mayor demanda histórica durante tiempos sin ofertas activas, no solo reaccionar a las que llegan. Es un diferenciador fuerte para el criterio de Judgment si se alcanza a implementar.
- **Formateo de logs con un LLM ligero** (ej. Gemini) para que las explicaciones se lean más naturales en el demo, sin cambiar la lógica de decisión subyacente.
