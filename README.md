# The Courier — HackMTY 2026 (Infosys)

Simulación del turno de un repartidor en Monterrey donde un agente de IA
decide en tiempo real aceptar/rechazar ofertas y re-optimiza su ruta ante
cambios del entorno, comparado contra un baseline sobre el mismo stream de
eventos.

Documentación completa de la arquitectura y las decisiones técnicas en
[`docs/01_Arquitectura.md`](docs/01_Arquitectura.md) y
[`docs/02_Documentacion_Tecnica.md`](docs/02_Documentacion_Tecnica.md) —
léelos antes de tocar código, ahí está el "por qué" de cada bloque.

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, FastAPI, WebSockets nativo |
| Ruteo / optimización | Google OR-Tools (VRPTW), OSMnx + NetworkX |
| Frontend | Next.js 16 (App Router) + TypeScript + Tailwind CSS |
| Estado | En memoria, con locks + versionado optimista (sin base de datos externa) |

## Estructura del repo

```
backend/
├── api/            # Bloque 6 (backend): rutas REST + WebSocket
├── core/
│   ├── agent/      # Bloque 3: motor de decisión + logs explicables
│   ├── routing/    # Bloque 4 y 5: heurística, OR-Tools, grafo vial
│   ├── simulation/ # Bloque 1 y 2: reloj/eventos + estado del courier
│   └── models.py   # Contratos de datos compartidos (Offer, RouteStop, ...)
├── data/           # Grafo .graphml y dataset (no se commitean, ver data/README.md)
├── tests/
├── main.py
└── requirements.txt

frontend/
└── src/
    ├── app/            # page.tsx, layout.tsx (App Router)
    ├── components/     # Map, Metrics, Feed
    ├── hooks/          # useSimulation (WebSocket)
    └── lib/types.ts    # Espejo TS de los contratos del backend

docs/                # Arquitectura y documentación técnica del reto
```

## Quickstart

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

Health check: `http://localhost:8000/health`

Correr tests:

```bash
pytest
```

> **Nota sobre `osmnx`**: instala varias dependencias geoespaciales
> (geopandas, shapely, pyogrio). Si falla la instalación en tu máquina,
> revisa que tengas una versión reciente de `pip` (`pip install -U pip`)
> antes de reintentar.

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Abre `http://localhost:3000`.

## Contratos de datos

`backend/core/models.py` (Python) y `frontend/src/lib/types.ts`
(TypeScript) definen las mismas estructuras (`Offer`, `RoadEvent`,
`RouteStop`, `CourierState`). Son la interfaz fija entre bloques — si
necesitas cambiarlas, coordina con el equipo antes.

## Estado del scaffold

Este repo trae la estructura, dependencias y contratos ya resueltos, más
`CourierStateManager` (Bloque 2) funcional con sus tests. El resto de los
bloques (`engine.py`, `decision.py`, `greedy.py`, `ortools_optimizer.py`,
`stability.py`, `graph.py`, endpoints de `api/`, componentes de frontend)
están marcados con `TODO(equipo)` y firmas de función ya alineadas a los
contratos — ahí es donde arranca el desarrollo.
