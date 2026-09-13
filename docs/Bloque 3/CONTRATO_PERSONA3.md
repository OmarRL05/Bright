# Contrato Bloque 3 → Persona 3 (`POST /decide`)

> **Para Persona 3.** Esto es todo lo que necesitas de mi lado para escribir el
> endpoint **sin esperarme**. La firma de abajo está **congelada**: puedes
> escribir contra ella ahora mismo aunque el cuerpo de las constraints todavía
> esté en progreso. Si algo tiene que cambiar, te aviso antes de cambiarlo.
>
> Fuente de verdad del contrato HTTP:
> `student-materials/courier/decision_response_schema.json` (ya commiteado en
> el repo). Este documento **no lo reemplaza**, solo dice quién llena qué.

---

## 1. Reparto en una línea

**Tú** eres dueño del transporte: ruta HTTP, parseo, serialización, `latency_ms`
y que el proceso no se caiga.
**Yo** soy dueño del veredicto de seguridad: las 5 constraints, el
`binding_constraint` y el `reason` de cada refusal.

La economía (reservation wage) la combinas tú — pero **no a mano**: te doy un
`combine()` que hace imposible devolver `ACCEPT` cuando la seguridad bloqueó.
Esa es la invariante *Safety-over-pay* que los jueces prueban explícitamente
(protocolo, sección 3), y no quiero que dependa de que ninguno de los dos se
acuerde del orden de los `if`.

---

## 2. Superficie que te exporto (congelada)

```python
from core.agent.contracts import (
    OrderRequest,          # dataclass espejo de order_offered / decide_request
    CourierRuntimeState,   # dataclass espejo de courier_state_overrides
    VEHICLE_PROFILES,      # moto / car / bike: speed_kmh, max_weight_kg, max_volume_liters
)
from core.agent.safety import evaluate_safety, combine, SafetyVerdict
```

### 2.1 Construir las entradas

```python
order = OrderRequest.from_payload(payload)                  # nunca lanza
state = CourierRuntimeState.from_overrides(
    payload.get("courier_state_overrides"),                 # puede ser None
    base=<estado real del turno, o None>,
)
```

Ambos constructores son **totales**: toleran campos extra, campos ausentes y
valores basura sin lanzar excepción. No necesitas validar el payload antes.

### 2.2 Evaluar seguridad

```python
verdict: SafetyVerdict = evaluate_safety(order, state)
```

`SafetyVerdict` tiene exactamente esto:

| atributo | tipo | qué es |
|---|---|---|
| `blocked` | `bool` | `True` si alguna constraint dura violó |
| `binding` | `SafetyViolation \| None` | la que se reporta, ya resuelta por precedencia |
| `violations` | `tuple[SafetyViolation, ...]` | **todas** las que violaron, en orden de precedencia |

`SafetyViolation` tiene `.constraint` (el id del enum oficial), `.reason` (string
ya formateado, **garantizado <40 palabras**) y `.detail` (dict con los números
crudos, para `explain_decision`).

### 2.3 Combinar con tu lado económico

```python
decision, reason, binding = combine(
    verdict,
    economic_accept=<bool: tu regla de pago dice aceptar>,
    economic_reason=<str: tu reason si manda la economía>,
    economic_binding=<"reservation_wage" | None>,
)
```

Devuelve la tripleta lista para el body. Si `verdict.blocked`, ignora por
completo tus tres argumentos económicos y devuelve el refusal de seguridad —
por construcción, no por convención.

---

## 3. Quién llena cada campo de la respuesta

| campo | requerido | quién | nota |
|---|---|---|---|
| `order_id` | ✅ | tú | echo del request, siempre string |
| `decision` | ✅ | `combine()` | `"ACCEPT"` \| `"SKIP"` |
| `reason` | ✅ | `combine()` | el validador rechaza >40 palabras y rechaza vacío |
| `latency_ms` | ✅ | **tú** | `perf_counter` de extremo a extremo |
| `binding_constraint` | — | `combine()` | `null` si decidió el pago solo |
| `tier` | — | tú | **siempre `"tier1"`** en este endpoint |
| `degraded` | — | strategy layer | te doy un `snapshot().degraded`; hasta entonces `False` |
| `economics` | — | tú | opcional, pero es lo que contestas cuando un juez pide la aritmética |

---

## 4. Siete cosas que el protocolo castiga y que se te pueden pasar

1. **Extras no pueden reventar el parseo.** El PROBE de `validate_format.py`
   manda `platform`, `est_tip_mxn`, `restaurant_prep_min`, `decision_deadline`.
   Si usas un modelo pydantic estricto, eso es un **422** y el `--endpoint`
   queda en rojo. Usa `model_config = ConfigDict(extra="ignore")`, o recibe
   `dict` crudo y pásalo a `OrderRequest.from_payload`.
2. **El PROBE no manda `courier_state_overrides`.** Tiene que responder 200 con
   una decisión válida igual. Mis defaults ya lo cubren; no agregues un
   `required` ahí.
3. **El endpoint no puede lanzar nunca.** Un 500 es *hard failure* de
   Feasibility (protocolo §7). Envuelve todo en try/except y, ante lo
   inesperado, responde `SKIP` con un reason honesto en vez de propagar.
4. **Cero LLM, cero red, cero I/O a disco dentro del handler.** Cualquier ruta
   de código que llame a un modelo dentro de la ventana de decisión falla
   Feasibility. El log JSONL se escribe en batch fuera del handler.
5. **Cero `datetime.now()`.** Todo el tiempo entra por `sim_time` y por los
   overrides. Si lees el reloj de pared, el replay (protocolo §6) deja de dar
   decisiones idénticas y ese es un check explícito.
6. **`shift_end_time` se lee del estado, nunca se hardcodea** — el schema lo
   dice literalmente: *"Read from state, never hardcoded — judges check this."*
7. **`latency_ms` se mide, no se estima.** `t0 = perf_counter()` al entrar al
   handler, `t1` justo antes de devolver. Mi gate corre en microsegundos, así
   que el número que reportes es básicamente tu overhead: mídelo de verdad.

---

## 5. Esqueleto que puedes copiar hoy

```python
from time import perf_counter
from fastapi import APIRouter, Request

from core.agent.contracts import CourierRuntimeState, OrderRequest
from core.agent.safety import combine, evaluate_safety

router = APIRouter()


@router.post("/decide")
async def decide(request: Request) -> dict:
    t0 = perf_counter()
    try:
        payload = await request.json()
        order = OrderRequest.from_payload(payload)
        state = CourierRuntimeState.from_overrides(payload.get("courier_state_overrides"))

        verdict = evaluate_safety(order, state)

        # TODO(Persona 3): tu capa económica. Mientras no exista, `True` es
        # un placeholder honesto: acepta todo lo que la seguridad permita.
        economic_accept, economic_reason, economic_binding = True, "cumple el umbral de pago", None

        decision, reason, binding = combine(
            verdict,
            economic_accept=economic_accept,
            economic_reason=economic_reason,
            economic_binding=economic_binding,
        )
        order_id = order.order_id
    except Exception:
        # Nunca 500: un crash en la ventana de decisión es fallo duro.
        order_id = str((payload or {}).get("order_id", "UNKNOWN"))
        decision, reason, binding = "SKIP", "error interno al evaluar la oferta", None

    return {
        "order_id": order_id,
        "decision": decision,
        "reason": reason,
        "binding_constraint": binding,
        "tier": "tier1",
        "degraded": False,
        "latency_ms": (perf_counter() - t0) * 1000.0,
    }
```

Con esto, esto debería quedar en verde de inmediato:

```bash
python3 student-materials/courier/validate_format.py --endpoint http://localhost:8000/decide
```

---

## 6. `explain_decision` — ya está listo, conéctalo

`core/agent/journal.py` guarda cada decisión y la explica después. Dos llamadas:

```python
from core.agent.journal import JOURNAL, DecisionRecord

# (a) dentro del handler, después de combine() — O(1), sin formateo, sin I/O
JOURNAL.record(DecisionRecord(
    order=order, state=state, verdict=verdict,
    decision=decision, reason=reason, binding_constraint=binding,
    latency_ms=(perf_counter() - t0) * 1000.0,
    economics=<tu dict de aritmética, o None>,
))

# (b) la ruta que los jueces usan para preguntar "¿por qué saltaste ese pedido?"
@router.get("/explain/{order_id}")
async def explain(order_id: str):
    payload = JOURNAL.explain(order_id)       # None si no existe
    return payload or {"error": "order_id no registrado"}
```

`JOURNAL.explain()` ya devuelve exactamente las cinco claves del schema
(`order_id`, `decision`, `reason`, `inputs`, `alternatives_considered`), con los
números **del momento de decidir**, no recalculados. No formatees nada encima.

Usa **`JOURNAL`, la instancia compartida** del módulo, no una tuya: si cada
capa construye la suya, `explain` contesta "no encontrado" para decisiones que
sí se tomaron.

Para el feed del dashboard: `JOURNAL.recent(20)` da los últimos registros de la
más nueva a la más vieja, y `to_decision_event(record)` (mismo módulo) convierte
uno al evento `decision` del event log JSONL — ya verificado contra
`validate_format.py --event-log` en un test.

## 7. Lo que falta y no te bloquea

| entrega | qué cambia para ti |
|---|---|
| `strategy.py` (tier2 + degradado) | cambias `"degraded": False` por el flag del snapshot |
| capa económica bajo el contrato oficial | pasas `economics=` al `DecisionRecord` y tus tres argumentos a `combine()` |

Ninguna de las dos cambia la firma de la sección 2.

---

## 8. Lo que necesito de ti

1. **Un venv que arranque.** Hoy `fastapi` no está instalado y no hay venv en
   el repo: el backend **no levanta en esta máquina**, y `--endpoint` es P0.
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install fastapi "uvicorn[standard]" pydantic python-dotenv pytest httpx
   ```
   Deja `ortools`/`osmnx` fuera: no arrancan, no puntúan y tardan en instalar.
2. **Confírmame el nombre de la ruta**: el validador apunta a `/decide` en la
   raíz, no a `/api/decide`. Si la montas bajo el prefijo `/api` existente, el
   comando de arriba falla.
3. **Dime si tu capa económica emite `binding_constraint`.** Si el pago rechaza,
   el enum oficial espera `"reservation_wage"`, no `null`.
