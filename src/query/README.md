# Procesamiento de consultas

De un texto SQL a un resultado. Cinco piezas, cada una con una responsabilidad:

```
  "SELECT ... "
       │
       ▼  sql/          texto → AST
       ▼  catalog.py    ¿qué tablas hay? ¿qué tipos? ¿qué índices?
       ▼  planner.py    AST → árbol de operadores (elige el camino de acceso)
       ▼  operators.py  ejecuta el árbol, fila a fila
       ▼  engine.py     fachada: mide, cuenta filas y devuelve el plan
  QueryResult(columns, rows, plan, elapsed_ms)
```

## Catálogo (`catalog.py`)

Guarda en `catalog.json` qué columnas tiene cada tabla, cómo se almacena y qué índices
tiene. Es la **única** pieza que traduce los tipos de SQL a los del almacenamiento:
`VARCHAR(30)` → `STRING(30)`, `TEXT` → `STRING(text_length)`, `BIGINT` → `INT`.

## Tabla (`table.py`)

Une la organización física con sus índices y mantiene la coherencia: si entra o sale una
fila, todos sus índices se enteran en la misma operación.

| `CREATE TABLE` | Organización | Cómo se lee |
|---|---|---|
| `id INT PRIMARY KEY` | heap file + índice B+ sobre la clave | por índice o recorrido |
| `id INT PRIMARY KEY INDEX SEQ` | archivo secuencial | ordenado por clave |
| `id INT PRIMARY KEY INDEX BTREE` | B+ agrupado | ordenado por clave |

Una tabla con clave primaria en **heap** recibe automáticamente un índice B+ sobre esa
clave. Sin él, comprobar que la clave no se repite costaría un recorrido completo por cada
inserción.

Los **índices secundarios** (`CREATE INDEX … USING BTREE|HASH`) solo existen sobre heap
files, porque necesitan que cada fila tenga una dirección física estable. Un archivo
secuencial mueve las filas al reorganizarse y un B+ agrupado al dividir nodos.

## Planificador (`planner.py`)

Su decisión interesante es **el camino de acceso**. Recorre los `AND` del `WHERE` buscando
una condición aprovechable:

| Condición | Con índice adecuado | Sin él |
|---|---|---|
| `col = valor` | `IndexLookup` (hash o B+) | `SequentialScan` |
| `col BETWEEN a AND b`, `col < v` | `IndexRange` (solo B+) o `PrimaryKeyRange` | `SequentialScan` |
| `a = 1 OR b = 2` | — | `SequentialScan` |

Un índice explícito gana a la organización: en un heap file, "buscar por clave primaria"
sin índice sería un recorrido completo.

El `Filter` se conserva aunque el índice ya haya acotado: cuesta poco y garantiza que el
resultado es correcto aunque el camino de acceso devuelva de más.

## Operadores (`operators.py`)

Modelo **Volcano**: cada operador es un iterador que pide filas a su hijo. Nadie materializa
la tabla entera salvo donde el algoritmo lo exige, y ahí se usa disco.

```
Limit
└─ Projection
   └─ ExternalSort            ← ordenamiento externo (external/sort)
      └─ HashAggregate        ← hashing externo (external/hash)
         └─ Filter
            └─ IndexLookup    ← el camino de acceso elegido
```

Orden de aplicación: acceso → `WHERE` → `GROUP BY`/`HAVING` → `ORDER BY` → `SELECT` →
`DISTINCT` → `LIMIT`. El `ORDER BY` va **antes** de la proyección para poder ordenar sobre
filas con tipos conocidos; si nombra un alias del `SELECT`, se sustituye por su expresión.

Agregaciones: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, con o sin `GROUP BY`. Las que aparecen en
el `HAVING` se calculan una sola vez y el `HAVING` se reescribe para apuntar a esa columna.

## Motor (`engine.py`)

Ejecuta `CREATE`/`DROP TABLE`, `CREATE`/`DROP INDEX`, `INSERT`, `UPDATE`, `DELETE`,
`SELECT` y `CREATE TABLE … FROM FILE` (carga de CSV con inferencia de tipos). Devuelve un
`QueryResult` con columnas, filas, **plan de ejecución** y tiempo medido.

El motor **no sabe qué es una transacción**: solo avisa de cada fila que cambia al
`Journal` que le pasen. Quien quiera deshacer, que se apunte (ver [`txn/`](../txn/README.md)).

## Límites conocidos

- Solo `INNER JOIN`, y el `ON` debe ser una igualdad entre una columna de cada lado.
- `GROUP BY` sobre columnas, no sobre expresiones.
- Índices sobre una sola columna.
- Sin subconsultas.

## Uso

```python
from config import EngineConfig
from query.engine import Engine

with Engine(EngineConfig()) as engine:
    engine.execute("CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(30))")
    engine.execute("INSERT INTO alumnos VALUES (1, 'ana')")
    result = engine.execute("SELECT * FROM alumnos WHERE id = 1")
    print(result.columns, result.rows)
    print(result.plan.render())
```

## Tests

```bash
.venv/bin/python -m pytest tests/query -q
```

`test_engine.py` recorre el SQL completo de extremo a extremo; `test_planner.py` comprueba
que **el plan elegido es el esperado**: que el hash se use para igualdad y no para rangos,
que un `OR` no pueda usar índice, y que las tablas ordenadas aprovechen su orden.
