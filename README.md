# The Courier — HackMTY 2026 (Infosys)

Simulación del turno de un repartidor en Monterrey donde un agente de IA
decide en tiempo real aceptar/rechazar ofertas y re-optimiza su ruta ante
cambios del entorno, comparado contra baselines nombrados sobre el mismo
stream de eventos.

- Arquitectura y decisiones técnicas: [`docs/01_Arquitectura.md`](docs/01_Arquitectura.md),
  [`docs/02_Documentacion_Tecnica.md`](docs/02_Documentacion_Tecnica.md)
- Integración del contrato oficial: [`docs/03_Integracion_API_Decide.md`](docs/03_Integracion_API_Decide.md)
- **Resultados, metodología y lo que no funcionó**: [`docs/Bloque 3/RESULTADOS.md`](docs/Bloque%203/RESULTADOS.md)

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, FastAPI, WebSockets nativo |
| Ruteo / optimización | Google OR-Tools (VRPTW), OSMnx + NetworkX |
| Capa de estrategia (tier2) | API de Claude (`claude-opus-5`), fuera de la ventana de decisión |
| Frontend | Next.js 16 (App Router) + TypeScript + Tailwind CSS |
| Estado | En memoria, con locks + versionado optimista (sin base de datos externa) |

## Estado real por bloque

Esta tabla es el estado **de hoy**, no el del scaffold inicial. Un `TODO`
aquí es un TODO de verdad.

| Bloque | Archivos | Estado |
|---|---|---|
| 1 — Simulador | `core/simulation/engine.py` | ✅ Stream reproducible por seed, 8 tipos de evento oficiales, event log JSONL |
| 2 — Estado | `core/simulation/state.py` | ✅ Locks, versión optimista, `tick()`, posición interpolada, `validate_windows()` |
| 3 — Decisión (coordenadas) | `core/agent/decision.py`, `core/routing/greedy.py` | ✅ Cheapest insertion + frozen horizon + umbral $/km |
| 3 — Decisión (contrato oficial) | `api/decide.py`, `core/agent/safety.py`, `economics.py`, `reasons.py`, `journal.py` | ✅ Fast path completo, 5 constraints, explicabilidad |
| — Shocks en vivo | `core/agent/shocks.py`, `POST /shock` | ✅ Los 4 tipos, con efecto medible en la decisión siguiente |
| — Estrategia tier2 | `core/agent/strategy.py` | ✅ `ClaudeAdvisor` + modo degradado + recuperación |
| — Evaluación | `core/evaluation/`, `scripts/run_evaluation.py` | ✅ 6 políticas, seeds disjuntas, CSV del template |
| 4 — Optimizador global | `core/optimization/ortools_optimizer.py`, `core/routing/ortools_optimizer.py` | ⚠️ VRPTW funcional, pero **dos rutas de código** con modelos de datos distintos; ninguna conectada al estado real |
| 5 — Grafo vial | `core/routing/graph.py` | ✅ OSMnx + NetworkX con cierres/tráfico — requiere descargar `monterrey.graphml` (ver `backend/data/README.md`) |
| 6 — API de simulación | `api/routes.py`, `api/sockets.py` | ❌ `NotImplementedError`: arrancar/pausar el turno y el WebSocket de estado siguen sin implementarse — el dashboard no depende de ninguno de los dos, ver más abajo |
| 6 — Frontend | `frontend/src/` | ✅ Feed, métricas y mapa reales por REST (`useAgent`, `useZones`) — cero datos inventados. Mapa: Leaflet + `GET /zones`, ruta reconstruida de `GET /decisions`/`GET /replay/{seed}`, shocks en vivo de `GET /shocks` |

## Quickstart

### Backend

**Usa Python 3.12 exacto**, no la última que tengas instalada.
`backend/.python-version` lo fija; créala explícitamente con esa versión:

```bash
cd backend

# macOS (Homebrew)
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate

# Windows (py launcher)
py -3.12 -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

Health check: `http://localhost:8000/health`. Tests: `pytest`.

> **Por qué la versión exacta importa:** `requirements.txt` fija versiones
> (`pydantic==2.10.4`, `ortools==9.11.4210`, ...) que solo publican wheel
> precompilado hasta **cp312**. Con Python 3.13/3.14 `pip` intenta compilar
> `pydantic-core` desde fuente (necesita Rust) y `ortools` no tiene wheel que
> compilar (necesita Bazel + toolchain de C++) — falla en Windows y Mac por
> igual. No es un problema de plataforma sino de versión de Python.
>
> **Nota sobre `osmnx`**: instala varias dependencias geoespaciales
> (geopandas, shapely, pyogrio). Si falla, actualiza `pip` antes de reintentar.

`ANTHROPIC_API_KEY` es **opcional**: sin ella el sistema corre entero y decide
igual de bien, solo que tier2 nunca propone nada. Con ella, quitarla a media
corrida es lo que dispara el modo degradado — que es justo lo que los jueces
prueban (ver "Modo degradado" abajo).

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Abre `http://localhost:3000`. Con el backend arrancado pero sin turno
corriendo, vas a ver las 16 zonas en el mapa y el feed vacío — es el estado
real, no un error. Dos formas de poblarlo:

- **En vivo**: manda pings a `/decide` (`python3 backend/scripts/demo.py`, o
  a mano con `curl`) y el feed/mapa se refrescan solos cada 1.5 s.
- **Grabado**: `python3 backend/scripts/run_evaluation.py --event-log
  backend/logs/replay_seed_101.jsonl --seed 101` y elígelo en "Turnos
  grabados" — dibuja la ruta completa del turno de una vez.

> **Nada que instalar aparte:** el mapa usa `leaflet`/`react-leaflet` (ya en
> `package.json`, sin llave de API — tiles de CartoDB, gratis). El único paso
> manual real es el `.env.local` de arriba; si ya tenías uno de antes de este
> cambio, bórralo y vuelve a copiarlo — la plantilla anterior traía
> `NEXT_PUBLIC_API_URL` con un `/api` que no corresponde a ningún endpoint
> real (ver tabla de abajo) y deja el dashboard entero pegado en "sin backend".

## Endpoints

| Método | Ruta | Para qué |
|---|---|---|
| `POST` | `/decide` | El fast path. Contrato oficial, presupuesto de 50 ms |
| `GET` | `/explain_decision/{order_id}` | "¿Por qué saltaste ese pedido?" desde la bitácora |
| `POST` | `/shock` | Inyección de shock en vivo (`surge`, `closure`, `rain`, `delay`) |
| `GET` | `/shocks` | Qué shocks están vigentes (`?at=<sim_time ISO>`) |
| `DELETE` | `/shocks` | Limpia los vigentes entre pasadas del ensayo |
| `GET` | `/status` | Salud del servicio, incluido `degraded` |
| `GET` | `/decisions?limit=N` | Feed del dashboard, con `zone_pickup`/`zone_dropoff` para el mapa |
| `GET` | `/zones` | Las 16 zonas (`zone_id`, `coord`, `demand_score`, `flagged`) — de aquí sale el mapa |
| `GET` | `/replays` / `/replay/{seed}` / `/replay/{seed}/summary` | Turnos grabados, en JSONL crudo |
| `GET` | `/health` | Liveness |

Ninguno de estos va bajo el prefijo `/api`: el material del reto golpea
`http://host:puerto/decide` directo, y el resto sigue la misma convención
por consistencia. `/api/simulation/*` (en `api/routes.py`) es la excepción —
y sigue sin implementarse.

## Verificación — las tres banderas del validador

`student-materials/courier/validate_format.py` se corre tal cual viene, sin
tocarlo. Las tres banderas tienen que estar en verde antes de la demo:

```bash
cd backend && source .venv/bin/activate

# 1) --event-log : un turno completo grabado
python3 scripts/run_evaluation.py --event-log out/shift.jsonl --seed 101
python3 ../student-materials/courier/validate_format.py --event-log out/shift.jsonl

# 2) --responses : una tanda de respuestas de /decide
python3 scripts/record_responses.py --validate

# 3) --endpoint : sonda de forma contra el servicio corriendo
uvicorn main:app --port 8000 &
python3 ../student-materials/courier/validate_format.py --endpoint http://localhost:8000/decide
```

`record_responses.py` graba el PROBE oficial + una sonda por cada constraint
del protocolo + un turno real del arnés, y reporta cuántas veces disparó cada
`binding_constraint`. Corre **sin red** por defecto (habla con la app en
proceso); `--endpoint` lo hace contra un servidor real.

Tabla de resultados sobre seeds held-out:

```bash
python3 scripts/run_evaluation.py            # seeds de REPORTE (la lámina)
python3 scripts/run_evaluation.py --tuning   # seeds de TUNING (calibrar)
```

## Mapeo de nombres de campo

> *"Judges' tooling reads these fields by name. If your field names differ,
> supply a one-page mapping in your README."*
> — `student-materials/courier/decision_response_schema.json`

**No difieren.** Los nombres del contrato oficial se usan literales en el
código, y esta tabla existe para que un juez lo confirme en diez segundos en
vez de leer el código.

### `decide_request` → nuestro parseo

Todos los campos de `order_offered` / `decide_request` se reciben con su
nombre oficial en `DecideRequest` (`backend/api/schemas.py`).

| Campo oficial | Nuestro nombre | Dónde |
|---|---|---|
| `order_id`, `platform`, `sim_time` | idéntico | `api/schemas.py::DecideRequest` |
| `zone_pickup`, `zone_dropoff` | idéntico | ídem |
| `distance_pickup_km`, `distance_delivery_km` | idéntico | ídem |
| `base_pay_mxn`, `est_tip_mxn`, `surge_multiplier` | idéntico | ídem |
| `weight_kg`, `volume_liters`, `vehicle` | idéntico | ídem |
| `restaurant_prep_min`, `estimated_pickup_min`, `estimated_delivery_min` | idéntico | ídem |
| `courier_state_overrides.*` | idéntico | `api/schemas.py::CourierStateOverrides` |

Campos extra que no conocemos se **ignoran** (`extra="ignore"`), nunca son un
422. Los que no vienen usan defaults seguros: una constraint sin datos no
dispara.

### `decide_response` → lo que devolvemos

| Campo oficial | Estado | Nota |
|---|---|---|
| `order_id`, `decision`, `reason`, `latency_ms` | ✅ requeridos, siempre presentes | `reason` garantizado <40 palabras por `core/agent/reasons.py::cap_words` |
| `binding_constraint` | ✅ | Los 6 ids del enum, ni uno inventado. `null` cuando decidió el pago |
| `tier`, `degraded` | ✅ | `tier1` siempre en este endpoint; `degraded` viene de la capa de estrategia |
| `economics.*` | ✅ los 6 campos | Más 4 de desglose (`gross_pay_mxn`, `fuel_cost_mxn`, `total_km`, `dropoff_demand_score`) |
| `shocks_applied` | ➕ extra nuestro | Lista corta de los shocks que movieron esta decisión. El schema permite extras |

### `explain_decision_response`

Las cinco claves requeridas (`order_id`, `decision`, `reason`, `inputs`,
`alternatives_considered`) salen de `core/agent/journal.py::explain_payload`,
con los números **del momento de decidir**, no recalculados.

### Event log

Los 8 tipos de evento se emiten con el nombre oficial y campos al nivel raíz
(no anidados bajo `payload`) — ver `core/models.py::EventType`.

## Dónde están los límites, en código

> *"Constraints live in code, not in prompts. A judge will ask you to open the
> file where the constant is defined."*

| Qué | Archivo | Sección |
|---|---|---|
| Las 5 constraints de seguridad | `backend/core/agent/safety.py` | `LIMITES`, arriba del archivo |
| Salario de reserva y sus cotas | `backend/core/agent/strategy.py` | `LIMITES` |
| Peso de la zona de dropoff | `backend/core/agent/economics.py` | `DROPOFF_DEMAND_WEIGHT` |
| Límites de peso/volumen/velocidad por vehículo | `backend/core/models.py` | `VEHICLE_PROFILES` |
| Cuánto mueve cada shock | `backend/core/agent/shocks.py` | `LIMITES` |
| Umbrales de los baselines | `backend/core/evaluation/policies.py` | arriba del archivo |
| Seeds de tuning y de reporte | `backend/core/evaluation/seeds.py` | — |

## Modo degradado (protocolo §7)

Los jueces invalidan la credencial a media corrida. Para ensayarlo:

```bash
curl localhost:8000/status                      # degraded: false
unset ANTHROPIC_API_KEY                         # (o pon una inválida)
# ...siguiente /decide dispara el refresco de tier2, que falla
curl localhost:8000/status                      # degraded: true, last_model_error
```

El fast path sigue decidiendo con la última estrategia conocida, dentro del
presupuesto, y lo **señala** por tres canales: el campo `degraded` de cada
respuesta, el evento `strategy_update` del log y `GET /status`.

## Estructura del repo

```
backend/
├── api/            # /decide, /shock, /explain_decision, /status + REST/WS de simulación
├── core/
│   ├── agent/      # safety, economics, reasons, journal, strategy, shocks + motor de coordenadas
│   ├── evaluation/ # arnés de turnos, políticas comparables, seeds, métricas
│   ├── routing/    # heurística, OR-Tools, grafo vial
│   ├── simulation/ # reloj/eventos + estado del courier
│   └── models.py   # contratos compartidos (Offer, ZoneMap, VehicleProfile, ...)
├── data/           # grafo .graphml y dataset (no se commitean, ver data/README.md)
├── scripts/        # run_evaluation.py, record_responses.py
└── tests/

frontend/src/       # app/, components/, hooks/, lib/types.ts
docs/               # arquitectura, documentación técnica, integración, resultados
student-materials/  # el contrato oficial del reto (no se modifica)
```

## Contratos de datos

`backend/core/models.py` (Python) y `frontend/src/lib/types.ts` (TypeScript)
definen las mismas estructuras. Son la interfaz fija entre bloques — si
necesitas cambiarlas, coordina con el equipo antes.
