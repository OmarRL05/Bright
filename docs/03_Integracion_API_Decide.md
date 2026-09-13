# Integración con `POST /decide` — qué necesita cada bloque para encajar

Para: **Abraham** (P0.1, P0.2, P0.5, P0.6, P1.1) y **Omar** (P0.3, P1.4, P1.5, P1.6, P2.1).

`POST /decide` (P0.4/P0.7, Adriana) está implementado y en verde contra
`validate_format.py --endpoint` — ver [`backend/api/decide.py`](../backend/api/decide.py),
[`backend/api/schemas.py`](../backend/api/schemas.py),
[`backend/core/agent/safety.py`](../backend/core/agent/safety.py) y
[`backend/core/agent/economics.py`](../backend/core/agent/economics.py).

**Actualización (merge de `abraham/bloques-1-2` a `B5`):** P0.1, P0.2, P0.5,
P0.6 y P1.1 ya están implementados por Abraham y mezclados con este trabajo.
Se reconciliaron dos cosas antes de dar por buena la mezcla (sección 1) y
queda **un mismatch real sin resolver** en P1.1 (sección 1, al final) —
léanlo antes de asumir que el event log ya pasa el validador oficial.

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

**P1.1 (event log JSONL) — mismatch real, sin resolver todavía:** tu
`EventType` (`tick`, `offer_received`, `offer_accepted`, `offer_rejected`,
`stop_completed`, `road_event`, `shift_end`, `route_optimized`) **no
corresponde** a los 8 tipos que exige
`student-materials/courier/event_log_schema.json`
(`shift_start`, `order_offered`, `decision`, `position_update`,
`earnings_update`, `shock`, `strategy_update`, `shift_end`) — ni los nombres
ni, en el único que coincide por casualidad (`shift_end`), los campos
requeridos. Lo comprobé corriendo tu `SimulationEngine` directamente:
ahora mismo, un log tuyo pasado a
`validate_format.py --event-log` fallaría en **cada línea** con "unknown
event type". No lo reescribí yo — es tu diseño y sigue siendo tu tarea
(P1.1) — pero es un bloqueador de puntos duro (Feasibility/Clarity dependen
de esto) y conviene que lo sepas antes de darlo por terminado. El mapeo
tentativo, para cuando lo ataques:

| Tu `EventType` | Evento oficial más cercano | Nota |
|---|---|---|
| (ninguno) | `shift_start` | falta emitirlo al inicio del turno |
| `offer_received` | `order_offered` | nombre y campos distintos (`zone_pickup`/`zone_dropoff` enteros, no `pickup`/`dropoff` coordenadas) |
| `offer_accepted` / `offer_rejected` | `decision` | el oficial es uno solo con `decision: ACCEPT\|SKIP`, mismos campos que `DecideResponse` (ver sección 0 de la versión anterior de este doc — siguen coincidiendo) |
| `road_event` | `shock` | `shock_type` en vez de `type`, enum distinto |
| `tick` | (ninguno) | no existe en el oficial; `position_update`/`earnings_update` sí, y tú no los emites |
| `stop_completed`, `route_optimized` | (ninguno) | internos, no rompen el validador si no se llaman así — pero tampoco cubren `position_update`/`earnings_update`, que sí son requeridos |
| `shift_end` | `shift_end` | coincide el nombre, no los campos (`orders_offered`, `orders_completed`, `earnings_mxn`, `safety_violations`) |

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
enganche es `core/agent/economics.py`: `RESERVATION_WAGE_MXN_HR` es hoy una
constante fija; cuando exista una capa tier2 que la ajuste dinámicamente
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
| `DEFAULT_ZONE_MAP`, `ZoneMap`, `Zone.zone_id` | `core/models.py` (Abraham, P0.2) |
| `evaluate_safety`, `SafetyViolation` | `core/agent/safety.py` |
| Los 6 strings de `binding_constraint` (5 safety + `reservation_wage`) | `core/agent/safety.py` + `core/agent/economics.py` |
| `evaluate_economics`, `EconomicsResult`, `RESERVATION_WAGE_MXN_HR` | `core/agent/economics.py` |

Cualquier duda, correr esto siempre debe seguir en verde:

```bash
cd backend && source .venv/bin/activate
pytest -q
uvicorn main:app --port 8000 &
python3 ../student-materials/courier/validate_format.py --endpoint http://localhost:8000/decide
```
