# Arquitectura del Sistema — HackMTY 2026 · Infosys "The Courier"

## 1. Objetivo del sistema

Simular el turno de un repartidor (courier) en Monterrey y demostrar cómo un agente de IA decide, en tiempo real, **aceptar o rechazar** ofertas de pedidos, y cómo **re-optimiza** su ruta ante cambios del entorno (surge pricing, cierres viales, tráfico), maximizando ganancias sin tomar decisiones inseguras o inviables para un repartidor real.

El sistema corre dos instancias del mismo turno en paralelo — el **agente IA** y un **baseline de control** — sobre el mismo stream de eventos, para poder comparar resultados de forma justa en el demo.

---

## 2. Flujo end-to-end (por cada "tick" de la simulación)

1. El **simulador** avanza el reloj del turno y genera eventos: ofertas nuevas, cambios de surge, cierres viales o tráfico pesado.
2. Si el evento es una **oferta**, el **motor de decisión** la evalúa de inmediato:
   - calcula el costo marginal de insertarla en la ruta actual (heurística de inserción),
   - compara ese costo contra el pago, el tiempo restante del turno y la señal de demanda histórica de la zona,
   - **acepta o rechaza**, y genera un log explicando por qué.
3. Si acepta, se actualiza el **estado del courier** (mochila, ganancias, ruta activa) — de forma segura ante concurrencia (ver sección 5).
4. En paralelo, el **optimizador global** evalúa si conviene reordenar las paradas pendientes (batching), respetando el tramo que el courier ya tiene comprometido (ver sección 6).
5. Si el evento es **topológico** (cierre, tráfico), el motor de mapas recalcula los caminos afectados y, si el tramo comprometido del courier se ve afectado, dispara una re-optimización inmediata de emergencia.
6. La **interfaz de demo** refleja en vivo el estado de ambos agentes: mapa, métricas ($/hora, ganancias, tiempo restante) y el feed de logs explicables.

---

## 3. Bloques del sistema

### Bloque 1 — Simulador de Entorno (Event Streamer)

**Qué hace**
- Controla el reloj del turno.
- Genera y emite: ofertas (pings de pedidos), variaciones de surge pricing, eventos topológicos (cierres, tráfico).
- **Debe usar una semilla fija (seed)** y reproducir exactamente el mismo stream de eventos para el agente IA y para el baseline — sin esto, la comparación "mismo turno fresco" del demo no es válida.

**Conecta con**
- Ofertas y surge → Bloque 3.
- Eventos de red → Bloque 5.
- Pulso de tiempo → Bloque 2.

---

### Bloque 2 — Gestor de Estado del Agente (Courier State Manager)

**Qué hace**
- Es la única fuente de verdad del turno, en memoria (no hay base de datos externa).
- Mantiene: tiempo restante, ganancias acumuladas, pedidos en mochila, ruta activa — para el agente IA y para el baseline por separado.
- Mantiene un **contador de versión** que se incrementa cada vez que el estado cambia (aceptación de oferta, aplicación de ruta optimizada).

**Concurrencia**
- Bloque 3 (hilo principal) y Bloque 4 (hilo secundario) leen y escriben el mismo objeto de estado. Todo acceso pasa por un lock.
- Expone dos operaciones:
  - `snapshot()` — lectura consistente del estado y su versión actual.
  - `apply_optimized_route(version, ruta)` — solo aplica la ruta propuesta por el Bloque 4 si la versión no cambió desde que empezó a resolver; si cambió, la descarta (evita sobrescribir con una propuesta basada en una mochila que ya no existe).

**Conecta con**
- Se actualiza por Bloque 3 (al aceptar oferta) y Bloque 4 (al aplicar ruta optimizada).
- Expone el estado en tiempo real → Bloque 6.

---

### Bloque 3 — Motor de Decisión en Tiempo Real

**Qué hace**
1. **Heurística de inserción** — calcula distancia extra, tiempo extra e impacto en entregas ya aceptadas si se inserta la nueva oferta (estrategia golosa).
2. **Evaluación de umbral** — pondera ese costo contra el pago ofrecido, el tiempo restante del turno, la señal de demanda histórica de la zona y reglas mínimas (ej. $/km o $/min mínimo).
3. **Regla de compromiso (frozen horizon)** — el tramo hacia el punto al que el courier ya está en tránsito no se reordena. Solo se permite insertar ahí una oferta nueva si el costo marginal es prácticamente cero (está literalmente en el camino).
4. **Explicabilidad** — genera un log corto de la decisión (ej. `Rechazado: desvío de 15 min por $20 en zona fría`).

**Conecta con**
- Recibe ofertas de Bloque 1.
- Consulta distancias/tiempos a Bloque 5.
- Si acepta, actualiza Bloque 2.
- Envía logs a Bloque 6.

---

### Bloque 4 — Optimizador Global (hilo en background)

**Qué hace**
- Resuelve el reordenamiento de paradas pendientes como VRPTW con OR-Tools, sin congelar la simulación (corre en un thread aparte).
- Toma como punto de inicio fijo la posición futura del courier tras completar el tramo comprometido (frozen horizon) — solo reordena de ahí en adelante.

**Disparadores (triggers)**
| Tipo | Condición | Prioridad |
|---|---|---|
| Batch | 3+ pedidos nuevos aceptados sin optimizar globalmente | Normal |
| Idle | El courier acaba de entregar y está por iniciar el siguiente tramo | Normal |
| Override (emergencia) | Un evento topológico invalida el tramo comprometido o la ruta activa | Alta — se salta el freeze y el umbral de histéresis |

**Umbral de histéresis**
- Solo se aplica la ruta nueva si reduce la distancia restante en más de un 5% o ahorra más de 2 minutos (parámetros configurables). Si la mejora es marginal, se conserva la ruta original para evitar inestabilidad de ruta ("route thrashing" / plan nervousness).
- El override de emergencia ignora este umbral: si la ruta actual quedó inválida por un cierre, se reemplaza sin importar el ahorro.

**Conecta con**
- Lee paradas pendientes y versión del estado de Bloque 2.
- Pide matriz de distancias a Bloque 5.
- Escribe la ruta optimizada en Bloque 2 (sujeta a validación de versión).

---

### Bloque 5 — Motor Topológico (OSMnx + NetworkX)

**Qué hace**
- Mantiene el grafo vial de Monterrey (precargado al inicio; matriz de distancias precalculada si el tamaño del grafo lo permite).
- Resuelve caminos más cortos bajo demanda.
- Ante eventos de cierre/tráfico, ajusta el peso de las aristas afectadas (a infinito o un valor muy alto) y recalcula las rutas afectadas.
- Si el tramo comprometido del courier queda invalidado por el evento, notifica al Bloque 4 para disparar el override de emergencia.

**Conecta con**
- Escucha eventos viales de Bloque 1.
- Provee distancias y tiempos a Bloque 3 y Bloque 4.

---

### Bloque 6 — Interfaz de Demostración (API + Frontend)

**Qué hace**
- Backend con **FastAPI** (no Flask): WebSocket nativo para streaming en vivo sin necesitar extensiones tipo Flask-SocketIO, que requieren monkey-patching (eventlet/gevent) y pueden entrar en conflicto con el thread nativo del Bloque 4.
- Endpoints REST para iniciar/pausar la simulación.
- Frontend (Next.js/React): mapa con la ruta activa y cierres, métricas en vivo ($/hora, ganancias, tiempo restante), feed de logs explicables, y vista comparativa lado a lado del agente IA vs. el baseline.

**Conecta con**
- Lee estado de Bloque 2.
- Lee logs de Bloque 3.

---

## 4. Diagrama de flujo

```
[Bloque 1: Simulador] ──ofertas/surge──▶ [Bloque 3: Motor de Decisión] ──acepta──▶ [Bloque 2: Estado]
        │                                        │  ▲                                    ▲  │
        │                                  consulta│  │escribe                    lee/versión│  │aplica ruta
        │                                        ▼  │                                    │  ▼
        └──eventos viales──▶ [Bloque 5: Topología] ◀─┴────consulta matriz──────── [Bloque 4: Optimizador]
                                                                                    (thread background)

[Bloque 2: Estado] ──stream en vivo──▶ [Bloque 6: API + Frontend] ◀──logs── [Bloque 3]
```

---

## 5. Modelo de concurrencia

- **Hilo principal**: loop de simulación (Bloque 1) + evaluación de ofertas (Bloque 3). Mutaciones directas al estado vía lock.
- **Hilo secundario**: Bloque 4, corre OR-Tools sin bloquear el hilo principal (la librería libera el GIL durante el cómputo pesado).
- **Patrón usado**: concurrencia optimista por versión.

```python
class CourierState:
    def __init__(self):
        self.lock = threading.Lock()
        self.version = 0
        self.backpack = []
        self.route = []

    def snapshot(self):
        with self.lock:
            return self.version, list(self.backpack), list(self.route)

    def apply_optimized_route(self, solved_version, new_route):
        with self.lock:
            if solved_version == self.version:
                self.route = new_route
            # si la versión cambió, se descarta y el Bloque 4 reintenta
            # en su próximo disparador
```

---

## 6. Reglas de estabilidad de ruta (resumen)

| Regla | Qué hace | Excepción |
|---|---|---|
| 1. Compromiso (frozen horizon) | El tramo en tránsito no se reordena | Inserción de costo marginal ~0; cierre de calle sobre el tramo comprometido |
| 2. Disparadores controlados | Optimizador solo corre en batch, idle, u override | Override rompe el ciclo normal ante emergencia |
| 3. Umbral de histéresis | Solo se aplica ruta nueva si mejora >5% distancia o >2 min | Se ignora en triggers de override |

---

## 7. Estructura de carpetas del proyecto

```
hackathon-courier-agent/
├── backend/
│   ├── api/
│   │   ├── routes.py            # FastAPI: iniciar/pausar simulación
│   │   └── sockets.py           # WebSocket: streaming de estado en vivo
│   ├── core/
│   │   ├── agent/
│   │   │   ├── decision.py      # Bloque 3: inserción + umbral + frozen horizon
│   │   │   └── llm_log.py       # Generación/formato de explicaciones
│   │   ├── routing/
│   │   │   ├── graph.py         # Bloque 5: OSMnx + NetworkX, recálculo por cierres
│   │   │   ├── greedy.py        # Heurística de inserción O(n)
│   │   │   ├── ortools_optimizer.py  # Bloque 4: VRPTW en thread background
│   │   │   └── stability.py     # Triggers + umbral de histéresis
│   │   └── simulation/
│   │       ├── engine.py        # Bloque 1: reloj, eventos, seed fija
│   │       └── state.py         # Bloque 2: CourierState con locks/versión
│   ├── data/
│   │   ├── monterrey.graphml
│   │   └── kaggle_orders.csv
│   ├── main.py
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── app/
    │   │   ├── page.tsx
    │   │   └── layout.tsx
    │   ├── components/
    │   │   ├── Map.tsx
    │   │   ├── Metrics.tsx
    │   │   └── Feed.tsx
    │   └── hooks/
    │       └── useSimulation.ts
    ├── package.json
    └── tailwind.config.ts
```

---

## 8. Contratos de datos compartidos

Estas estructuras son el "contrato" entre bloques — deben acordarse antes de repartir el trabajo, para que cada persona pueda programar contra una interfaz fija.

```python
@dataclass
class Offer:
    id: str
    pickup: tuple[float, float]
    dropoff: tuple[float, float]
    pay: float
    time_window: tuple[float, float]
    received_at: float

@dataclass
class RoadEvent:
    type: Literal["closure", "traffic", "surge"]
    location: tuple[float, float] | list[tuple[float, float]]
    multiplier: float | None   # para surge
    timestamp: float

@dataclass
class RouteStop:
    offer_id: str
    kind: Literal["pickup", "dropoff"]
    eta: float

@dataclass
class CourierState:
    version: int
    time_remaining: float
    earnings: float
    backpack: list[Offer]
    route: list[RouteStop]
```
