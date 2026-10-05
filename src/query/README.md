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

Una tabla con clave primaria en **heap** recibe automáticamente un índice sobre esa clave
—B+ por omisión, hash si se declara `PRIMARY KEY INDEX HASH`—. Sin él, comprobar que la
clave no se repite costaría un recorrido completo por cada inserción. Una tabla **sin clave
primaria** es un heap file sin ningún índice: todo se resuelve recorriéndola.

Una columna `UNIQUE` recibe también su índice, por el mismo motivo, y el motor rechaza un
valor repetido al insertar y al actualizar. Varios NULL no chocan entre sí.

Los **índices secundarios** (`CREATE INDEX … USING BTREE|HASH|RTREE`) solo existen sobre
heap files, porque necesitan que cada fila tenga una dirección física estable. Un archivo
secuencial mueve las filas al reorganizarse y un B+ agrupado al dividir nodos.

Cuando un índice devuelve muchas filas de una zona —una búsqueda por radio o por
polígono—, las direcciones **se ordenan por página antes de ir al heap**. Llegan repartidas
al azar, e ir a buscarlas en ese orden relee la misma página una y otra vez; ordenadas, cada
página se lee una sola vez. Es la idea del *bitmap heap scan* de PostgreSQL.

Un índice secundario **no guarda las filas cuyo valor es NULL**: ninguna búsqueda por
igualdad o por rango puede devolverlas, y un B+ no sabría dónde ordenarlas. La fila sigue
en la tabla y `IS NULL` la encuentra recorriéndola.

### Escrituras: todo o nada

`INSERT` de varias filas y `UPDATE` **validan antes de escribir la primera fila**: calculan
todos los valores nuevos, comprueban que cada uno corresponde a su columna y que ninguna
clave primaria ni columna `UNIQUE` quedaría repetida. Si algo falla —una división por cero
en la fila mil, un texto en una columna entera, una clave que ya existe— la tabla queda
como estaba.

Un `UPDATE` puede cambiar la clave primaria, con la semántica de SQL: lo que cuenta es el
**estado final**, no los intermedios. `UPDATE t SET id = id + 1` sobre las claves 1, 2 y 3
funciona, aunque la 2 esté ocupada cuando le toca a la 1, porque la misma sentencia la
libera. Para que dos filas no compartan clave ni por un instante se hace en tres tiempos:

1. las filas que **conservan** su clave se actualizan en su sitio;
2. las que la **cambian** se borran todas;
3. y después se insertan todas con su clave nueva.

Antes de tocar nada se comprueba que ninguna clave nueva choque con otra nueva ni con una
fila que la sentencia no mueve. Para el registro de deshacer, una fila que cambia de clave
es un borrado más una inserción.

## Tipos en las condiciones (`comparison.py`)

Solo se comparan valores de la misma familia: número con número, texto con texto, fecha
con fecha, booleano con booleano. `WHERE id = 'abc'` no devuelve cero filas: **se
rechaza**, diciendo qué columna choca con qué valor.

```
SELECT * FROM clientes WHERE id = 'abc'
→ la columna 'id' es INT y no se puede comparar con un texto ('abc')
```

- Una **fecha** se escribe como texto ISO, en las condiciones y al insertar:
  `WHERE fecha >= '2025-01-01'`, `VALUES (…, '2026-03-15')`.
- Un punto o un polígono se comparan con `=` y `<>`, pero no tienen orden.
- Si la condición compara una columna con un literal, el choque se detecta **antes de leer
  ninguna fila**, así que también falla sobre una tabla vacía.

La regla vive en un solo módulo y la aplican el evaluador y el planificador. Por eso una
condición responde lo mismo —las mismas filas o el mismo error— recorra la tabla o use un
índice; hay un test que cruza cada tipo de columna con cada tipo de literal y cada camino
de acceso para comprobarlo.

Cuando el literal es del tipo correcto pero la columna no podría guardarlo —un texto más
largo que el `VARCHAR`, un `5.5` frente a una columna entera—, ninguna fila puede
igualarlo: el planificador no le pide esa clave al índice y el recorrido responde vacío.

### Lo que se comprueba antes de leer ninguna fila

Una consulta mal escrita falla igual sobre una tabla de un millón de filas que sobre una
vacía. Al construir cada operador se valida:

| Qué | Ejemplo | Error |
|---|---|---|
| columnas y tablas | `SELECT nada FROM t` | la columna no existe |
| tipo de una constante frente a su columna | `nombre = -5` | STRING no se compara con un número |
| constantes que no se pueden calcular | `id = 1 / 0` | división por cero |
| forma de una llamada | `distancia(p)` | necesita 2 argumentos |
| tipo de las columnas que recibe una función | `distancia(nombre, POINT(…))` | la columna es STRING |
| argumentos constantes y opciones | `intersecta(p, 5)`, `metrica='manhattan'` | no es un polígono; métrica desconocida |
| tipo de lo que se agrega | `SUM(nombre)`, `MIN(ubicacion)` | no es numérico; un punto no tiene orden |

**Una expresión constante vale lo mismo que un literal.** `-5`, `2 + 8` o
`POINT(-12.0, -77.0)` no dependen de ninguna fila: se calculan una vez (`constant_value`)
y a partir de ahí se comprueban y se buscan en un índice como si se hubieran escrito ya
calculados.

Para las funciones se **ensaya la llamada** con los argumentos constantes y NULL en los que
dependen de la fila. Toda función admite NULL en cualquier argumento —y entonces devuelve
NULL—, así que si el ensayo falla es por algo que ninguna fila va a arreglar.

## Planificador (`planner.py`)

Su decisión interesante es **el camino de acceso**. Recorre los `AND` del `WHERE` buscando
una condición aprovechable:

| Condición | Con índice adecuado | Sin él |
|---|---|---|
| `col = valor` | `IndexLookup` (hash, B+ o, para un punto exacto, R-Tree) | `SequentialScan` |
| `col BETWEEN a AND b`, `col < v`, `v > col` | `IndexRange` (solo B+) o `PrimaryKeyRange` | `SequentialScan` |
| `a = 1 OR b = 2` | — | `SequentialScan` |
| `distancia(col, POINT(…)) < r` | `SpatialRangeScan` (R-Tree) | `SequentialScan` |
| `intersecta(col, POLYGON(…))` | `SpatialPolygonScan` (R-Tree) | `SequentialScan` |
| `ORDER BY distancia(col, POINT(…))` | `SpatialNearestScan` (R-Tree), sin ordenar | `SequentialScan` + `ExternalSort` |
| `ORDER BY clave_primaria` | `OrderedScan`, sin ordenar (secuencial o B+ agrupado) | `SequentialScan` + `ExternalSort` |

El valor puede ser cualquier expresión constante (`id = -5`, `id BETWEEN 10 * 10 AND 120`,
`ubicacion = POINT(-12.05, -77.04)`) y la comparación puede escribirse en cualquiera de los
dos sentidos (`5 < id` es `id > 5`).

Un índice explícito gana a la organización: en un heap file, "buscar por clave primaria"
sin índice sería un recorrido completo. Es lo que ocurre si se borra el índice de la clave
con `DROP INDEX`: la tabla sigue funcionando y la clave sigue sin poder repetirse, pero
cada búsqueda y cada inserción recorren el heap.

Un archivo secuencial y un B+ agrupado **ya guardan las filas en orden de clave**. Un
`ORDER BY` ascendente por la clave primaria no las vuelve a ordenar: el recorrido pasa a
llamarse `OrderedScan` y el plan no lleva `ExternalSort`, así que un `LIMIT` corta tras
leer las primeras páginas. Lo mismo con un rango sobre la clave (`PrimaryKeyRange`).

### Reuniones

Del `ON` se separan las igualdades entre una columna de cada lado; el resto queda como
condición residual que se evalúa sobre cada par.

| `ON` | Operador | Algoritmo |
|---|---|---|
| `a.x = b.x` | `HashJoin` | *grace hash join* |
| `a.x = b.x AND a.y = b.y` | `HashJoin`, clave compuesta | *grace hash join* |
| `a.x = b.x AND a.precio < b.tope` | `HashJoin` + condición residual | *grace hash join* |
| `a.precio < b.tope` (sin igualdad) | `NestedLoopJoin` | bucles anidados en bloques |

Los cuatro tipos —`INNER`, `LEFT`, `RIGHT` y `FULL [OUTER]`— funcionan con los dos
operadores: la fila que no encontró pareja sale con NULL en las columnas del otro lado.
Una clave NULL no empareja con nada. El `ON` entero se valida sobre las columnas de las
dos tablas antes de ejecutar, así que una columna ambigua o inexistente falla aunque las
tablas estén vacías.

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

Orden de aplicación: acceso → `JOIN` → `WHERE` → `GROUP BY`/`HAVING` → `ORDER BY` →
`DISTINCT` → `SELECT` → `LIMIT`. El `ORDER BY` y el `DISTINCT` van **antes** de la
proyección porque ahí las filas tienen tipos conocidos y se pueden volcar a disco; si el
`ORDER BY` nombra un alias del `SELECT`, se sustituye por su expresión.

### Agrupación y agregaciones (`aggregation.py`)

`COUNT`, `SUM`, `AVG`, `MIN` y `MAX`, con o sin `GROUP BY`.

- Se puede agrupar por **columnas, expresiones** (`GROUP BY n % 2`), **alias** del `SELECT`
  o **posiciones** (`GROUP BY 1`); `ORDER BY 2` también es una posición.
- Una agregación puede estar en el `SELECT`, en el `HAVING` o en el `ORDER BY`, sola o
  dentro de una expresión (`SUM(total) / COUNT(*)`, `HAVING MAX(n) - MIN(n) > 0`). Cada una
  se calcula una sola vez, y las expresiones se reescriben para leer la columna que produjo.
- Una columna calculada sin alias se llama como está escrita: `x * 2`, `SUM(n * 2)`.

**De cada grupo se guarda un acumulador por función, no sus filas**: un contador, un total,
el menor valor visto. Un grupo de un millón de filas ocupa lo mismo que uno de dos, y un
`COUNT(*)` sin `GROUP BY` usa memoria constante. Si los grupos tampoco caben en memoria, el
hashing externo vuelve a repartir (ver [`external/hash/`](../external/hash/README.md)).

`SUM` y `AVG` suman los enteros de forma exacta y los reales con **suma compensada**
(Neumaier): el error de redondeo de cada paso se guarda aparte y se devuelve al final, así
que el total no depende de cuántas filas tenga el grupo ni de en qué orden lleguen.

### `DISTINCT`

Deja pasar la primera fila de cada resultado distinto, **en el orden en que llegan** (el
del `ORDER BY`, si lo hay). Mientras los resultados distintos caben en el buffer se
entregan según llegan, de modo que un `LIMIT` corta pronto. Si no caben, el resto se
resuelve en disco: hashing externo para quedarse con la primera aparición de cada uno y
ordenamiento externo para devolverlos en su orden. El plan dice cuál de los dos caminos
se usó.

## Consultas espaciales

Tres archivos, uno por responsabilidad:

| Archivo | Qué hace |
|---|---|
| `functions.py` | las funciones escalares: `POINT`, `POLYGON`, `distancia`, `intersecta` |
| `spatial.py` | reconoce en el `WHERE` y el `ORDER BY` las búsquedas que un R-Tree resuelve |
| `spatial_operators.py` | los tres caminos de acceso por R-Tree |

**Las funciones** se evalúan como cualquier otra expresión, así que una consulta espacial
funciona **con o sin índice**: sin él, el plan es un recorrido completo con el mismo
`Filter`. El evaluador calcula una sola vez las llamadas constantes —el `POINT(…)` o el
`POLYGON(…)` de la consulta— en lugar de reconstruirlas por cada fila. Con un argumento
NULL devuelven NULL: `distancia` de una fila sin ubicación, o un `POINT(lat, lon)` armado
con dos columnas cuando falta una.

```sql
distancia(ubicacion, POINT(-12.0464, -77.0428))                         -- Haversine, en metros
distancia(ubicacion, POINT(-12.0464, -77.0428), metrica='euclidiana')   -- en grados
intersecta(ubicacion, POLYGON((-12.11, -77.05), (-12.11, -77.01), (-12.14, -77.01)))
```

**El reconocimiento** exige que el punto o el polígono sean constantes: si dependieran de
la fila no habría un único lugar del árbol por el que empezar. Lo usan el planificador,
para elegir el índice, y el motor, para decirle al panel de mapa qué figura dibujar; los
dos miran la consulta con las mismas reglas.

**Los operadores:**

| Operador | Cuándo | Qué entrega |
|---|---|---|
| `SpatialRangeScan` | `distancia(col, punto) < r` en el `WHERE` | las filas dentro del radio |
| `SpatialPolygonScan` | `intersecta(col, polígono)` en el `WHERE` | las filas dentro del polígono |
| `SpatialNearestScan` | `ORDER BY distancia(col, punto)` como única clave, ascendente | **todas** las filas, ya en orden de cercanía |

`SpatialNearestScan` sustituye a la vez al recorrido y al ordenamiento: el plan no lleva
`ExternalSort`, y un `LIMIT k` encima deja de pedirle filas tras la k-ésima. Un `WHERE` o un
`DISTINCT` conservan su orden, así que «las 10 gasolineras más cercanas» es ese operador con
un `Filter` encima. Con `JOIN` o `GROUP BY` no se aplica.

Entrega **exactamente** lo que entregaría recorrer y ordenar, también en los casos raros:

- las filas **sin ubicación** no están en el R-Tree, pero salen primero, que es donde un
  `ORDER BY` ascendente pone los NULL (`WHERE ubicacion IS NOT NULL` las quita). Solo se
  buscan si la tabla tiene alguna, y eso se sabe sin leer nada: son las que le faltan al
  índice;
- las filas **a la misma distancia** salen en el orden físico de la tabla, igual que tras
  un ordenamiento estable, así que un `LIMIT` que corta en medio de un empate devuelve las
  mismas filas por los dos caminos.

Tras ejecutarse, cada uno añade al plan **cuántos nodos del árbol abrió**:
`… (índice idx_tiendas_ubicacion, RTREE) · 5 de 610 nodos visitados`.

## Motor (`engine.py`)

Ejecuta `CREATE`/`DROP TABLE`, `CREATE`/`DROP INDEX`, `INSERT`, `UPDATE`, `DELETE`,
`SELECT` y `CREATE TABLE … FROM FILE` (carga de CSV con inferencia de tipos). Devuelve un
`QueryResult` con columnas, filas, **plan de ejecución** y tiempo medido.

Al cargar un CSV (`loader.py`) el tipo de cada columna se deduce de todos sus valores:
entero, real, fecha ISO, booleano (`true`/`false`), punto (`POINT(lat, lon)`) o texto, y
una celda vacía es NULL. Una columna toma el tipo que admite todo lo que trae; si mezcla
tipos que no conviven, es texto.

**Crear una tabla o un índice es todo o nada.** Si los archivos no se pueden crear —una
fila que no cabe en una página, una clave demasiado larga para un nodo del B+, un R-Tree
sobre una columna que no es un punto— no queda ni la entrada del catálogo ni un archivo a
medias: una tabla registrada que no se puede abrir no se podría consultar, volver a crear
ni borrar.

El motor **no sabe qué es una transacción**: solo avisa de cada fila que cambia al
`Journal` que le pasen. Quien quiera deshacer, que se apunte (ver [`txn/`](../txn/README.md)).

## Alcance

El motor cubre el SQL que pide el enunciado y lo que hace falta para usarlo con comodidad.
Tres cosas quedan deliberadamente fuera, porque el enunciado no las pide y ninguna
estructura evaluada depende de ellas: las subconsultas, los índices sobre varias columnas a
la vez y las columnas de geometrías que no sean puntos (un polígono existe como valor de
una consulta, no como columna).

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

`test_spatial.py` ejecuta cada consulta espacial **dos veces, sin índice y con R-Tree**, y
compara las dos respuestas entre sí y con el cálculo directo en Python: que el índice
acelere no sirve de nada si cambia el resultado.

`test_typed_conditions.py` hace lo mismo con los tipos: la misma condición sobre una tabla
sin índices, un heap con índices, un archivo secuencial y un B+ agrupado, para cada tipo
de columna y de literal. `test_writes.py` comprueba que una escritura que falla no deja
nada a medias, en las tres organizaciones, y que un `UPDATE` de la clave primaria o de una
columna `UNIQUE` llega al estado final correcto.

`test_constants.py` comprueba que una expresión constante llega al índice igual que un
literal y que la que no se puede calcular se rechaza sobre una tabla vacía.
`test_predicates.py` recorre `LIKE`, `IN`, los NULL y cada forma de llamar mal a una
función. `test_ddl.py` cubre lo que cambia el catálogo: índices repetidos, tablas e índices
que no se pueden crear —y que no dejan rastro— y una tabla a la que se le quita el índice
de su clave.

`test_joins.py` cruza los cuatro tipos de reunión con claves simples, compuestas, NULL y
condiciones sin igualdad, y los compara con el cálculo directo. `test_grouping.py` y
`test_aggregation.py` cubren la agrupación por expresiones, alias y posiciones, las
agregaciones dentro de expresiones, la suma compensada y **más grupos de los que caben en
memoria**. `test_distinct.py` hace lo mismo con el `DISTINCT`, en memoria y en disco.
