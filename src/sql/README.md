# Front-end SQL: del texto al árbol de sintaxis

El paquete `sql` convierte una cadena en un AST inmutable y no toca nada más: ni el
catálogo, ni el almacenamiento. Por eso se puede probar solo.

| Archivo | Qué hace |
|---|---|
| `tokens.py` | el vocabulario: tipos de token y palabras reservadas |
| `lexer.py` | texto → tokens, cada uno con su línea y su columna |
| `parser.py` | tokens → AST, por descenso recursivo: una función por regla de la gramática |
| `nodes.py` | los nodos del AST, todos inmutables |
| `errors.py` | errores con posición y con la línea ofensora |

```python
from sql import parse, parse_script

statement = parse("SELECT * FROM tiendas WHERE id = 1")
statements = parse_script("BEGIN; UPDATE t SET v = 1; COMMIT;")
```

El parser acepta el núcleo relacional de la Parte 1 y las extensiones espacial, de texto y
multimedia de las Partes 2 a 4. **No valida nombres ni tipos contra el catálogo**: eso lo
hace el motor al construir el plan, antes de leer ninguna fila.

La sintaxis de texto de la Parte 3 (`MATCH`, `SCORE()`, `USING TF_IDF | BM25`, `WITH (k1,
b)` y el índice `INVERTED`) y la multimedia de la Parte 4 (`SIMILAR_TO`,
`SIMILARITY_SCORE()`, `USING HNSW | IVF`, `WITH METRIC=...` y los índices `HNSW` e `IVF`)
se ejecutan: ver [Búsqueda de texto](#búsqueda-de-texto) y
[Búsqueda por similitud](#búsqueda-por-similitud).

```bash
.venv/bin/python -m pytest tests/sql -q
```

## Gramática soportada

### Convenciones léxicas

| Elemento | Regla |
|---|---|
| Palabras clave | insensibles a mayúsculas (`select` = `SELECT`) |
| Identificadores | letra (incluye acentuadas), `_` o `$` iniciales; luego alfanuméricos, `_` o `$` |
| Identificadores citados | `"entre comillas dobles"`; única forma de usar una palabra reservada como nombre |
| Cadenas | `'entre comillas simples'`; `''` inserta una comilla; la barra invertida es literal (rutas de Windows) |
| Números | `42`, `3.14`, `1e3`, `2.5E-4`; solo dígitos ASCII |
| Comentarios | `-- hasta fin de línea` y `/* en bloque */` |
| Separador | `;` entre sentencias |

`"texto"` es ambivalente por diseño: se lee como nombre donde se espera un identificador y como
cadena donde se espera un valor, para admitir `USING INDEX BTREE("id")` y `FROM FILE "ruta.csv"`.

### Sentencias

```
statement    ::= select | insert | update | delete | explain
               | create_table | create_index | drop_table | drop_index
               | 'BEGIN' ['TRANSACTION'] | 'END' ['TRANSACTION']
               | 'COMMIT' ['TRANSACTION'] | 'ROLLBACK' ['TRANSACTION']

explain      ::= 'EXPLAIN' ['ANALYZE'] select

select       ::= 'SELECT' ['DISTINCT'] projection (',' projection)*
                 'FROM' table_ref join*
                 ['WHERE' expression]
                 ['GROUP' 'BY' expression (',' expression)*]
                 ['HAVING' expression]
                 ['USING' method]
                 ['WITH' options]
                 ['ORDER' 'BY' order_item (',' order_item)*]
                 ['LIMIT' integer] ['OFFSET' integer]

projection   ::= '*' | name '.' '*' | expression [['AS'] alias]
table_ref    ::= name [['AS'] alias]
join         ::= ['INNER' | ('LEFT'|'RIGHT'|'FULL') ['OUTER']] 'JOIN' table_ref 'ON' expression
order_item   ::= expression ['ASC' | 'DESC']

insert       ::= 'INSERT' 'INTO' name ['(' name (',' name)* ')']
                 'VALUES' row (',' row)*
update       ::= 'UPDATE' name 'SET' name '=' expression (',' name '=' expression)*
                 ['WHERE' expression]
delete       ::= 'DELETE' 'FROM' name ['WHERE' expression]

create_table ::= 'CREATE' 'TABLE' ['IF' 'NOT' 'EXISTS'] name
                 ( '(' column_def (',' column_def)* [',' 'PRIMARY' 'KEY' '(' name+ ')'] ')'
                 | 'FROM' 'FILE' string ['USING' 'INDEX' index_spec] )
column_def   ::= name data_type constraint*
constraint   ::= 'PRIMARY' 'KEY' | 'NOT' 'NULL' | 'NULL' | 'UNIQUE' | 'INDEX' method
create_index ::= 'CREATE' 'INDEX' ['IF' 'NOT' 'EXISTS'] [name]
                 'ON' name 'USING' index_spec
index_spec   ::= method '(' name (',' name)* ')' ['WITH' options]
drop_table   ::= 'DROP' 'TABLE' ['IF' 'EXISTS'] name
drop_index   ::= 'DROP' 'INDEX' ['IF' 'EXISTS'] name ['ON' name]

options      ::= option (',' option)* | '(' option (',' option)* ')'
option       ::= name '=' (number | string | identifier | 'TRUE' | 'FALSE' | 'NULL')
```

`END TRANSACTION` y `COMMIT` producen el mismo nodo: confirmar la transacción.

### Tipos de columna

`INT`/`INTEGER`, `BIGINT`, `FLOAT`/`REAL`, `DOUBLE`, `BOOL`/`BOOLEAN`, `CHAR(n)`, `VARCHAR(n)`,
`TEXT`, `DATE`, `POINT`, `VECTOR(n)`/`ARRAY(n)`, `BLOB`.

`CHAR` y `VARCHAR` admiten tamaño opcional, `VECTOR` lo exige, el resto lo rechaza.

`TEXT` no tiene longitud máxima: el texto vive fuera de la fila y la fila guarda su
dirección (ver [`query/README.md`](../query/README.md#columnas-text)). Se compara, se filtra
con `LIKE`, se ordena y se agrupa igual que un `VARCHAR`, pero no admite un índice `BTREE` ni
`HASH`, ni `UNIQUE`, ni ser clave primaria.

No hay un literal propio para las fechas: se escriben como texto ISO, `'2026-03-15'`, y
es el motor quien las convierte al guardarlas en una columna `DATE` o al compararlas con
una.

### Métodos de acceso

| Cláusula `method` | Estructura |
|---|---|
| `SEQ`, `SEQUENTIAL` | archivo secuencial paginado |
| `BTREE`, `BPTREE`, `BPLUSTREE` | índice B+ |
| `HASH`, `EHASH`, `EXTENDIBLE_HASH` | hash extensible |
| `RTREE` | R-Tree espacial |
| `INVERTED`, `SPIMI` | índice invertido |
| `IVF`, `HNSW` | índices de similitud sobre la columna de las rutas (`SIMILAR_TO`) |
| `TF_IDF`, `TFIDF`, `BM25` | funciones de ranking de texto (solo en `SELECT ... USING`) |

### Expresiones

Precedencia de menor a mayor:

```
OR  <  AND  <  NOT  <  comparación  <  + -  <  * / %  <  - unario  <  primaria
```

- Comparación: `=` `==` `!=` `<>` `<` `<=` `>` `>=`
- Predicados: `x [NOT] BETWEEN a AND b`, `x [NOT] IN (…)`, `x [NOT] LIKE p`, `x IS [NOT] NULL`
- Primarias: literales, `col`, `tabla.col`, `f(args)`, `(expr)`, `(a, b)` (tupla, p. ej. vértices
  de un polígono), `*` como argumento de agregado
- Llamadas con argumentos nombrados: `SIMILAR_TO('foto.jpg', k = 10)`; los posicionales van primero

Dos límites hacen que una consulta patológica falle como error de sintaxis y no como
desbordamiento de pila. El anidamiento de paréntesis, `NOT` y `-` llega a
`MAX_NESTING_DEPTH` (32) niveles, porque cada uno recorre el descenso entero del parser. La
profundidad total del árbol llega a `MAX_EXPRESSION_DEPTH` (200), y ahí cuenta también cada
operador de una cadena: `a OR b OR c` es un árbol de tres niveles. Esa profundidad se mide
sobre el árbol terminado (`expression_depth`, sin recursión), así que dos cadenas cortas
encajadas, como `a * b * … + c + …`, suman sus niveles. Una lista larga de alternativas se
escribe con `IN (...)`.

### Funciones espaciales

Las de la Parte 2. Se usan en cualquier expresión: `WHERE`, `SELECT`, `ORDER BY`, `VALUES`.

| Función | Devuelve |
|---|---|
| `POINT(latitud, longitud)` | un punto geográfico; latitud en `[-90, 90]`, longitud en `[-180, 180]` |
| `POLYGON(v1, v2, v3, …)` | un polígono; cada vértice es `(lat, lon)` o `POINT(lat, lon)`, y hacen falta al menos tres |
| `distancia(a, b)` | metros entre dos puntos, por Haversine |
| `distancia(a, b, metrica='euclidiana')` | distancia euclidiana, en grados |
| `intersecta(punto, POLYGON(…))` | si el punto cae dentro del polígono o en su borde |

El orden de las coordenadas es siempre **latitud y luego longitud**, como en el enunciado.
La métrica admite `'haversine'` (o `'geodesica'`) y `'euclidiana'` (o `'euclidean'`), con
o sin comillas; el argumento también se puede llamar `metric`.

```sql
CREATE TABLE tiendas (id INT PRIMARY KEY, nombre VARCHAR(40), ubicacion POINT INDEX RTREE);
CREATE INDEX idx_tiendas_ubicacion ON tiendas USING RTREE (ubicacion);

INSERT INTO tiendas VALUES (1, 'Centro', POINT(-12.0464, -77.0428));

-- por radio: tiendas a menos de 5 km
SELECT * FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000;

-- k vecinos más cercanos
SELECT * FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 10;

-- dentro de un polígono
SELECT * FROM tiendas WHERE intersecta(ubicacion,
    POLYGON((-12.112, -77.046), (-12.112, -77.010), (-12.136, -77.008)));
```

Un índice `RTREE` solo se declara sobre una columna `POINT`, y una columna `POINT` solo
admite `RTREE`. En un CSV, un punto se escribe igual que en SQL: `"POINT(-12.0464, -77.0428)"`.

### Búsqueda de texto

Las de la Parte 3. `MATCH` y `SCORE()` se escriben como llamadas a función, pero no se
evalúan fila a fila: las resuelve el índice invertido de la columna.

| Forma | Qué es |
|---|---|
| `CREATE INDEX [nombre] ON t USING INVERTED (col) [WITH (idioma='es')]` | índice invertido sobre una columna `VARCHAR` o `TEXT`; `idioma` es `es` (por omisión) o `en` |
| `CREATE TABLE t FROM FILE "ruta.csv" USING INDEX INVERTED(col)` | la tabla es un heap y el índice se construye al cargar, como con `RTREE` |
| `WHERE MATCH(col, 'consulta')` | las filas que tienen algún término de la consulta |
| `WHERE MATCH(col, 'a AND b AND NOT c')` | las filas que cumplen la expresión booleana (ver abajo) |
| `USING TF_IDF` / `USING BM25` | el ranking; sin `USING`, **BM25** |
| `WITH (k1 = 1.5, b = 0.6)` | los parámetros de BM25 de esta consulta; el que falte sale de la configuración |
| `SCORE()` | el puntaje de cada fila, en cualquier expresión del mismo `SELECT` |

```sql
CREATE INDEX ON productos USING INVERTED (texto) WITH (idioma='es');

SELECT id, titulo, SCORE() AS relevancia FROM productos
    WHERE MATCH(texto, 'auriculares inalámbricos') AND precio < 100
    USING BM25 WITH (k1 = 1.5, b = 0.6)
    ORDER BY relevancia DESC LIMIT 10;
```

**Consultas booleanas.** Dentro del texto de `MATCH`, las palabras `AND`, `OR` y `NOT` **en
mayúsculas** y los paréntesis forman una expresión; en minúsculas («and») son palabras de
la consulta. El SQL no las ve: para el parser la consulta sigue siendo un literal de texto,
y la analiza el motor con el analizador del índice
([`text/boolean_query.py`](../text/boolean_query.py)).

```
expresión   := conjunción { [OR] conjunción }
conjunción  := negación { AND negación }
negación    := NOT negación | primario
primario    := palabra | ( expresión )
palabra     := lo que hay entre espacios y paréntesis
```

- Precedencia `NOT` > `AND` > `OR`: `a OR b AND NOT c` es `a OR (b AND (NOT c))`.
- Dos operandos seguidos sin operador se unen con `OR`, como el texto libre: `funda (iphone
  OR samsung)` es `funda OR iphone OR samsung`, y `cable usb AND rojo` es
  `cable OR (usb AND rojo)`.
- Una consulta sin operadores ni paréntesis no es booleana: va por el camino del texto
  libre, con los mismos puntajes.
- Cada palabra pasa por el analizador. Si da varios términos (`iphone-12`), es el `AND` de
  todos; si no da ninguno (una stopword, como `de`), se quita con su operador:
  `cable AND de` es `cable`. Si no queda ninguno, la consulta no encuentra nada.
- Un `NOT` resta: tiene que quedar unido con `AND` a algo positivo (`funda AND NOT
  silicona`, también `NOT silicona AND funda`). Una consulta que solo excluye —`NOT cable`,
  `cable OR NOT usb`— es un error, porque pediría casi todo el índice.
- Las filas se ordenan con TF-IDF o BM25 sobre los **términos positivos**, los que no están
  bajo un `NOT` (o bajo un número par de ellos); los negados no puntúan, y `SCORE()`,
  `LIMIT` y los filtros funcionan igual.

```sql
SELECT titulo, SCORE() AS relevancia FROM catalogo
    WHERE MATCH(texto, 'funda AND iphone AND NOT silicona') LIMIT 10;
SELECT id FROM catalogo WHERE MATCH(texto, '(auriculares OR cascos) AND NOT bluetooth');
```

Lo que el motor exige, con un error antes de leer ninguna fila:

- `MATCH(columna, 'consulta')`: dos argumentos, una columna `VARCHAR` o `TEXT` con índice
  `INVERTED` (si no lo tiene, el error dice el `CREATE INDEX` que falta) y una consulta que
  sea un texto constante;
- va en el `WHERE` de un `SELECT`, unida al resto con `AND`: no dentro de un `OR` ni de un
  `NOT`, ni en el `SELECT`, el `ORDER BY`, un `UPDATE` o un `DELETE`; y una sola por
  consulta;
- `SCORE()` no lleva argumentos y solo existe en un `SELECT` con un `MATCH`;
- `USING` es `TF_IDF` o `BM25`; `WITH` lleva solo `k1` y `b`, números en rango, y solo con
  BM25;
- una consulta booleana bien formada (`BooleanQueryError` si no): paréntesis que cierran
  («falta cerrar el paréntesis de la posición 1»), operadores con sus dos operandos («AND
  de la posición 7 no tiene operando a la derecha») y algún término positivo («la consulta
  solo excluye documentos»).

El planificador y sus decisiones están en
[`query/README.md`](../query/README.md#búsqueda-de-texto).

### Búsqueda por similitud

Las de la Parte 4. Como `MATCH`, `SIMILAR_TO` y `SIMILARITY_SCORE()` se escriben como
llamadas a función pero las resuelve un índice: el `HNSW` o el `IVF` de la columna que
guarda la ruta de cada imagen o audio.

| Forma | Qué es |
|---|---|
| `CREATE INDEX [nombre] ON t USING HNSW (ruta) [WITH (...)]` | extrae SIFT o MFCC de cada archivo, entrena el vocabulario y construye un HNSW sobre los vectores TF-IDF; opciones `metric`, `m`, `ef_construction`, `ef_search` y `vocabulary` |
| `CREATE INDEX [nombre] ON t USING IVF (ruta) [WITH (...)]` | lo mismo con IVF; opciones `metric`, `lists`, `probes` y `vocabulary` |
| `WHERE SIMILAR_TO('consulta.jpg', k = 10)` | las 10 filas cuyo archivo más se parece al de la consulta |
| `WHERE SIMILAR_TO(ruta, 'consulta.jpg', k = 10)` | lo mismo nombrando la columna, si la tabla tiene varias con índice |
| `USING HNSW` / `USING IVF` | el índice; sin `USING`, el único índice de similitud que tenga la tabla |
| `WITH METRIC = cosine` | `euclidean`, `dot` o `cosine` (también `euclidiana`, `producto_punto`, `coseno`); sin ella, la del índice |
| `WITH ef_search = 100` / `WITH probes = 20` | el esfuerzo de esta consulta en HNSW o en IVF |
| `SIMILARITY_SCORE()` | el puntaje de cada fila, mayor es más parecido, en cualquier expresión del `SELECT` |

```sql
CREATE INDEX idx_img ON imagenes USING HNSW (ruta) WITH (METRIC = cosine, M = 16);

SELECT titulo, categoria, SIMILARITY_SCORE() AS score FROM imagenes
    WHERE SIMILAR_TO('data/datasets/abo/imagenes/c8/c8fa4944.jpg', k = 10)
      AND categoria <> 'CELLULAR_PHONE_CASE'
    USING HNSW WITH METRIC = cosine, ef_search = 100
    ORDER BY score DESC;
```

- **El resto del `WHERE` filtra antes de buscar:** la consulta da las k filas más parecidas
  que lo cumplen, no k menos las que un filtro descarta después.
- **Métrica distinta a la del índice:** el índice se construyó para ordenar con la suya, así
  que se compara contra todas las filas y el plan lo dice. Es lo que hace pgvector.
- **El archivo de la consulta** se resuelve igual que el de `CREATE TABLE ... FROM FILE`:
  dentro del directorio de datos o de `MINIGESTOR_IMPORT_DIRS`.

Lo que el motor exige, con un error antes de leer ninguna fila:

- `SIMILAR_TO('archivo', k = n)` o `SIMILAR_TO(columna, 'archivo', k = n)`: un archivo que
  exista, sea una imagen o un audio (por su extensión) y sea del tipo de la columna, y un `k`
  entero positivo;
- va en el `WHERE` de un `SELECT`, unida al resto con `AND`, una sola por consulta y no junto
  a un `MATCH`;
- un índice de similitud que la responda: si no hay, el error dice el `CREATE INDEX` que
  falta; si hay varios, pide `USING` o la columna;
- `USING` es `HNSW` o `IVF`; `WITH` lleva `metric` y el esfuerzo del método (`ef_search` o
  `probes`), enteros positivos.

### Ejemplos del enunciado

```sql
SELECT * FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000;

SELECT * FROM restaurantes ORDER BY distancia(ubicacion, mi_ubicacion) LIMIT 10;

SELECT * FROM documentos WHERE MATCH(contenido, 'base de datos vectorial')
    USING TF_IDF LIMIT 10;

SELECT *, SCORE() as relevancia FROM articulos
    WHERE MATCH(texto, 'machine learning')
    USING BM25
    ORDER BY relevancia DESC;

SELECT * FROM imagenes
    WHERE SIMILAR_TO('foto_consulta.jpg', k=10)
    USING HNSW WITH METRIC=cosine;

SELECT nombre, artista, SIMILARITY_SCORE() as score
    FROM canciones
    WHERE SIMILAR_TO('audio_query.mp3', k=5)
    USING IVF WITH METRIC=euclidean
    ORDER BY score DESC;
```

### Reglas que el parser ya aplica

Comprobaciones locales a una sentencia, que no necesitan catálogo y por eso se resuelven aquí con
posición exacta del error:

- nombres de columna repetidos en `CREATE TABLE`, en la lista de `INSERT`, en un índice y en la
  clave primaria compuesta (comparados sin distinguir mayúsculas);
- una sola cláusula `PRIMARY KEY (...)` a nivel de tabla, y sus columnas deben existir;
- una columna no puede asignarse dos veces en el mismo `UPDATE`;
- todas las filas de un `INSERT` tienen la misma cantidad de valores, y coincide con la lista de
  columnas cuando esta se declara;
- tamaño de tipo obligatorio en `VECTOR`, prohibido donde no aplica y siempre mayor que cero;
- opciones repetidas en `WITH` y argumentos nombrados repetidos en una llamada;
- identificador citado vacío (`""`), literal entero fuera del rango convertible y literal
  decimal que desborda a infinito (`1e400`).

La existencia de tablas y columnas, la compatibilidad de tipos y la aridad de las funciones
corresponden al analizador semántico, que trabaja contra el catálogo.

### Errores

`parse` lanza `SqlLexicalError` o `SqlSyntaxError`, ambas subclases de `SqlError`, con `message`,
`line`, `column` y `source_line`. El mensaje formateado incluye la línea ofensora y un cursor:

```
se esperaba un número de filas después de LIMIT, se encontró 'x' (línea 1, columna 23)
SELECT * FROM t LIMIT x
                      ^
```
