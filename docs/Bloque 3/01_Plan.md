# Plan — Bloque 3: Motor de Decisión en Tiempo Real (Persona B)

> Estado: **planeación**, sin código todavía en Bloque 3. Ver `docs/01_Arquitectura.md`
> sección 3 (Bloque 3) y sección 6 (reglas de estabilidad), y
> `docs/02_Documentacion_Tecnica.md` sección 1 para el vocabulario (cheapest
> insertion, frozen horizon, histéresis).
>
> **Revisión 2** (misma esencia y estructura que la v1; se ajustan las secciones
> 3, 5, 7, 9 y 11 con lo que ya se implementó en el resto del repo — ver sección 0).

## 0. Estado actual del repo (contexto para arrancar la implementación)

El repo ya no está en el punto "puro scaffold" de la v1 de este plan. Ahora vive
en la rama `merge` (fusión de `feat/b5` + una rama de simulación), con esto
implementado:

| Bloque | Archivo | Estado real |
|---|---|---|
| 1 (Persona A) | `core/simulation/engine.py` | **Implementado** (versión inicial): genera `Offer`/`RoadEvent` sintéticos reproducibles por seed, sobre 4 zonas fijas (ver sección 7). Sin dataset Kaggle todavía (`TODO` explícito en el propio archivo). |
| 2 (Persona A) | `core/simulation/state.py` | Sin cambios vs. v1 — ya estaba implementado y con tests en verde. |
| 4 (¿Persona C?) | `core/optimization/ortools_optimizer.py` (**archivo nuevo**, no es el que describe `docs/01_Arquitectura.md` sección 7) | **Implementado y funcional en aislamiento** (VRPTW real con OR-Tools, hilo + cola de prioridad, histéresis), pero con **su propio modelo de datos** (`Node`, `EstadoRuta`, `RutaOptimizada`, `StateManagerProtocol`) que **no coincide** con `core/models.py` ni con `CourierStateManager` reales (ver sección 11, riesgo nuevo #6). |
| 4 (ubicación original) | `core/routing/ortools_optimizer.py` | Sigue siendo el stub original (`NotImplementedError`), sin tocar. |
| 5 (Persona C) | `core/routing/graph.py` | **Sigue siendo el stub original** — `shortest_path_time` y `apply_road_event` siguen en `NotImplementedError`. Solo se agregó infraestructura (`data/datacreate.py` para descargar el grafo, y caché de OSMnx), no la lógica. |
| 3 (Persona B, este bloque) | `decision.py`, `greedy.py`, `llm_log.py` | Sin cambios — siguen como stub, es lo que vamos a implementar. |

**Conclusión para el plan:** lo que de verdad importa para Bloque 3 no cambió —
Bloque 5 (la dependencia real de "distancias") sigue sin implementar, así que el
placeholder euclidiano de la sección 3 sigue siendo necesario, no opcional. Lo
que sí cambia es que ahora hay evidencia concreta (no hipotética) de que los
contratos compartidos se están divergiendo entre bloques, lo cual refuerza — no
cambia — la recomendación de aislar Bloque 3 detrás de sus propios `Protocol`s
en vez de asumir cómo va a verse la interfaz final de Bloque 4/5.

## 1. Alcance del bloque

Bloque 3 evalúa **cada oferta** apenas llega y decide aceptar/rechazar en el hilo
principal, sin bloquear la simulación. Archivos que le pertenecen:

- `backend/core/agent/decision.py` — `DecisionEngine.evaluate(offer) -> Decision`
- `backend/core/routing/greedy.py` — `cheapest_insertion(...)` (heurística O(n))
- `backend/core/agent/llm_log.py` — formato del log explicable (ya tiene una
  implementación mínima funcional, `format_decision_log`)

Fuera de alcance (de otras personas, solo se consumen sus interfaces):

- `core/models.py` — contratos fijos, no se tocan sin acordarlo con el equipo.
- `core/simulation/state.py` (Persona A) — se **usa** (`snapshot()`, `accept_offer()`),
  no se modifica.
- `core/routing/graph.py` (Persona C) — el grafo real de Monterrey. Bloque 3 no lo
  implementa, pero sí define, junto con Persona C, la interfaz que va a consumir
  (ver sección 3).
- `core/routing/ortools_optimizer.py`, `stability.py` (Persona C) — el optimizador
  de fondo. Bloque 3 solo necesita saber que existe `on_offer_accepted()` como
  gancho de notificación (ver sección 4.6).

**Entregable formal** (repartición original): función
`decide(oferta, estado, distancias) → aceptar/rechazar + log`.
El scaffold ya la concretó como `DecisionEngine.evaluate(offer) -> Decision`, que
lee `estado` internamente vía `self.state_manager.snapshot()` en lugar de recibirlo
como parámetro. Nos quedamos con la firma del scaffold (ya está commiteada y es lo
que Persona D/main.py van a instanciar) y tratamos la del reparto como la
descripción funcional, no como el firma literal a implementar.

---

## 2. Por qué esto no es "solo llenar los TODO"

Los tres archivos son `raise NotImplementedError` con la firma ya puesta, así que
la tentación es rellenar directo. Pero hay tres decisiones de diseño que **no
están en el scaffold** y que si se improvisan sobre la marcha generan retrabajo o
rompen la integración con Persona C en Fase 3:

1. **Cómo se desacopla la heurística del grafo real** (Bloque 5 no existe todavía).
2. **Cómo se deriva el `frozen_index`** a partir de `CourierState` (concepto que
   comparten Bloque 3 y Bloque 4 — si cada quien lo interpreta distinto, el
   "tramo comprometido" deja de significar lo mismo en ambos lados).
3. **De dónde sale la señal de demanda histórica** — no tiene dueño explícito en
   el reparto de tareas ni campo en el contrato `Offer`.

Estas tres se resuelven abajo con una propuesta concreta + la pregunta exacta que
hay que confirmar con el equipo en el próximo checkpoint.

---

## 3. Interfaz de distancias (desacople de Bloque 5)

**Propuesta:** definir un `Protocol` compartido en
`core/routing/distance_provider.py` (archivo nuevo, no pertenece ni a greedy.py ni
a graph.py exclusivamente):

```python
class DistanceProvider(Protocol):
    def travel_time(self, origin: tuple[float, float], destination: tuple[float, float]) -> float: ...
    def travel_distance(self, origin: tuple[float, float], destination: tuple[float, float]) -> float: ...
```

- `EuclideanDistanceProvider` (implementación de Persona B, vive en
  `core/routing/euclidean.py`): distancia haversine + velocidad promedio asumida
  para convertir a tiempo. Placeholder mientras Persona C no tiene el grafo.
- `RoadNetwork` (Persona C, en `graph.py`) implementa el mismo `Protocol` cuando
  esté listo. `cheapest_insertion` y `DecisionEngine` reciben el provider como
  parámetro/dependencia — nunca importan `RoadNetwork` ni `EuclideanDistanceProvider`
  directamente — así el swap en Fase 3 (integración) es cambiar qué se instancia
  en `main.py`, sin tocar Bloque 3.
- `graph.py` ya tiene `shortest_path_time(origin, destination) -> float`, que casi
  calza con `travel_time`. Falta que exponga también una distancia (metros/km)
  para la regla de `$/km` — **pendiente de confirmar con Persona C** que agregue
  `travel_distance` con esa firma exacta.

**Pregunta para el checkpoint de equipo:** ¿Persona C puede alinear `graph.py` a
este `Protocol` (mismos nombres de método) para no necesitar un adaptador después?

**Actualización (revisión 2):** `graph.py` sigue exactamente igual que en la v1
de este plan (`shortest_path_time`/`apply_road_event` en `NotImplementedError`),
así que este placeholder sigue siendo un bloqueante real a resolver por Bloque 3
mismo, no una precaución de sobra. Además, ahora existe
`core/optimization/ortools_optimizer.py` (Bloque 4, implementado) que define su
**propio** `TopologyEngineProtocol.get_distance_matrix(nodos: list[Node]) -> list[list[int]]`
— una interfaz por lotes sobre un tipo `Node` propio, distinta tanto de
`graph.py` como del `DistanceProvider` propuesto aquí. Esto no cambia el diseño
de Bloque 3 (nuestro caso de uso es evaluar una oferta a la vez, no resolver un
VRPTW por lotes, así que una interfaz par-a-par sigue siendo la correcta para
nosotros), pero sí es una señal más para el checkpoint: el "contrato de
distancias" del reparto de tareas ya se fragmentó en dos formas distintas antes
de que Bloque 5 exista. Vale la pena que el equipo decida en el checkpoint si
Bloque 5 va a exponer ambas formas (par-a-par para Bloque 3, matriz para Bloque 4)
o si alguien construye un adaptador encima de una sola.

---

## 4. Heurística de inserción (`greedy.py`)

Algoritmo cheapest insertion, O(n) sobre las paradas pendientes de `route`:

1. Para cada posición válida `i` en `route` (respetando `frozen_index`, ver
   sección 5), calcular el costo de insertar `pickup` y `dropoff` de la oferta
   ahí: `extra_time = tiempo(prev, pickup) + tiempo(pickup, dropoff) + tiempo(dropoff, next) - tiempo(prev, next)`.
2. Elegir el `i` con menor `extra_time` (empate → menor `extra_distance`).
3. Validar que `dropoff.eta` resultante quepa dentro de `offer.time_window`; si no
   cabe en ninguna posición, no es insertable (se refleja en el resultado, no se
   descarta silenciosamente — `DecisionEngine` decide qué hacer con eso).
4. Devolver `InsertionResult(extra_distance, extra_time, new_route)` ya con el
   contrato existente.

Caso borde: `route` vacío → una sola posición posible (pickup + dropoff al final).

---

## 5. Frozen horizon — cómo se deriva `frozen_index`

`CourierState.route` es una lista de `RouteStop`; no guarda "posición actual del
courier" explícita. Propuesta: **el primer `RouteStop` de la lista siempre
representa el tramo en tránsito** → `frozen_index = 1 if route else 0` (no se
puede insertar antes de esa posición salvo costo marginal ~0, según la regla 1 de
`docs/01_Arquitectura.md` sección 6).

Esto es un concepto **compartido con Bloque 4** (el optimizador global también
respeta el mismo tramo comprometido al fijar su "punto de inicio"). Si Persona C
ya asumió una definición distinta, hay que reconciliarla antes de implementar —
si se malinterpreta, el optimizador y el motor de decisión podrían pisarse la
ruta activa en Fase 3.

**Pregunta para el checkpoint:** confirmar con Persona C que `frozen_index = 1 if route else 0` es la misma noción de "tramo comprometido" que usa `ortools_optimizer.py`.

**Actualización (revisión 2):** `core/optimization/ortools_optimizer.py` ya
implementa esta misma idea con nombres explícitos —
`EstadoRuta.active_leg_destination` (el tramo comprometido, equivalente a
"`route[0]`") vs. `EstadoRuta.pending_stops` (lo reordenable) — lo cual
**valida** la interpretación de `frozen_index = 1 if route else 0` propuesta
aquí; no es una idea improvisada, coincide con lo que la otra persona asumió
de forma independiente. El problema no es conceptual sino de contrato: ese
`EstadoRuta` es un dataclass propio que **no existe** en `core/models.py` y no
lo produce `CourierStateManager.snapshot()` (que devuelve `CourierState`, con
`route: list[RouteStop]`, sin campos `driver_current_position` ni
`active_leg_destination` separados). Es decir, el optimizador de Bloque 4 hoy
solo corre contra sus propios mocks (`if __name__ == "__main__"` al final del
archivo), no contra el estado real. Bloque 3 no necesita resolver esto — pero si
Bloque 3 termina antes, este es el primer punto de integración roto que hay que
señalar al equipo (alguien va a necesitar un adaptador `CourierState -> EstadoRuta`
antes de la Fase 3).

---

## 6. Evaluación de umbral

Reglas de aceptación, en orden (la primera que rechaza, corta la evaluación):

1. **Factibilidad de tiempo de turno**: `extra_time <= estado.time_remaining`. Si
   no, rechazo automático ("no queda tiempo en el turno").
2. **Ventana de tiempo de la oferta**: ya la valida `cheapest_insertion` (sección
   4.3); si no es insertable en ninguna posición dentro de su `time_window`,
   rechazo.
3. **Excepción de costo marginal ~0** (regla de frozen horizon): si
   `extra_time` es prácticamente cero (está literalmente en el camino), se
   acepta sin más evaluación — es la única forma de insertar antes de
   `frozen_index`.
4. **Umbral mínimo $/km**: `offer.pay / extra_distance_km >= MIN_PAY_PER_KM`
   (constante ya presente en `decision.py`). Si `extra_distance_km ≈ 0` pero no
   cae en el caso 3, tratar como caso especial para no dividir por cero.
5. **Señal de demanda histórica** (ver sección 7) como ajuste: en zonas de alta
   demanda se puede relajar el umbral (p. ej. `MIN_PAY_PER_KM * (1 - demand_score * DEMAND_DISCOUNT)`),
   en zonas frías se mantiene estricto. Parámetro `DEMAND_DISCOUNT` configurable,
   junto a `MIN_PAY_PER_KM`, para la fase de ajuste (Fase 4 del cronograma).

Todas las constantes deben quedar como variables nombradas al inicio de
`decision.py` (ya existe `MIN_PAY_PER_KM`), documentadas como "decisión
justificada, no arbitraria" tal como pide `docs/02_Documentacion_Tecnica.md`
sección 5 — para poder defenderlas si el jurado pregunta.

---

## 7. Señal de demanda histórica — dueño no asignado

El reparto de tareas la pone dentro de Bloque 3 ("señal de demanda histórica de
la zona"), pero el contrato `Offer` no trae ningún campo de zona/demanda, y quien
tiene el dataset crudo (Solomon/Kaggle) es Persona A (Bloque 1, generador de
ofertas).

**Propuesta de default para arrancar ya:** Persona B agrega
`core/agent/demand.py` con una interfaz mínima:

```python
class DemandSignal(Protocol):
    def zone_score(self, location: tuple[float, float]) -> float: ...  # 0.0-1.0
```

y una implementación stub (`StaticDemandSignal`) que devuelve un valor constante
o un lookup por grilla simple con datos de ejemplo, para no bloquear el resto del
motor de decisión. Se reemplaza más adelante por una versión real si el tiempo
alcanza (ver `docs/02_Documentacion_Tecnica.md` sección 6, extensiones posibles).

**Pregunta para el checkpoint:** ¿Persona A calcula esta señal al generar el
stream de eventos (y la añade a algo que reciba Bloque 3), o se queda en Bloque 3
leyendo el dataset por su cuenta? Mientras no se confirme, se avanza con el stub
propio para no bloquear.

**Actualización (revisión 2):** `core/simulation/engine.py` (Bloque 1) ya está
implementado y confirma que la pregunta sigue abierta: genera ofertas eligiendo
`pickup`/`dropoff` **uniformemente al azar** entre 4 zonas fijas, sin ninguna
señal de demanda ni ponderación por zona todavía, y `data/kaggle_orders.csv`
sigue sin existir en el repo (`data/README.md` lo marca como descarga manual
pendiente). Es decir, hoy no hay ninguna señal de demanda histórica en ningún
lado del código — el stub de Bloque 3 no es solo el default razonable, es
**la única fuente de esa señal que existe ahora mismo**.

Para no inventar zonas nuevas, `StaticDemandSignal` arranca con las mismas 4
zonas que ya usa `engine.py` (mismas coordenadas, para que el mapa del
dashboard y el log de explicabilidad hablen de los mismos lugares):

```python
_ZONES = {
    "Tec": (25.651, -100.289),
    "San Pedro": (25.657, -100.402),
    "Centro": (25.680, -100.310),
    "Apodaca": (25.780, -100.180),
}
```

con un score inicial arbitrario por zona (ej. Centro/Tec más "calientes" que
Apodaca), documentado como valor a calibrar en Fase 4, igual que
`MIN_PAY_PER_KM`. Si Persona A reubica o cambia estas coordenadas después, el
stub de Bloque 3 se actualiza en un solo lugar (`core/agent/demand.py`), sin
tocar `decision.py`.

---

## 8. Explicabilidad / logging

`llm_log.py` ya tiene `format_decision_log(accepted, offer_id, reason) -> str`
funcional — no necesita cambios de firma. Lo que falta es que `DecisionEngine`
construya un `reason` con números concretos, por ejemplo:

- Rechazo por umbral: `"desvío de {extra_time:.0f} min por ${offer.pay:.0f} en zona fría (score {demand_score:.2f})"`.
- Rechazo por tiempo de turno: `"quedan {time_remaining:.0f} min, se necesitan {extra_time:.0f}"`.
- Aceptado por estar en el camino: `"desvío ≈0, va en la ruta hacia {next_stop}"`.

Esto es lo que se muestra en vivo en el feed del dashboard (Bloque 6) y lo que se
usa para responder la "pregunta sorpresa" del jurado — el texto debe ser
autoexplicativo sin tener que leer el código.

---

## 9. Plan de implementación (orden sugerido)

| # | Tarea | Depende de | Estado |
|---|---|---|---|
| 1 | Checkpoint de equipo: confirmar `DistanceProvider`/fragmentación con Bloque 4 (sección 3), `frozen_index` (sección 5) y dueño de demand signal (sección 7) | — | Pendiente (se avanzó igual con los defaults propuestos, sin bloquear) |
| 2 | `core/routing/distance_provider.py` (Protocol) + `core/routing/euclidean.py` (placeholder) | 1 | **Hecho** — ver `docs/Bloque 3/doc-scripts/distance_provider.md` y `euclidean.md` |
| 3 | `core/agent/demand.py` (Protocol + stub, con las 4 zonas de `engine.py`) | 1 | **Hecho** — ver `docs/Bloque 3/doc-scripts/demand.md` |
| 4 | Implementar `cheapest_insertion` en `greedy.py` usando el provider | 2 | **Hecho** — ver `docs/Bloque 3/doc-scripts/greedy.md` |
| 5 | Implementar `DecisionEngine.evaluate` en `decision.py` (umbral + frozen horizon + demand + logging + `state_manager.accept_offer`) | 3, 4 | **Hecho** — ver `docs/Bloque 3/doc-scripts/decision.md` |
| 6 | Tests unitarios (sección 10) | 5 | **Hecho** — `test_euclidean.py`, `test_demand.py`, `test_greedy.py`, `test_decision.py` (24 tests en verde) |
| 7 | Smoke test de integración real: `SimulationEngine.event_stream()` (Bloque 1, ya implementado) → `DecisionEngine.evaluate` → `CourierStateManager` (Bloque 2, ya implementado), sin mocks, para validar el bloque contra el resto del sistema real que ya existe hoy | 5 | **Hecho** — `test_decision_integration.py` |
| 8 | Integración Fase 3: swap a `RoadNetwork` real cuando Persona C lo tenga; conectar con `StabilityController.on_offer_accepted()` tras cada aceptación; avisar al dueño de Bloque 4 del hallazgo de la sección 5 (adaptador `CourierState -> EstadoRuta` pendiente) | Bloque 5 listo | Pendiente (bloqueado por Bloque 5) |

Los pasos 2–7 se pueden hacer en paralelo al resto del equipo sin esperar a
nadie más — el paso 7 en particular ya no es hipotético: Bloque 1 y 2 están
implementados de verdad hoy, así que ese smoke test es alcanzable ahora, no
solo en la Fase 3 de integración del cronograma general.

---

## 10. Plan de pruebas

Siguiendo el estilo de `backend/tests/test_state.py` (pytest simple, sin mocks
pesados):

- `tests/test_euclidean.py`: distancia/tiempo simétricos, cero entre el mismo
  punto, orden de magnitud razonable para coordenadas de Monterrey.
- `tests/test_greedy.py`:
  - ruta vacía → inserta al final.
  - elige la posición de menor costo entre ≥2 opciones.
  - respeta `frozen_index` (no inserta antes salvo costo ~0).
  - detecta cuando ninguna posición respeta `time_window`.
- `tests/test_decision.py`:
  - acepta oferta claramente rentable y sin conflicto de tiempo → llama a
    `state_manager.accept_offer` y el log dice "Aceptado".
  - rechaza por `$/km` insuficiente → no muta el estado, log explica el motivo.
  - rechaza por falta de tiempo de turno.
  - acepta por excepción de costo marginal ~0 aunque el pago sea bajo.
  - usa un `DemandSignal` fake para verificar que el score mueve el umbral.
- `tests/test_decision_integration.py` (nuevo, habilitado por el estado actual
  del repo): instancia `SimulationEngine(seed=..., shift_duration=...)` y
  `CourierStateManager` reales (sin mocks, igual que `test_engine.py` y
  `test_state.py` ya hacen por separado), corre `DecisionEngine.evaluate` sobre
  cada `Offer` del stream y verifica invariantes de extremo a extremo: el
  `version` del estado nunca retrocede, `earnings` solo sube cuando el log dice
  "Aceptado", y el turno completo corre sin excepciones sobre datos reales de
  Bloque 1.

---

## 11. Riesgos y preguntas abiertas (llevar al checkpoint del equipo)

1. ¿`graph.py` va a exponer `travel_distance` además de `travel_time`, con la
   firma del `DistanceProvider` propuesto en la sección 3? (Persona C)
2. ¿`frozen_index = 1 if route else 0` coincide con lo que asume
   `ortools_optimizer.py` como "punto fijo" del tramo comprometido? (Persona C)
3. ¿Quién calcula la señal de demanda histórica — Persona A al generar el
   stream, o Persona B leyendo el dataset directamente? (Persona A)
4. `MIN_PAY_PER_KM` y `DEMAND_DISCOUNT` son placeholders — deben calibrarse con
   corridas reales en la Fase 4 del cronograma, no quedarse en el valor inicial.
5. El "score de demanda ~0-1" debe tener la misma escala que espere quien
   consuma logs/dashboard (Persona D), si se llega a mostrar en el feed.
6. **(Nuevo, confirmado en el repo)** `core/optimization/ortools_optimizer.py`
   (Bloque 4) usa un modelo de datos propio (`Node`/`EstadoRuta`/`RutaOptimizada`)
   que no es compatible con `core/models.py` ni con `CourierStateManager` reales
   — hoy solo corre contra sus propios mocks. No es responsabilidad de Bloque 3
   arreglarlo, pero si Bloque 3 llega primero a la Fase 3, hay que avisarlo en
   el checkpoint antes de que alguien asuma que ya está integrado. (dueño: quien
   escribió ese archivo, a confirmar quién es en el equipo)
7. **(Nuevo)** `core/routing/ortools_optimizer.py` (la ubicación original de
   Bloque 4 según `docs/01_Arquitectura.md` sección 7) sigue siendo un stub sin
   tocar — hay dos rutas de código para "el optimizador" en el repo ahora mismo.
   Vale la pena que el equipo decida cuál es la real antes de la Fase 3, para
   que Bloque 3 sepa contra cuál probar la integración cuando llegue el momento.

---

## 12. Definición de "terminado" para Bloque 3

- `DecisionEngine.evaluate(offer)` implementado, sin `NotImplementedError`,
  cumpliendo las 4 reglas de la sección 3 de `docs/01_Arquitectura.md`
  (inserción, umbral, frozen horizon, explicabilidad).
- Funciona contra `EuclideanDistanceProvider` de forma aislada (sin depender de
  que Bloque 5 esté terminado) — cumple el requisito del reparto de tareas de
  "arrancar con distancias euclidianas de placeholder".
- Tests de la sección 10 en verde (`pytest` desde `backend/`), incluyendo el
  smoke test de integración real contra `SimulationEngine` + `CourierStateManager`.
- Constantes de umbral documentadas (comentario corto con la justificación,
  no un valor mágico suelto).
- Las preguntas abiertas de la sección 11 resueltas o explícitamente
  pospuestas con el equipo (no asumidas en silencio) — en particular las
  nuevas #6 y #7, que son hallazgos de esta revisión y no estaban en la v1.
