# Integración con `POST /decide` — qué necesita cada bloque para encajar

Para: **Abraham** (P0.1, P0.2, P0.5, P0.6, P1.1) y **Omar** (P0.3, P1.4, P1.5, P1.6, P2.1).

`POST /decide` (P0.4/P0.7, Adriana) está implementado y en verde contra
`validate_format.py --endpoint` — ver [`backend/api/decide.py`](../backend/api/decide.py),
[`backend/api/schemas.py`](../backend/api/schemas.py),
[`backend/core/agent/safety.py`](../backend/core/agent/safety.py) y
[`backend/core/agent/economics.py`](../backend/core/agent/economics.py).

**Actualización (merge de `abraham/bloques-1-2` a `B5`):** P0.1, P0.2, P0.5,
P0.6 y P1.1 ya están implementados por Abraham y mezclados con este trabajo.
Se reconciliaron dos cosas antes de dar por buena la mezcla (sección 1), y el
mismatch de nombres de evento de P1.1 que se había quedado pendiente ya se
corrigió y se verificó contra el validador oficial real (sección 1, al
final).

## 0. Lo más importante: `/decide` es autocontenido, no llama a `CourierStateManager`

Cada request trae su propio `sim_time` (ISO 8601) y, opcionalmente,
`courier_state_overrides` (`continuous_riding_min`, `shift_elapsed_hours`,
`shift_end_time`, `in_flight_orders`, ...). El endpoint calcula todo a partir
de eso — **no lee el estado en memoria de `CourierStateManager`** ni depende
de que un turno esté corriendo. Así es como el protocolo de evaluación prueba
casos de frontera sin orquestar un turno completo (ver
`student-materials/courier/evaluation_protocol.md`, sección 2).

Esto sigue siendo cierto después del merge: `CourierStateManager.tick()` de
Abraham (P0.1) ya funciona (posición interpolada, paradas que se retiran,
`sim_time`/`time_remaining` reales), pero es para el **loop interno de un
turno completo** (coordenadas, `float` minutos) — un mundo distinto del que
usa `/decide` (zonas enteras, `datetime` ISO). No hay puente entre los dos
todavía; ver más abajo.

## 1. Abraham — P0.1/P0.2/P0.5/P0.6/P1.1: qué se reconcilió al mezclar

**`VehicleProfile` estaba duplicado.** Antes del merge existían dos clases
`VehicleProfile` distintas: la tuya en `core/models.py`
(`avg_speed_kmh`, `max_backpack`, `cost_per_km` — para el motor VRPTW
interno) y un placeholder mío en `core/agent/vehicle_profiles.py`
(`speed_kmh`, `weight_limit_kg`, `volume_limit_liters` — para la constraint
`vehicle_capacity` de `/decide`). Se consolidaron en una sola: **extendí tu
`VehicleProfile` en `core/models.py`** con los dos campos que le faltaban
(`weight_limit_kg`, `volume_limit_liters`) y borré el placeholder. Ahora
`core/agent/safety.py` y `api/decide.py` importan `VEHICLE_PROFILES` /
`VehicleType` directamente de `core.models` — única fuente de verdad, como
ya dice el docstring del archivo. Si recalibras velocidades/capacidad de
mochila, aprovecha para revisar también esos dos campos nuevos.

**`ZoneMap`/`Zone` no tenían id entero.** El contrato oficial identifica
zonas con enteros (`zone_pickup: int`, `zone_dropoff: int`); tu `Zone` solo
tenía `name` (string) y `coord`. Agregué `zone_id: int` a `Zone` (0=Tec,
1=San Pedro, 2=Centro, 3=Apodaca, mismo orden que `_DEFAULT_ZONES`) y
`ZoneMap.by_id(zone_id)`. Es el puente para cuando algo necesite resolver el
`zone_pickup`/`zone_dropoff` de un request de `/decide` a una coordenada real
de tu `ZoneMap` — hoy `/decide` no lo usa (ver más abajo), pero
`core/agent/safety.py` sí: `FLAGGED_ZONES` ahora apunta a
`DEFAULT_ZONE_MAP.zone_by_name("Centro").zone_id` (zone_id=2) en vez de un
entero inventado. **Si cambias el orden o el número de `_DEFAULT_ZONES`,
`FLAGGED_ZONES` se recalcula solo** (no hay id hardcodeado), pero confírmalo.

**`_order_total_time_min` en `api/decide.py` sigue sin usar tu `ZoneMap`/
`DistanceMatrix`.** Usa `distance_pickup_km`/`distance_delivery_km` tal como
llegan en el request (así lo pide el contrato oficial) + velocidad plana del
`VehicleProfile` — no resuelve `zone_pickup`/`zone_dropoff` a coordenadas.
Es deliberado: un juez puede mandar cualquier entero de zona, no
necesariamente el mismo universo de 4 zonas que genera tu simulador interno.
Si más adelante quieren que el loop real de un turno complete alimente
`/decide` con las zonas que tu `SimulationEngine` genera, ese es el punto
para engancharlo, pero no es necesario para pasar `validate_format.py`.

**P0.6 (validar ventanas tras recalcular ETAs):** confirmado, vive en
`CourierStateManager.validate_windows()`/`tick()` — no afecta a `/decide`.

**P1.1 (event log JSONL) — ya resuelto, verificado contra el validador real.**
`EventType` y `SimulationEngine._log*` en `core/simulation/engine.py` ahora
emiten exactamente los 8 tipos oficiales, planos (`{"event": ..., "sim_time":
..., ...}`, no anidados bajo `payload`), con `sim_time` en ISO 8601 (ancla
determinista `DEFAULT_SHIFT_START_TIME`, nunca `datetime.now()`) y
`zone_pickup`/`zone_dropoff` resueltos a `Zone.zone_id` vía
`zone_map.nearest_zone(coord)`. Verificado con
`tests/test_abraham.py::TestEventLog::test_log_passes_official_validator`,
que corre el `validate_format.py` real (no una copia) contra un log
generado — y a mano:

```bash
python3 student-materials/courier/validate_format.py --event-log <tu_log.jsonl>
# events: order_offered=84, shift_end=1, shift_start=1, shock=24
# PASS  output conforms to the required formats
```

**Lo que quedó deliberadamente fuera de este fix** (serían features nuevas,
no una corrección de nombres):
- `position_update`, `earnings_update`, `strategy_update` — nadie los emite
  todavía. Le corresponden a `CourierStateManager`/`DecisionEngine` (P0.1/
  Bloque 3), que sí saben posición/ganancias/estrategia; `SimulationEngine`
  por sí solo no tiene esa información.
- `distance_pickup_km` en `order_offered` queda en `0.0` (el generador no
  conoce la posición real del courier, solo genera el stream). Cuando exista
  un loop que una `SimulationEngine` con `CourierStateManager`, ese es el
  punto para calcular el deadhead real.
- `surge_multiplier` queda fijo en `1.0` — no se propaga un surge activo de
  un `shock` anterior a las `order_offered` posteriores en la misma zona.
- `log_stop_completed`/`log_route_optimized` se eliminaron (sin equivalente
  oficial, nada en producción los llamaba). Si Bloque 4 necesita loguear una
  ruta reoptimizada, no hay evento oficial para eso — no inventarlo, dejarlo
  fuera del JSONL.

## 2. Omar — P0.3 (safety.py), P1.4 (explain_decision), P1.5 (capacidad), P2.1 (degradado)

**P0.3 ya tiene una primera versión funcional**, no un stub, en
`backend/core/agent/safety.py`: 5 funciones puras (`check_vehicle_capacity`,
`check_mandatory_break`, `check_heat_rule`, `check_flagged_zone_night`,
`check_shift_end_infeasible`) + `evaluate_safety(...)` que corre las 5 en
orden y devuelve la primera violación. Se hizo así para no bloquear
`/decide`, con la idea explícita de que la refines tú. Si la reescribes,
conserva:

- La firma de `evaluate_safety(...)` (los kwargs que recibe) — `api/decide.py`
  la llama tal cual.
- Los strings exactos de `constraint` (`flagged_zone_night`,
  `mandatory_break`, `heat_rule`, `shift_end_infeasible`,
  `vehicle_capacity`) — son el enum que valida `validate_format.py`, no se
  pueden cambiar sin romper el formato.
- El import de `VEHICLE_PROFILES`/`VehicleType` desde `core.models` (ya no
  existe `core/agent/vehicle_profiles.py`, se consolidó ahí — ver sección 1).

**`FLAGGED_ZONES` ya no es un entero inventado** — apunta a la zona "Centro"
real de `DEFAULT_ZONE_MAP` (Abraham, P0.2). Sigue siendo una elección
arbitraria (la zona de mayor `demand_score`), no un criterio real de riesgo
nocturno — cámbialo si el equipo define uno mejor.

**P1.4 (`explain_decision`):** también hay una primera versión en
`GET /explain_decision/{order_id}` (`backend/api/decide.py`), con log en
memoria (`_decision_log`, se vacía al reiniciar el proceso — para replay
real necesita respaldarse en el event log JSONL de P1.1, no antes, y P1.1
todavía no pasa el formato oficial — ver sección 1). **Ojo:** la sugerencia
del roadmap de "reusar lo que `greedy.py` ya evalúa" aplica al motor de
coordenadas (`decision.py`), pero `/decide` **no llama a `greedy.py`** — es
una evaluación de zonas + economía, no una inserción VRPTW. Si quieres
enriquecer `alternatives_considered` (hoy es un solo `{option,
rejected_because}`), el lugar es dentro de la función `decide()` en
`api/decide.py`, no dentro de `greedy.py`.

**P1.5 (límite de mochila) — son DOS constraints distintas, no una:**
1. **Capacidad por vehículo, una sola orden** (peso/volumen vs. el vehículo
   asignado) — ya cubierta por `check_vehicle_capacity` en `safety.py`, la
   consume `/decide`.
2. **Mochila acumulada del turno** (hallazgo 3 de la auditoría: 55 órdenes
   aceptadas, 200+ paradas activas, sin límite agregado) — esa vive en
   `CourierState.backpack` / `core/agent/decision.py` (el motor de
   coordenadas del loop interno). Abraham agregó `max_backpack` al
   `VehicleProfile` (P0.5) pero **nadie todavía lo hace cumplir** en
   `decision.py`/`greedy.py` — el número existe, falta el `if` que lo use.
   No asumas que `safety.py` ya la resuelve.

**P2.1 (modo degradado):** hoy `/decide` siempre devuelve `tier: "tier1"`,
`degraded: false` porque no existe capa tier2 (ni LLM, ni red). El punto de
enganche es `core/agent/economics.py`: `BASE_RESERVATION_WAGE_MXN_HR` es hoy
una constante fija; cuando exista una capa tier2 que la ajuste dinámicamente
(vía red), si esa llamada falla, `api/decide.py` debe seguir usando el
último valor conocido y devolver `degraded: true` en vez de bloquear la
respuesta — nunca esperar a la red dentro de la ventana de decisión
(presupuesto de 50ms).

## 3. Contrato a no romper sin avisar

Estos nombres los importa `api/decide.py` / `core/agent/safety.py`
directamente — si los cambias, actualiza ambos:

| Símbolo | Archivo |
|---|---|
| `VEHICLE_PROFILES`, `VehicleProfile`, `VehicleType` | `core/models.py` (Abraham, P0.5) |
| `DEFAULT_ZONE_MAP`, `ZoneMap`, `Zone.zone_id`, `ZoneMap.by_id_or_none` | `core/models.py` (Abraham, P0.2) |
| `evaluate_safety`, `SafetyViolation` | `core/agent/safety.py` |
| Los 6 strings de `binding_constraint` (5 safety + `reservation_wage`) | `core/agent/safety.py` + `core/agent/economics.py` |
| `evaluate_economics`, `EconomicsResult`, `BASE_RESERVATION_WAGE_MXN_HR`, `DEMAND_DISCOUNT` | `core/agent/economics.py` |

## 4. Actualización 13 sep — 3 fixes de Persona 1 (simulación)

**ZoneMap ampliado de 4 a 16 zonas** (`core/models.py`). Los ejemplos
ilustrativos del material oficial usan `zone_pickup`/`zone_dropoff` hasta 11
(`decision_response_schema.json`); con solo 0-3, cualquier id oficial >=4
caía fuera de nuestro universo de zonas. Consecuencias:
- `ZoneMap.by_id_or_none(zone_id) -> Zone | None` (nuevo) — no lanza para un
  id desconocido, a diferencia de `by_id`. `api/decide.py::_zone_demand()`
  lo usa para resolver `zone_pickup` a un `demand_score`, con fallback
  `NEUTRAL_DEMAND_SCORE=0.5` y `zone_pickup_known: false` explícito en
  `explain_decision.inputs` cuando la zona no es nuestra — nunca falla
  callado.
- `FLAGGED_ZONES` en `safety.py` pasó de una zona (Centro) a tres (Centro,
  Parque Industrial=11, Linda Vista) — con solo Centro marcada,
  `flagged_zone_night` casi nunca se disparaba contra los ids que usan los
  ejemplos oficiales (un juez podía mandar `zone_dropoff: 11` a las 23:00 y
  siempre aceptábamos; verificado que ahora sí rechaza).

**La demanda por zona ya se conecta a la economía** (`core/agent/economics.py`).
Antes `evaluate_economics` ignoraba `demand_score` a propósito (comentario:
"Bloque 1 no expone esa señal sobre zonas enteras") — ya no es cierto,
`ZoneMap` trae `demand_score` desde el principio. `reservation_wage_mxn_hr`
en la respuesta de `/decide` ahora es el salario de reserva YA ajustado por
demanda de la zona de pickup (`BASE_RESERVATION_WAGE_MXN_HR * (1 -
demand_score * DEMAND_DISCOUNT)`, mismo criterio que `DEMAND_DISCOUNT` en
`core.agent.decision`), no la constante plana. **Renombrado:**
`RESERVATION_WAGE_MXN_HR` → `BASE_RESERVATION_WAGE_MXN_HR` (el nombre viejo
ya no existe — quien lo importe directamente se rompe).

**Shocks con efecto económico real** (`core/simulation/engine.py`). Un
`surge` se emitía al log y ahí quedaba, sin afectar nada — el brief exige un
shock en vivo durante la demo y no tenía efecto visible. Ahora
`SimulationEngine._active_surges` trackea el multiplicador vigente por zona
(`SURGE_DURATION_MIN=30` min, placeholder) y lo aplica tanto a `Offer.pay`
(efecto real sobre lo que evalúa el motor de decisión) como al
`surge_multiplier` logueado en `order_offered` (antes fijo en `1.0`).
`base_pay_mxn` en el log sigue sin premultiplicar, tal como pide
`event_log_schema.json` (campos separados).

**Hallazgo 1 de `greedy.py` — corregido.** `cheapest_insertion` solo
validaba la ventana de tiempo de la oferta NUEVA; nunca revisaba si el
corrimiento en cadena de ETAs sacaba a una parada YA ACEPTADA de su propia
ventana. `_downstream_still_feasible` (nuevo) descarta toda posición
candidata que rompería la ventana de una parada posterior — verificado con
una regresión que reproduce el escenario exacto de la auditoría (revertí el
fix, confirmé que el test nuevo falla, lo restauré).

Cualquier duda, correr esto siempre debe seguir en verde:

```bash
cd backend && source .venv/bin/activate
pytest -q
uvicorn main:app --port 8000 &
python3 ../student-materials/courier/validate_format.py --endpoint http://localhost:8000/decide
```
