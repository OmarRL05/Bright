# Ensayo de la demo — hoja de ruta

> El guion está en `backend/scripts/demo.py`, no aquí. Este documento dice
> **cómo correrlo, en qué orden y qué contestar**; el script dice qué pasa y
> verifica que siga pasando.

## Por qué el guion es código

    "A constraint that exists in code but is never demonstrated triggering
     during your demo scores low. Rehearse at least two as live demo moments."

Un guion en Markdown se desincroniza en silencio: alguien mueve
`HEAT_RULE_MAX_CONTINUOUS_MIN` y el papel sigue prometiendo un rechazo que ya
no ocurre. En `demo.py` **cada escena declara el resultado que espera**, así
que correr el ensayo es también una prueba de regresión. Ya pasó: las escenas
B y F fallaron en el primer ensayo porque las ofertas se habían diseñado antes
de calibrar el salario de reserva a $400/hr. Eso se descubrió aquí y no en el
escenario — que es el punto entero.

## Preparación

```bash
cd backend && source .venv/bin/activate
export GEMINI_API_KEY=...          # imprescindible para la escena E
uvicorn main:app --port 8000
```

En otra terminal:

```bash
python3 scripts/demo.py --list          # ver las escenas
python3 scripts/demo.py --no-interactive # ensayo completo salvo la E
python3 scripts/demo.py --scene A       # una sola
```

**Sin `GEMINI_API_KEY` la escena E no se puede ensayar y el script lo dice.**
Es a propósito: sin credencial, `main.py` no conecta tier2, porque "no hay
modelo configurado" no es lo mismo que "el modelo se cayó" — y arrancar ya
degradado destruye justo la transición que hay que demostrar.

## Orden y tiempos

| # | escena | demuestra | ~ |
|---|---|---|---|
| 1 | **A** Seguridad sobre paga | constraint 3 (`heat_rule`) en vivo | 45 s |
| 2 | **B** «¿Y si cambio este input?» | constraint 5 (`vehicle_capacity`) en vivo | 60 s |
| 3 | **C** La frontera de las 22:00 | constraint 1 (`flagged_zone_night`), frontera | 60 s |
| 4 | **D** Respuesta desde la bitácora | `explain_decision` en <10 s | 30 s |
| 5 | **F** Entra un surge **en vivo** | protocolo §5: shock inyectado | 45 s |
| 6 | **G** Replay determinista | protocolo §6: grabar, reproducir, difear | 60 s |
| 7 | **E** Se cae el modelo | protocolo §7, con la red apagada | 90 s |

**A y B son las dos constraints ensayadas que el protocolo exige.** C es de
regalo y es la que mejor aguanta una repregunta.

## Qué se ve, y qué se dice

### A — la mejor oferta del turno, rechazada

```
SKIP  binding=heat_rule  0.136 ms
Regla de calor (12:00-16:00): aceptarla dejaria 130 min continuos
proyectados sobre un tope de 90 min en esa franja.
bruto $594 − combustible $6 = neto $588  |  $2892/hr vs mínimo $400/hr
```

> «Son las 13:10, el repartidor lleva 118 minutos seguidos sobre la moto, y
> esta es la oferta que más paga de todo el turno: casi tres mil pesos por
> hora. La rechazamos.»

**Si preguntan por el número:** 130 no es el manejo actual, es el **proyectado**
— actual más la duración del pedido. La regla dice *"capped at 90 minutes"*, y
un tope se evalúa hacia adelante: la pregunta no es si ya te pasaste, es si
aceptar esto te pasa.

**Si piden el archivo:** `core/agent/safety.py`, sección `LIMITES`, arriba de
todo. Los cinco límites juntos y con nombre.

### B — un solo campo cambia, la decisión se invierte

```
mochila vacía    ->  ACCEPT   binding=None              $856/hr
mochila 13 kg    ->  SKIP     binding=vehicle_capacity
Capacidad de moto excedida en peso: 16.0 kg contra un limite de 15.0
(mochila 13.0 mas pedido 3.0).
```

> «No cambié el pedido. Cambié lo que ya trae cargando. Tres kilos son
> inofensivos solos e imposibles sobre trece.»

Es literalmente la pregunta que los jueces traen escrita: *"What happens if I
change this input?"*.

### C — la frontera

```
21:15  ->  ACCEPT
21:50  ->  SKIP   binding=flagged_zone_night
Zona 2 marcada: la entrega llegaria 22:02, dentro del toque de queda
que empieza 22:00.
```

> «Treinta y cinco minutos después. El pedido entra antes de las diez; la
> entrega no. Y la regla habla de dónde termina el repartidor, no de cuándo
> suena el teléfono.»

**Dos minutos de margen** — es el caso de frontera, no uno cómodo.

### D — contestar desde el log

> «¿Por qué saltaste ese pedido?»

`GET /explain_decision/ORD-DEMO-A` → **1 ms**. Enseñar
`alternatives_considered`: la segunda entrada contesta *"¿y si arreglo esa
constraint?"* sin re-correr nada, porque el journal guardó **todas** las que
violaban, no solo la que mandó.

Los números son los **del momento de decidir**, no recalculados: hay un test
que registra una estimación absurda a propósito y verifica que `explain` la
devuelva tal cual.

### F — el shock, inyectado de verdad

```
sin shocks              ->  SKIP     binding=reservation_wage   $236/hr
POST /shock surge 2.2x  ->  ACCEPT   binding=None               $559/hr
    Conviene: $559/hr efectivos contra un minimo de $260/hr, y ninguna
    constraint de seguridad la bloquea. [shock: surge 2.2x zona 3]
```

**No se toca el pedido**: el surge entra por `POST /shock`, y el body es el
mismo objeto que el evento `shock` del event log — una línea copiada de un log
entra tal cual. Es literalmente lo que el protocolo dice que los jueces pueden
hacer (§5), así que se les puede ofrecer el teclado.

El `binding_constraint` pasa de `reservation_wage` a `null`: **la máquina
distingue un rechazo por dinero de uno por seguridad sin leer la prosa**.

Los otros tres tipos también mueven números, sobre el mismo pedido:

| shock | efecto medido |
|---|---|
| ninguno | 21.60 min · 9.0 km |
| `rain` | 28.80 min · 9.0 km (baja la velocidad) |
| `closure` | 29.16 min · 12.2 km (obliga a rodear) |
| `delay` dirigido | 35.40 min — y otro pedido en el mismo instante sigue en 21.60 |

### E — se cae el modelo

El script pide apagar el wifi y espera. Luego:

```
GET /status     degraded=true   fallos=1
                ModelUnavailable: APIConnectionError...
salario de reserva CONSERVADO: $400/hr
POST /decide    ACCEPT  degraded=true  0.059 ms
```

> «Se sigue decidiendo, dentro del presupuesto, con la última estrategia
> conocida. Y lo dice: `degraded: true` viaja en cada respuesta, en el evento
> `strategy_update` del log y en `/status`.»

Ninguna oferta se encoló: **el fast path no conoce al advisor**, así que no
puede esperarlo aunque quisiera. Encender la red otra vez y mostrar la
recuperación.

### G — grabar, reproducir, difear

```
✔ IDENTICO  201 decisiones reproducidas sin una sola diferencia
   shocks   : 44 reinyectados
   strategy : 1 reinyectado  (parámetros clavados)
```

> «Grabamos el turno, se lo reproducimos contra este mismo servidor —el que
> está corriendo, con su estado— y difeamos las 201 decisiones. Ni una
> diferencia.»

**Si preguntan por qué es creíble:** durante la reproducción los parámetros de
tier2 quedan **clavados**. El protocolo permite que varíen si ninguna decisión
del fast path cambia por ello; en nuestro diseño sí cambiaría, porque el
salario de reserva es el umbral. Sin clavarlos, un diff limpio no probaría
nada.

**Si preguntan qué encontró:** dos bugs que ninguna revisión de código vio —
el log grababa el deadhead en cero, y el arnés decidía ignorando los shocks
que él mismo registraba. Está en `RESULTADOS.md` §6.bis.

## Las preguntas que traen escritas

| pregunta | dónde se contesta | respuesta corta |
|---|---|---|
| *«What happens if I change this input?»* | escena B | un campo, decisión invertida, el reason lo nombra |
| *«Why should I trust this number?»* | `RESULTADOS.md` | seeds disjuntas, y los baselines calibrados con el mismo procedimiento que el nuestro |
| *«What does it do when it's wrong?»* | escena E | sigue decidiendo y avisa que está degradado |
| *«Could a real courier use this tomorrow?»* | escenas A y B | límites de peso y de descanso que muerden de verdad |
| *«What did you cut, and why?»* | `RESULTADOS.md` §4 | el salario decreciente al final del turno: empeoró entre −0.7% y −4.8% |
| *«Why did you skip that order?»* | escena D | desde el log, 1 ms |
| *«What would it do if a surge hit right now?»* | escena F | SKIP → ACCEPT, mismo pedido |
| *«¿Puedes reproducir este turno?»* | escena G | 201 decisiones, cero diferencias |
| *«What if the restaurant is 15 min late?»* | — | `restaurant_prep_min` entra en la estimación; sube el tiempo total y puede activar `heat_rule` o `shift_end_infeasible` |

## Lo que NO hay que hacer en la demo

- **No re-derivar en vivo.** El protocolo dice que es la respuesta incorrecta
  aunque salga bien. Todo sale de `explain_decision`.
- **No prometer que la zona marcada es cualquiera.** Sólo la zona 2 está
  marcada; nuestro `ZoneMap` tiene 4 zonas y los ejemplos oficiales usan la 5,
  la 7 y la 11. Si un juez manda la 11, no dispara — y el `detail` lo dice con
  `zone_known: false`. Usar la zona 2 en el ensayo y ser honesto si preguntan.
- **No decir "ganamos a los baselines".** Perdemos 15% contra `GreedyRate`
  porque `GreedyRate` acumula 481 violaciones de seguridad. La frase correcta
  está en `RESULTADOS.md` §2.

## Área de oportunidad detectada ensayando

El salario de reserva calibrado, **$400/hr**, rechaza ofertas que a simple
vista parecen razonables: un pedido de $140 a 6.8 km sale a $389/hr y se
rechaza. Es óptimo **en nuestro simulador**, que entrega ~25 ofertas por hora;
un repartidor real recibe bastantes menos, y con menos oferta el umbral óptimo
baja. Si alguien pregunta *"¿de verdad rechazarías eso?"*, la respuesta honesta
es que el umbral está calibrado contra la frecuencia de ofertas de nuestro
simulador, y que bajar esa frecuencia bajaría el umbral.
