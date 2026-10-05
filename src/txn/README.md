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
control sale 975: en cada vuelta se pierden tres de las cuatro restas.

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

## Decisiones de alcance

- **El bloqueo es por tabla.** Es el grano más simple que garantiza el aislamiento, y el
  que hace visible la espera en la demostración: dos transacciones que tocan filas
  distintas de la misma tabla se esperan.
- **El registro de deshacer vive en memoria.** Sirve para lo que pide el enunciado
  —agrupar operaciones y poder abortarlas—, no para recuperarse de una caída del proceso:
  eso es otro mecanismo, un log de escritura anticipada en disco.

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
