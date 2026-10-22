# Transacciones y concurrencia

## El problema

Dos usuarios tocando la misma fila a la vez se pisan. El caso clásico:

```
  hilo A: lee saldo = 100          hilo B: lee saldo = 100
  hilo A: escribe 99               hilo B: escribe 99
                    → se perdió una de las dos restas
```

La demo [`demos/concurrencia.py`](../../demos/concurrencia.py) reproduce exactamente eso:
con 4 hilos y 25 vueltas cada uno sobre un saldo de 1 000, el esperado es 900 y sin
control sale 975: en cada vuelta se pierden tres de las cuatro restas. Con **la misma
carga** dentro de `BEGIN … COMMIT` sale 900. Cuando dos hilos leen el saldo a la vez y los
dos quieren escribir, cada uno espera al otro: el gestor detecta ese interbloqueo, aborta a
uno y ese uno lo vuelve a intentar, que es lo que hace cualquier aplicación sobre un gestor
con bloqueo en dos fases.

## Las tres piezas

```
  Session ──► LockManager   ¿puedo tocar esta tabla?
     │
     ├──────► Transaction   registro de deshacer
     │
     └──────► Engine        ejecuta la sentencia y va contando qué cambió
```

Reparto de responsabilidades: **el motor no sabe qué es una transacción**. Solo notifica
cada fila que crea, borra o modifica a través del protocolo `Journal`. La transacción
implementa ese protocolo y se apunta lo necesario para deshacer.

## Bloqueos (`lock_manager.py`)

Granularidad: **tabla**. Dos modos:

|            | mantiene S | mantiene X |
|------------|-----------|-----------|
| **pide S** | compatible | espera     |
| **pide X** | espera     | espera     |

`SELECT` pide compartido; `INSERT`, `UPDATE`, `DELETE` y el DDL piden exclusivo. Una
transacción que ya tiene S y pide X **asciende** el bloqueo.

El recurso que se bloquea es siempre **la tabla**. `EXPLAIN` bloquea las tablas de la
consulta que explica, y `DROP INDEX nombre` —que no nombra ninguna— bloquea la tabla dueña
de ese índice: borrarlo cambia la tabla, y no puede hacerse mientras otra transacción la
esté usando.

Los bloqueos se sueltan al confirmar o abortar, nunca antes: es **bloqueo en dos fases**
(primero se adquiere todo, luego se suelta todo), que es lo que impide que otra transacción
vea a medias lo que esta está cambiando.

### Nadie se queda esperando para siempre

Un pedido compartido nuevo **hace cola detrás de los exclusivos que llegaron antes** al
mismo recurso, aunque sea compatible con los compartidos que ya están concedidos. Sin esa
regla, mientras quede algún lector, un escritor no entra nunca: los lectores nuevos se
suman a los que ya leen y el escritor espera hasta agotar su plazo. Es la *inanición del
escritor*, y la demo la sacó a la luz: con cuatro hilos que leen y escriben el mismo saldo
dentro de transacciones, tres morían por espera agotada y se perdían descuentos. Ascender
de compartido a exclusivo no hace cola: quien ya tiene el recurso no espera detrás de
quien todavía no lo tiene. Esas esperas nuevas también son aristas del grafo de espera,
así que un ciclo que pase por ellas se detecta igual.

### Interbloqueos

```
  A tiene 'cuentas' y pide 'otra'
  B tiene 'otra'    y pide 'cuentas'      ← ciclo: nadie avanzará nunca
```

Antes de ponerse a esperar, el gestor recorre el **grafo de espera** buscando volver a sí
mismo. Si encuentra el ciclo, en vez de esperar para siempre lanza `DeadlockError` y la
sesión aborta esa transacción; la otra continúa y confirma. Como red de seguridad, toda
espera tiene además un tiempo máximo (`lock_timeout_seconds`).

## Transacciones (`transaction.py`)

El registro de deshacer es una lista de cambios en orden. Abortar es recorrerla **al revés**
aplicando el inverso:

| Cambio | Inverso |
|---|---|
| `INSERT` de una fila | borrarla |
| `DELETE` de una fila | volver a insertarla |
| `UPDATE` de una fila | restaurar el valor anterior |

El orden inverso importa: si una transacción inserta una fila y luego la modifica, deshacer
en orden directo intentaría restaurar una fila que aún no existe.

El motor anota cada fila **en cuanto la cambia** y **tal como quedó guardada** —la fecha
ya como fecha, no como el texto que escribió el usuario—, porque deshacer consiste en
volver a encontrar esa fila en la tabla.

### Una sentencia que falla no deja nada

Si una sentencia falla a mitad dentro de una transacción, la sesión **deshace solo lo que
esa sentencia alcanzó a cambiar** y la transacción sigue abierta, con sus cambios
anteriores intactos. Confirmar después no puede dejar escrito medio `INSERT`. En
autocommit, la sentencia fallida se aborta entera.

## Sesión (`session.py`)

Es la puerta por la que se ejecuta SQL. Decide qué tablas bloquear y en qué modo, y qué
hacer si algo falla.

- Fuera de `BEGIN`, cada sentencia va en **autocommit**: bloquea, ejecuta, suelta.
- Dentro de `BEGIN … COMMIT`, los bloqueos se conservan hasta el final.
- `ROLLBACK` (y cerrar la sesión con algo abierto) deshace todo.
- Un interbloqueo aborta la transacción automáticamente y propaga el error.
- Cualquier otro error deshace esa sentencia y deja la transacción abierta.

`END TRANSACTION` y `COMMIT` son lo mismo, como pide el enunciado.

## Uso

```python
from query.engine import Engine
from txn import LockManager, Session, TransactionManager

engine, locks, manager = Engine(config), LockManager(config), TransactionManager()
with Session(engine, locks, manager) as session:
    session.execute("BEGIN TRANSACTION")
    session.execute("UPDATE cuentas SET saldo = saldo - 100 WHERE id = 1")
    session.execute("UPDATE cuentas SET saldo = saldo + 100 WHERE id = 2")
    session.execute("END TRANSACTION")
```

Cada usuario concurrente necesita **su propia sesión**; el motor y el gestor de bloqueos se
comparten.

Los bloqueos de tabla protegen las filas, pero dos lecturas corren a la vez. Por eso el motor
no comparte entre consultas nada que se escriba: cada operador que se apoya en disco
(ordenamiento, `JOIN`, `GROUP BY`, `DISTINCT`) usa un directorio temporal con nombre
aleatorio, y un lock propio del motor serializa el DDL y la apertura de tablas.

## Decisiones de alcance

- **El bloqueo es por tabla.** Es el grano más simple que garantiza el aislamiento, y el
  que hace visible la espera en la demostración: dos transacciones que tocan filas
  distintas de la misma tabla se esperan.
- **El registro de deshacer vive en memoria.** Sirve para lo que pide el enunciado
  —agrupar operaciones y poder abortarlas—, no para recuperarse de una caída del proceso:
  eso es otro mecanismo, un log de escritura anticipada en disco.
- **COMMIT escribe en disco antes de soltar los bloqueos** (política *force*). Las páginas
  que la transacción tocó, de la tabla y de sus índices, se vuelcan con `fsync`. Si el
  volcado falla, la transacción se aborta. Lo confirmado sobrevive a que el proceso
  termine sin cerrar el motor. Una caída *a mitad* de una transacción sí puede dejar
  cambios a medias: arreglar eso es justo lo que haría el log de escritura anticipada.
- **El DDL va fuera de las transacciones.** Dentro de `BEGIN`, un `CREATE` o un `DROP` da
  `DefinitionInTransactionError` y la transacción sigue abierta, como en Oracle o MySQL.
  El registro guarda filas, no archivos, así que un ROLLBACK no podría devolver una tabla
  borrada.
- **Terminar una transacción siempre suelta sus bloqueos**, aunque el deshacer falle; si
  falla el deshacer de una sola sentencia, se aborta la transacción entera.
- **Deshacer busca cada fila por su clave primaria**, que es una búsqueda por índice: un
  ROLLBACK de `m` cambios cuesta `m` búsquedas y no `m` recorridos. Solo una tabla sin
  clave primaria recorre el heap para encontrar la fila.
- **La espera por un bloqueo tiene un plazo fijo** (`lock_timeout_seconds`), que no se
  reinicia cuando otras transacciones sueltan bloqueos ajenos.

## Tests y demo

```bash
.venv/bin/python -m pytest tests/txn -q
.venv/bin/python demos/concurrencia.py
```

Los tests cubren: compatibilidad de modos, ascenso de S a X, espera real entre hilos,
tiempo de espera agotado, **detección de ciclo**, autocommit, deshacer de `INSERT`,
`DELETE`, `UPDATE` y de varios cambios en orden inverso, deshacer de una sentencia que
falla a mitad, de un cambio de clave primaria y de filas con fechas y puntos, liberación
de bloqueos al confirmar, y dos escenarios con hilos: transferencias concurrentes que conservan el total y
un interbloqueo del que exactamente una de las dos transacciones sale abortada.
