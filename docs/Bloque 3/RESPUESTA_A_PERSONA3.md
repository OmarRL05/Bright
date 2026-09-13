# Re: Re: Contrato Bloque 3 → Persona 3

**Decisión: opción A.** Mantengo tu firma de kwargs por fuera y mis dataclasses
quedan como estructura interna de `safety.py`. No toques `api/decide.py`.

El razonamiento no es de gustos: tu endpoint ya está **verde contra
`validate_format.py --endpoint`**, y eso es el activo más escaso que tiene el
repo ahora mismo. Gastar 15 líneas de churn en la única cosa que ya pasa la
prueba oficial, a menos de 8 horas del cierre, es negativo aunque el diseño B
sea más bonito. B queda anotado como post-hackathon.

Con eso zanjado, cinco cosas: dos donde tienes razón y corrijo, dos donde te
falta información que sí tengo, y una pregunta que hay que contestar hoy.

---

## 1. `VehicleProfile` — tienes razón, sin peros

`core/models.py` es la única fuente de verdad. Mi `contracts.py` declara su
propio `VEHICLE_PROFILES` y eso es precisamente la divergencia que ya te costó
una reconciliación con Abraham. Lo borro y lo importo de ahí en cuanto traiga
tu rama (todavía no la tengo local: lo que estoy leyendo es `main`, donde
`core/models.py` aún no tiene `VEHICLE_PROFILES` ni `VehicleType`).

Mapa de nombres, para que quede escrito:

| mío (se va) | tuyo (queda) |
|---|---|
| `speed_kmh` | `avg_speed_kmh` |
| `max_weight_kg` | `weight_limit_kg` |
| `max_volume_liters` | `volume_limit_liters` |

## 2. La capa económica — retiro lo dicho

Estuve diciendo que nadie era dueño de la economía bajo el contrato oficial.
Era cierto de `main`; no lo es de tu rama, donde existe `core/agent/economics.py`
con `RESERVATION_WAGE_MXN_HR`. Bórralo de la lista de huecos.

---

## 3. Tu firma necesita 4 kwargs más — todos opcionales, tu call site no cambia

Aquí es donde la opción A tiene un costo real que hay que pagar. Tu firma
actual no transporta información que tres de las cinco constraints necesitan:

```python
evaluate_safety(*, vehicle, weight_kg, volume_liters, sim_time, zone_dropoff,
                continuous_riding_min, order_total_time_min, shift_end_time)
```

**Falta `in_flight_weight_kg` / `in_flight_volume_liters`.** Sin ellos la
capacidad solo puede mirar el pedido suelto. 2 kg son inofensivos solos e
imposibles si ya lleva 11 en una moto de 12. Es el hallazgo 3 de la auditoría
(la mochila creció a 200+ paradas en un turno).

**Falta `last_break_end_time`.** Los jueces arman `courier_state_overrides` a
mano y el schema incluye ese campo justo al lado de `continuous_riding_min`.
Nada les impide mandar `continuous_riding_min=250` con una pausa terminada hace
10 minutos. Si solo le crees al contador, la pausa obligatoria dispara *justo
después de descansar* — falso positivo que el sondeo de "Continuous-riding
safeguards" encuentra de inmediato. Mi gate reconcilia por el mínimo de los dos.

**Falta `queue_offset_min`.** `order_total_time_min` es la duración del pedido
solo. La constraint de fin de turno tiene que comparar contra cuándo termina
*todo*, incluyendo lo que ya trae en vuelo. Es el hallazgo 2: seis pedidos con
delta individual aceptable terminan 8 minutos después del cierre porque nadie
mira el acumulado, y además es la categoría de sondeo "Stacking and route
feasibility". Propongo **agregar un kwarg** en vez de cambiarle el significado
a `order_total_time_min`: redefinir en silencio lo que significa un parámetro
existente es peor que sumar uno nuevo.

Firma resultante — los cuatro con default seguro, así que **tu llamada actual
sigue compilando y comportándose igual**:

```python
def evaluate_safety(
    *, vehicle, weight_kg, volume_liters, sim_time, zone_dropoff,
    continuous_riding_min, order_total_time_min, shift_end_time,
    in_flight_weight_kg: float = 0.0,       # nuevo
    in_flight_volume_liters: float = 0.0,   # nuevo
    last_break_end_time: datetime | None = None,   # nuevo
    queue_offset_min: float = 0.0,          # nuevo
) -> SafetyViolation | None: ...
```

Los cuatro salen directo de `courier_state_overrides`, así que es leerlos donde
ya lees los demás.

## 4. Capacidad: es UNA constraint, no dos

Dices que son dos constraints distintas y que la acumulada sigue sin dueño.
La segunda mitad es correcta — la tomo yo, es P1.5 y son 4 líneas una vez que
entren los kwargs de arriba. La primera mitad no: el protocolo lista **una**
(4e, *"Weight and volume limits per vehicle type"*) y el enum oficial tiene
**un** id, `vehicle_capacity`. Si las partimos en dos, las dos tienen que
reportar el mismo `binding_constraint` y no ganamos nada; peor, invita a que
una dispare y la otra no.

Es una constraint evaluada sobre la suma: `Σ(en vuelo) + entrante > límite`.
El pedido suelto es el caso particular donde la mochila está vacía.

Detalle de frontera que hay que fijar y escribir: **`==` el límite cabe**.
Dispara con `>` estricto. El límite es el máximo permitido, no el primer valor
prohibido.

---

## 5. La pregunta que hay que contestar hoy

**¿Tu `flagged_zone_night` compara contra la hora del ping o contra la hora
estimada de llegada al dropoff?**

Si es contra la hora del ping, la regla se burla sola: a las 21:50 acepta un
pedido que entrega a las 22:06, y deja al repartidor exactamente donde la regla
dice que no debe estar. Mi gate la evalúa contra la llegada
(`sim_time + tiempo estimado hasta el dropoff`) y tiene test de los dos lados.

Si la tuya es contra el ping, no es un descuido tuyo — es que
`order_total_time_min` estaba ahí y no era obvio que hacía falta usarlo aquí
también. Pero hay que cambiarlo antes de la demo.

---

## 6. Lo que te entrego, y lo que quiero de vuelta

**Adopto tus tres peticiones**, con una tolerancia extra para que no dependa
del orden en que mergeemos:

- `SafetyViolation.detail: dict` con los números crudos. Ya está.
- `.violations` con todas las que dispararon. Vive en `SafetyVerdict`. Para no
  romper tu `-> SafetyViolation | None`, expongo **las dos**:
  `evaluate_safety(...)` devuelve la violación que manda (tu firma, sin cambios)
  y `evaluate_safety_full(...)` devuelve el `SafetyVerdict` completo, con los
  mismos kwargs. Migras cuando quieras `.violations`; no tienes que hacerlo.
- `combine()` acepta `SafetyVerdict`, `SafetyViolation` o `None`
  indistintamente, así que sirve con cualquiera de las dos formas.

**Lo nuevo que traigo** (archivos que no existen en tu rama, colisión cero):

| archivo | qué es |
|---|---|
| `core/agent/reasons.py` | builder de `reason` con <40 palabras **garantizado** por `cap_words`, contando igual que `validate_format.py` (`len(reason.split())`) |
| `core/agent/journal.py` | `explain_decision` completo: las 5 claves del schema, con los números *del momento de decidir*, no recalculados |
| `core/agent/strategy.py` | tier2 + modo degradado (protocolo §7), con `ClaudeAdvisor` real |
| `tests/test_safety.py`, `test_journal.py`, `test_strategy.py` | ~90 tests, incluido uno que corre `validate_format.py --event-log` de verdad en un subproceso |

**Una cosa que necesito de ti para tier2:** el punto de enganche que señalaste,
`economics.py::RESERVATION_WAGE_MXN_HR`, tiene que dejar de ser una constante
leída al importar y pasar a leerse por decisión:

```python
from core.agent.strategy import STRATEGY

wage = STRATEGY.snapshot().reservation_wage_mxn_hr   # sin lock, sin red, ~ns
```

`snapshot()` es una lectura de atributo: no bloquea, no falla, no toca la red.
Es lo que permite que el modelo se caiga sin que el fast path se entere, y que
`degraded` viaje a tu response. Dime si lo cambias tú o lo cambio yo.

**Y un aviso de expectativas:** tú tienes 84 tests verdes en tu rama y yo 115
en la mía, sobre árboles divergentes. El riesgo de aquí al cierre no es el
código, es el merge. Propongo que traigas lo tuyo a `main` primero y yo rebaseo
encima — el que tiene el endpoint verde no debería ser el que rebasea.

## 7. Gracias por dos cosas

El fix del event log (nombres internos que no eran ninguno de los 8 oficiales)
y el pin de Python 3.12 con el porqué de las wheels. Ninguna de las dos estaba
en mi radar y las dos eran bloqueantes.
