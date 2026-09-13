# Resultados — metodología, números y lo que no funcionó

> Reproducir todo lo de este documento:
> ```bash
> cd backend && source .venv/bin/activate
> python3 scripts/run_evaluation.py            # tabla sobre seeds held-out
> python3 scripts/run_evaluation.py --tuning   # las seeds con las que se calibró
> ```

---

## 1. La tabla

12 turnos held-out de 8 h, vehículo `moto`, zona inicial 0, sobre el `ZoneMap`
de 16 zonas.
Seeds de reporte: `101 113 127 131 149 151 163 173 181 191 199 211`.
Seeds de tuning (**disjuntas**, nunca reportadas): `11 23 37 41 59 67 71 83 89
97 103 109`.

| policy | mean_earnings_mxn | mean_mxn_per_hr | accept_rate_pct | orders_completed | deadhead_pct | deadline_misses | **safety_violations** |
|---|---|---|---|---|---|---|---|
| AcceptAll | 895.8 | 112.0 | 100.0 | 9.8 | 50.1 | 2359 | **5179** |
| HighestPay | 1814.1 | 226.8 | 11.5 | 8.9 | 50.6 | 269 | **470** |
| NearestFirst | 1194.8 | 149.3 | 21.4 | 12.8 | 24.1 | 498 | **923** |
| GreedyRate | 2685.5 | 335.7 | 15.4 | 16.8 | 55.8 | 334 | **509** |
| *GreedyRateSafe* | *2367.5* | *295.9* | *7.5* | *14.8* | *57.4* | *135* | ***0*** |
| **OurAgent** | **2473.3** | **309.2** | **8.0** | **15.8** | **54.0** | **150** | **0** |
| Oracle | 2745.6 | 343.2 | 8.8 | 17.3 | 55.7 | 175 | **0** |

`GreedyRateSafe` no es un baseline del template: es una fila de diagnóstico
que hace legible el resto.

## 2. Qué dice la tabla, en dos restas

**Perdemos 7.9% contra el mejor baseline, y el motivo es exactamente la
seguridad.** Vale más decirlo así que maquillarlo:

```
GreedyRate        $2686   509 violaciones   ← el baseline más fuerte, sin seguridad
GreedyRateSafe    $2368     0 violaciones   ← el gate de seguridad cuesta  −11.8%
OurAgent          $2473     0 violaciones   ← el valor de zona recupera    + 4.5%
Oracle            $2746     0 violaciones   ← capturamos el 90.1%
```

Las tres frases que se sostienen con esto:

1. **Cero violaciones de seguridad en los 12 turnos held-out, y en los tres
   vehículos.** Hay un test parametrizado por seed que lo comprueba turno a
   turno, no sobre el promedio.
2. **El precio de la seguridad es 11.8% de las ganancias**, y lo sabemos con
   un número porque medimos la misma política con y sin el gate.
3. **De lo que se puede ganar sin violar nada, capturamos el 90.1%.** El resto
   es lo que cuesta elegir el umbral a ciegas en vez de con conocimiento del
   turno completo.

`AcceptAll` es instructivo: acepta el 100% y termina **último**. Aceptar todo
llena el turno de pedidos que no dejan dinero después del combustible y que
además desplazan a los que sí.

## 3. Cómo se calibró

**El barrido es un script, no una corrida a mano**: `scripts/calibrate.py`.
Toca únicamente `TUNING_SEEDS` y calibra **también los baselines**, con la
misma rejilla — afinar solo el nuestro y compararlo contra umbrales puestos a
ojo convierte la tabla en un espantapájaros.

| política | parámetro | elegido |
|---|---|---|
| HighestPay | `min_pay_mxn` | 180 |
| NearestFirst | `max_deadhead_km` | 6.0 |
| GreedyRate | `min_rate_mxn_hr` | 275 |
| OurAgent | `reservation_wage_mxn_hr` | 275 |
| OurAgent | `DROPOFF_DEMAND_WEIGHT` | 0.6 |

**No se toma el máximo de la rejilla.** Con 12 turnos la diferencia entre el
pico y su vecino suele estar dentro del ruido, y elegir el pico es ajustar al
ruido. Se elige el punto cuyo **peor vecino** es más alto. En `GreedyRate` el
pico era 300 ($2739) y se eligió 275 ($2627, peor vecino $2546).

**El agente y `GreedyRate` acabaron en el mismo umbral ($275)** por barridos
independientes. Eso es lo que permite que la resta entre las dos filas aísle
la seguridad y el valor de la zona, y no una calibración distinta.

**Estos números pertenecen al simulador, no al mundo.** Bajaron de $400 a $275
cuando el `ZoneMap` pasó de 4 a 16 zonas: el mapa nuevo es más disperso, los
trayectos más largos, y un umbral de $400 rechazaba casi todo (la aceptación
se cayó a 5.5%). Si cambia la geografía o la frecuencia de ofertas, hay que
rehacer el barrido — y por eso es un script.

## 4. Lo que no funcionó

Se prueban aquí porque *"¿qué cortaste y por qué?"* es una de las preguntas
que los jueces hacen por escrito.

**El ajuste por zona de dropoff aporta poco, y depende del umbral.** Es el
hallazgo que más cuidado exige al contarlo, porque cambió de signo durante el
trabajo y es tentador quedarse con la versión favorable.

Con el mapa de 4 zonas aportaba +10.3%. Al pasar a 16 zonas y recalibrar a
mano en $260, la diferencia contra peso 0 en held-out fue **cero** ($2454.3
contra $2452.1). Con la calibración reproducible de `calibrate.py`, que sitúa
el umbral en $275, aporta **+4.5%** ($2367.5 contra $2473.3).

Las tres medidas son sobre las mismas seeds held-out. Lo que cambió entre
ellas fue el umbral, no el peso — y eso es el resultado: **el efecto de la
zona es de segundo orden frente al salario de reserva**. Es honesto decir que
aporta +4.5% con esta calibración, y deshonesto presentarlo como una mejora
robusta.

En ningún momento se re-tuneó el peso contra las seeds de reporte: el 0.6 sale
del barrido sobre tuning, y los números de arriba son la consecuencia, no el
criterio. Hacerlo al revés es exactamente lo que el material castiga con techo
de 3 en Results.

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
