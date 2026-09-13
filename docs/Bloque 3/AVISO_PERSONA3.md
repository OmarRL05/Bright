# Aviso a Persona 3 — qué se tocó de tus archivos y qué no se puede romper

> **Para Persona 3 (dueña de `POST /decide`).** Dos archivos tuyos cambiaron
> de manos y hay que decirlo antes de que lo descubras en un merge:
> `core/agent/economics.py` y `api/decide.py`. Abajo va qué cambió, por qué, y
> qué de eso es un contrato que no se puede mover sin avisar.
>
> El porqué largo ya está escrito y no se repite aquí:
> [`RESPUESTA_A_PERSONA3.md`](./RESPUESTA_A_PERSONA3.md) (las decisiones de
> diseño) y [`RESULTADOS.md`](./RESULTADOS.md) (los números que las sostienen).
>
> **Rama:** `persona3/shock-responses-docs`. Todavía no está en `main`.

---

## 1. `core/agent/economics.py` — tres cambios, los tres medidos

No son preferencias de modelado: cada uno se calibró sobre `TUNING_SEEDS` y se
midió sobre las de reporte. Los números están en
[`RESULTADOS.md`](./RESULTADOS.md) §1 y §3.

**El surge multiplica la tarifa, no la propina.** `base*surge + tip`, nunca
`(base + tip)*surge`. Las plataformas aplican el multiplicador a lo que pagan
ellas; lo que deja el cliente no sube porque haya surge. La versión que inflaba
la propina sobreestimaba sistemáticamente las horas pico, que son justo las que
más pesan en el resultado.

**El pago neto descuenta combustible.** `net = base*surge + tip - km*cost_per_km`,
sobre el kilometraje **completo** (traslado en vacío + entrega). Sin esto el
deadhead sale gratis y "¿te conviene cruzar la ciudad por este pedido?" no
tiene respuesta numérica — que es exactamente lo que sondea la categoría
*Dropoff location value*. También es lo que hace que `deadhead_pct_of_km` del
CSV signifique algo.

**Dónde te deja el pedido vale dinero.** `adjusted_rate = raw_rate * (1 +
DROPOFF_DEMAND_WEIGHT * (demand_score - 0.5))`. Terminar en zona caliente vale
más porque el siguiente pedido llega antes. `DROPOFF_DEMAND_WEIGHT = 0.6`
aporta **+10.3%** de ganancias medias; se eligió 0.6 y no el pico de la rejilla
(0.4) porque con 12 turnos la diferencia está dentro del ruido y 0.6 es el
punto cuyo *peor vecino* es más alto.

**Lo que esto significa para ti:** `RESERVATION_WAGE_MXN_HR` ya no es una
constante que se lee al importar. Se define en `strategy.py` y se lee **por
decisión**:

```python
economics = evaluate_economics(..., reservation_wage_mxn_hr=STRATEGY.snapshot().reservation_wage_mxn_hr)
```

`snapshot()` es una lectura de atributo: sin lock, sin red, sin posibilidad de
fallar. Es lo que permite que el modelo se caiga sin que el fast path se entere.
Pasarlo explícito (como hacen los baselines del arnés) sigue funcionando y es
lo correcto para replay.

---

## 2. `api/decide.py` — lo que ya estaba avisado

Cubierto en [`RESPUESTA_A_PERSONA3.md`](./RESPUESTA_A_PERSONA3.md); aquí solo
el resumen operativo.

| Qué | Dónde | Nota |
|---|---|---|
| `JOURNAL.record(...)` dentro del handler | después de `combine()` | O(1), sin formateo, sin I/O. Respalda `GET /explain_decision/{order_id}` |
| `STRATEGY.snapshot()` al entrar | primera línea del handler | de ahí sale `degraded` de la respuesta |
| `GET /status` | mismo router | tercer canal de señalización del modo degradado (protocolo §7) |
| 4 kwargs nuevos en `evaluate_safety_full` | `in_flight_weight_kg`, `in_flight_volume_liters`, `last_break_end_time`, `queue_offset_min` | **todos con default seguro**: tu llamada anterior sigue compilando y comportándose igual |

Los cuatro salen directo de `courier_state_overrides`, vía
`in_flight_totals(...)` y `queue_offset_min(...)` de `core/agent/journal.py`.
Sin ellos, la capacidad solo mira el pedido suelto y la constraint de fin de
turno no ve la ruta combinada — los hallazgos 2 y 3 de la auditoría.

---

## 3. Nuevo en esta rama — lo que sí es noticia

### `POST /shock` (+ `GET`/`DELETE /shocks`)

El protocolo §5 dice que los jueces pueden inyectar shocks en vivo, y nadie
tenía el endpoint. Ahora existe, en tu router (`api/decide.py`), con la lógica
en `core/agent/shocks.py`.

**El body es el mismo objeto que el evento `shock` del event log**, así que una
línea copiada de un log grabado entra tal cual. Los cuatro tipos tienen efecto
medible:

| tipo | qué mueve |
|---|---|
| `surge` | sube el multiplicador de esa zona (se toma el mayor, **no se apila**) |
| `closure` | +35% de kilometraje en la zona afectada (los cierres **sí** se componen) |
| `rain` | velocidad al 75%, global |
| `delay` | suma `slip_min` a la preparación **del pedido que nombra** |

**Lo que cambió en tu handler**, en concreto:

1. `_order_total_time_min(request)` ahora es `_order_total_time_min(request, effects)`.
   El segundo parámetro tiene default (`None` → sin efectos), así que una
   llamada vieja sigue funcionando.
2. Antes de calcular nada se lee `SHOCKS.effects(...)` — tupla inmutable, sin
   lock, mismo patrón que `STRATEGY.snapshot()`.
3. `reasons.note_shocks(reason, effects.applied)` se aplica **después** de
   `combine()`, nunca antes: anexar texto no puede cambiar el veredicto.
4. `DecisionRecord` gana un campo `shocks`, que sale en `explain_decision`.
5. `DecideResponse` gana `shocks_applied: list[str]` — campo extra, el schema
   los permite.

**La invariante que NO se tocó:** `safety.py` sigue sin ver el pago. Los
efectos de shock viajan como tiempo y distancia; `ShockEffects` no tiene un
solo campo en MXN. Un surge de 3x **no puede** volver ACCEPT un refusal de
seguridad, y hay un test que lo fija
(`tests/test_shocks.py::test_safety_over_pay_sobrevive_a_un_surge_de_3x`).

Nota de comportamiento que te puede sorprender: un shock puede *empujar* una
oferta a violar una constraint. Llueve → el trayecto se alarga → la entrega ya
no cabe antes del cierre → `shift_end_infeasible`. Es deseado: la reacción al
shock pasa por el gate, no lo esquiva.

### `scripts/record_responses.py` — la tercera bandera del validador

Corríamos `--event-log` y `--endpoint`. Nunca `--responses`. La diferencia no
es de volumen sino de cobertura: `--endpoint` manda **una** oferta bien formada
y comprueba la forma de la respuesta feliz; `--responses` valida un lote, así
que es la única que demuestra que los **refusals** también están bien formados.

```bash
python3 scripts/record_responses.py --validate
```

Graba el PROBE oficial + una sonda por constraint + un turno real del arnés.
Estado actual: **53 respuestas, PASS**, latencia máxima 0.19 ms, y las seis
constraints del enum disparan al menos una vez. Corre **sin red** por defecto
(habla con la app en proceso), que es también el ensayo del protocolo §7.

`tests/test_responses_format.py` lo fija para que no se vuelva a quedar sin
correr.

### README

Estaba diciendo que `engine.py` sigue en `TODO` y que el resto de los bloques
son stubs — llevaba semanas de retraso. Ahora tiene el estado real por bloque,
los tres comandos de verificación, la tabla de "dónde está cada límite en
código", y la **página de mapeo de nombres de campo** que pide
`decision_response_schema.json`. Nuestros nombres coinciden con los oficiales,
así que la página dice eso y lo demuestra — media página que gana Clarity
barata.

---

## 4. Lo que no se puede mover sin avisar

Esto lo importa `api/decide.py` directamente. Si lo cambias, actualiza el otro
lado:

| Símbolo | Archivo |
|---|---|
| `evaluate_safety_full`, `evaluate_safety`, `combine`, `SafetyVerdict` | `core/agent/safety.py` |
| Los 6 strings de `binding_constraint` | `core/agent/safety.py` + `core/agent/economics.py` |
| `evaluate_economics`, `EconomicsResult` | `core/agent/economics.py` |
| `JOURNAL`, `DecisionRecord`, `in_flight_totals`, `queue_offset_min` | `core/agent/journal.py` |
| `STRATEGY.snapshot()`, `DEFAULT_RESERVATION_WAGE_MXN_HR` | `core/agent/strategy.py` |
| `SHOCKS`, `ShockEffects`, `shock_from_payload` | `core/agent/shocks.py` |
| `VEHICLE_PROFILES`, `VehicleType`, `DEFAULT_ZONE_MAP` | `core/models.py` |

Y esto tiene que seguir en verde:

```bash
cd backend && source .venv/bin/activate
pytest -q                                        # 261 passed, 3 skipped
python3 scripts/record_responses.py --validate   # PASS
uvicorn main:app --port 8000 &
python3 ../student-materials/courier/validate_format.py --endpoint http://localhost:8000/decide
```

---

## 5. Lo que sigue pendiente, y no es tuyo

Para que no lo asumas cubierto:

- **`api/routes.py` y `api/sockets.py`** siguen en `NotImplementedError`.
  Arrancar/pausar el turno y el WebSocket de estado no existen.
- **El frontend** es el scaffold: `Map.tsx` es un placeholder. Hay un
  `DashboardFeed.tsx` en `origin/feat/b5` sin mergear a `main`.
- **Bloque 4** tiene dos rutas de código para "el optimizador"
  (`core/optimization/` y `core/routing/`) con modelos de datos incompatibles,
  y ninguna conectada al estado real.
- **El mapa tiene 4 zonas** y los ejemplos oficiales usan 5, 7 y 11. No rompe
  (demanda neutral, `zone_known: false` en el `detail`), pero implica que
  `flagged_zone_night` solo puede dispararse en la zona 2 — el ensayo en vivo
  tiene que usar esa. Decisión pendiente con Persona 1: ampliar a ~16 zonas.
