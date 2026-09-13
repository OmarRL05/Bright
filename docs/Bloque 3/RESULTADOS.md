# Resultados — metodología, números y lo que no funcionó

> Reproducir todo lo de este documento:
> ```bash
> cd backend && source .venv/bin/activate
> python3 scripts/run_evaluation.py            # tabla sobre seeds held-out
> python3 scripts/run_evaluation.py --tuning   # las seeds con las que se calibró
> ```

---

## 1. La tabla

12 turnos held-out de 8 h, vehículo `moto`, zona inicial 0.
Seeds de reporte: `101 113 127 131 149 151 163 173 181 191 199 211`.
Seeds de tuning (**disjuntas**, nunca reportadas): `11 23 37 41 59 67 71 83 89 97 103 109`.

| policy | mean_earnings_mxn | mean_mxn_per_hr | accept_rate_pct | orders_completed | deadhead_pct | deadline_misses | **safety_violations** |
|---|---|---|---|---|---|---|---|
| AcceptAll | 1104.8 | 138.1 | 100.0 | 11.2 | 49.9 | 2355 | **5270** |
| HighestPay | 2039.2 | 254.9 | 12.7 | 10.4 | 49.8 | 291 | **509** |
| NearestFirst | 1590.7 | 198.8 | 24.5 | 15.0 | 0.0 | 565 | **1084** |
| GreedyRate | 4362.4 | 545.3 | 19.9 | 29.2 | 66.1 | 351 | **481** |
| *GreedyRateSafe* | *3365.0* | *420.6* | *11.4* | *22.3* | *65.7* | *104* | ***0*** |
| **OurAgent** | **3709.7** | **463.7** | **13.3** | **26.2** | **60.4** | **145** | **0** |
| Oracle | 4105.1 | 513.1 | 14.4 | 28.3 | 60.2 | 155 | **0** |

`GreedyRateSafe` no es un baseline del template: es una fila de diagnóstico
que hace legible el resto.

## 2. Qué dice la tabla, en tres restas

**El resultado honesto es que perdemos contra el mejor baseline, y el motivo
es exactamente la seguridad.** Vale más decirlo así que maquillarlo:

```
GreedyRate        $4362   481 violaciones   ← el baseline más fuerte, sin seguridad
GreedyRateSafe    $3365     0 violaciones   ← el gate de seguridad cuesta  −22.9%
OurAgent          $3710     0 violaciones   ← el valor de zona recupera    +10.3%
Oracle            $4105     0 violaciones   ← capturamos el 90.4%
```

Las tres frases que se sostienen con esto:

1. **Cero violaciones de seguridad en los 12 turnos held-out, y en los tres
   vehículos.** Hay un test parametrizado por seed que lo comprueba turno a
   turno, no sobre el promedio.
2. **El precio de la seguridad es 22.9% de las ganancias**, y lo sabemos con
   un número porque medimos la misma política con y sin el gate.
3. **De lo que se puede recuperar sin violar nada, capturamos el 90.4%.** El
   10% restante es lo que cuesta elegir el umbral a ciegas en vez de con
   conocimiento del turno completo.

`AcceptAll` es instructivo: acepta el 100% y termina **último**. Aceptar todo
llena el turno de pedidos que no dejan dinero después del combustible y que
además desplazan a los que sí.

## 3. Cómo se calibró

Todo sobre `TUNING_SEEDS`. **También los baselines** — si afináramos solo el
nuestro, "les ganamos" significaría nada más que lo afinamos:

| política | parámetro | barrido | elegido |
|---|---|---|---|
| HighestPay | `min_pay_mxn` | 60 → 300 | 170 |
| NearestFirst | `max_deadhead_km` | 1 → 12 | 1.0 |
| GreedyRate | `min_rate_mxn_hr` | 150 → 500 | 400 |
| OurAgent | `reservation_wage_mxn_hr` | 200 → 500 | 400 |
| OurAgent | `DROPOFF_DEMAND_WEIGHT` | 0.0 → 0.6 | 0.6 |

**No se tomó el máximo de la rejilla.** Con 12 turnos la diferencia entre el
pico (`wage=400, peso=0.4`, $3931) y su vecino (`peso=0.6`, $3875) está dentro
del ruido. Se eligió el punto cuyo **peor vecino** en la rejilla es más alto —
el que sobrevive a que el turno salga distinto. Ese criterio es el que hace
que el número del tuning se parezca al del reporte.

**`GreedyRate` y `OurAgent` acabaron en el mismo umbral ($400/hr).** No fue
impuesto: los dos barridos dieron el mismo óptimo por separado. Es lo que
permite que la resta entre las dos filas aísle la seguridad y no una
calibración distinta.

**$400/hr parece altísimo para un repartidor real, y lo es a propósito:** el
cuello de botella del turno es el **tiempo**, no la oferta. Llegan ~200 ofertas
en 8 h y solo caben ~28 entregas. Ser selectivo gana más dinero **y completa
más pedidos** que aceptar todo lo razonable — se ve en la tabla: OurAgent
acepta 13.3% y entrega 26.2; AcceptAll acepta 100% y entrega 11.2.

## 4. Lo que no funcionó

Se prueban aquí porque *"¿qué cortaste y por qué?"* es una de las preguntas
que los jueces hacen por escrito.

**Salario de reserva decreciente al final del turno.** La idea económica es
correcta: si faltan 30 minutos, el costo de oportunidad de quedarse parado es
casi cero, así que habría que aceptar tarifas más bajas. Se probaron 12
combinaciones de ventana (60–180 min) y piso (0.1–0.4): **todas empeoraron**,
entre −0.7% y −4.8%. El motivo es que un pedido barato y largo tiene **neto
negativo después del combustible** — el umbral bajo los deja entrar y el turno
pierde dinero. La versión correcta sería un piso en "neto > 0", no una
fracción del umbral. No se envió.

**Ajuste por zona de dropoff, primera versión.** Con el simulador original no
servía de nada (peso 0.0 era el óptimo). La causa no era la idea sino el
generador: `_generate_offer` elegía la zona de pickup **uniformemente**,
ignorando el `demand_score` que la propia zona declara. Si las zonas calientes
no producen más ofertas, terminar en una no puede valer nada. Corregido el
generador, el ajuste aporta **+10.3%**.

**Oracle clarividente y goloso.** Primera versión: rechazar un pedido sabiendo
que en unos minutos llega uno mejor. Quedó **por debajo** de OurAgent, o sea
que no era cota superior de nada. La versión que quedó barre umbrales sobre el
turno completo y se queda con el mejor: es la mejor versión de *nuestra propia
política* con conocimiento perfecto. Cota superior de nuestra familia, no un
óptimo demostrado — y decirlo así es más defendible que reclamar un óptimo que
no calculamos.

## 5. Dos defectos de modelado que se encontraron midiendo

Ninguno se habría visto sin correr turnos completos.

**Se cobraban pedidos que terminaban después del cierre del turno.** AcceptAll
reportaba $17,001 y 196 entregas en 8 horas — un pedido cada 2.4 minutos. El
corredor sumaba las ganancias al aceptar sin comprobar que la entrega cupiera
en el turno. Toda la tabla era ficción.

**El repartidor nunca descansaba, y por eso se quedaba encerrado.** La pausa
solo se tomaba si estaba libre justo cuando llegaba una oferta; como casi
siempre tenía trabajo encolado, nunca paraba, y `mandatory_break` rechazaba
todo el resto del turno. Lo mismo con el tope de calor: baja el límite a 90
minutos entre 12:00 y 16:00, pero nada reiniciaba el contador, así que entre
los 90 y los 240 minutos el repartidor quedaba bloqueado horas. Eran el 50% de
nuestros rechazos. Ahora descansa al terminar lo que trae.

**Una constraint que encierra al repartidor no es una regla de seguridad, es un
defecto.** Es el criterio que se usó para distinguir las dos cosas.

## 6. Determinismo, verificado

El mismo seed produce un event log **byte-idéntico** bajo tres
`PYTHONHASHSEED` distintos, medido en subprocesos separados — que es la única
forma de detectar una dependencia del orden de iteración de un `set` o un
`dict`, porque dentro del mismo proceso nunca se manifiesta.

```
2e8fdc3df6e13fefcc461806f4efcb3e  PYTHONHASHSEED=0
2e8fdc3df6e13fefcc461806f4efcb3e  PYTHONHASHSEED=1
2e8fdc3df6e13fefcc461806f4efcb3e  PYTHONHASHSEED=2
```

El log de un turno trae los **8 tipos de evento** oficiales, en orden
cronológico, y pasa `validate_format.py --event-log`.

## 7. Limitaciones conocidas

Escritas porque un juez las va a encontrar y es mejor llegar antes.

**El mapa tiene 4 zonas; los ejemplos oficiales usan las zonas 5, 7 y 11.** Un
juez puede mandar `zone_dropoff: 11`, que nuestro `ZoneMap` no conoce. El
sistema lo maneja sin romperse (demanda neutral, sin toque de queda), y el
`detail` de la decisión lo dice con `zone_known: false` en vez de fallar en
silencio. Pero implica que **la constraint de zona marcada solo puede
dispararse en la zona 2**, así que el ensayo en vivo tiene que usar esa.
*Decisión pendiente con Persona 1: ampliar el mapa a ~16 zonas.*

**Un solo repartidor, secuencial.** Acepta pedidos que se encolan y los hace
uno tras otro. `max_backpack` existe en el perfil de vehículo pero el modelo no
hace reparto simultáneo real de varias entregas en paralelo.

**Los shocks se generan pero no alteran la economía.** El simulador emite
`surge`/`closure`/`delay`, y el `surge_multiplier` viaja por oferta, pero un
shock de surge en una zona no sube todavía el multiplicador de las ofertas
siguientes de esa zona.

**El Oracle es cota superior de nuestra familia de políticas**, no el óptimo
teórico. Ver sección 4.
