# Gramática SQL soportada

Dialecto que acepta el paquete `sql`. Cubre el núcleo relacional de la Parte 1 y las extensiones
espacial, textual y multimedia de las Partes 2–4. El parser produce un AST inmutable; no valida
todavía nombres ni tipos contra el catálogo (eso corresponde al analizador semántico).

## Convenciones léxicas

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

## Sentencias

```
statement    ::= select | insert | update | delete
               | create_table | create_index | drop_table | drop_index
               | 'BEGIN' ['TRANSACTION'] | 'END' ['TRANSACTION']
               | 'COMMIT' ['TRANSACTION'] | 'ROLLBACK' ['TRANSACTION']

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

## Tipos de columna

`INT`/`INTEGER`, `BIGINT`, `FLOAT`/`REAL`, `DOUBLE`, `BOOL`/`BOOLEAN`, `CHAR(n)`, `VARCHAR(n)`,
`TEXT`, `DATE`, `POINT`, `VECTOR(n)`/`ARRAY(n)`, `BLOB`.

`CHAR` y `VARCHAR` admiten tamaño opcional, `VECTOR` lo exige, el resto lo rechaza.

## Métodos de acceso

| Cláusula `method` | Estructura |
|---|---|
| `SEQ`, `SEQUENTIAL` | archivo secuencial paginado |
| `BTREE`, `BPTREE`, `BPLUSTREE` | índice B+ |
| `HASH`, `EHASH`, `EXTENDIBLE_HASH` | hash extensible |
| `RTREE` | R-Tree espacial |
| `INVERTED`, `SPIMI` | índice invertido |
| `IVF`, `HNSW` | índices vectoriales |
| `TF_IDF`, `TFIDF`, `BM25` | funciones de ranking de texto (solo en `SELECT ... USING`) |

## Expresiones

Precedencia de menor a mayor:

```
OR  <  AND  <  NOT  <  comparación  <  + -  <  * / %  <  - unario  <  primaria
```

- Comparación: `=` `==` `!=` `<>` `<` `<=` `>` `>=`
- Predicados: `x [NOT] BETWEEN a AND b`, `x [NOT] IN (…)`, `x [NOT] LIKE p`, `x IS [NOT] NULL`
- Primarias: literales, `col`, `tabla.col`, `f(args)`, `(expr)`, `(a, b)` (tupla, p. ej. vértices
  de un polígono), `*` como argumento de agregado
- Llamadas con argumentos nombrados: `SIMILAR_TO('foto.jpg', k = 10)`; los posicionales van primero

El anidamiento de paréntesis, `NOT` y `-` está limitado a `MAX_EXPRESSION_DEPTH` niveles para que
una consulta patológica falle como error de sintaxis y no como desbordamiento de pila.

## Ejemplos del enunciado

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

## Reglas que el parser ya aplica

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
- identificador citado vacío (`""`) y literal entero fuera del rango convertible.

La existencia de tablas y columnas, la compatibilidad de tipos y la aridad de las funciones
corresponden al analizador semántico, que trabaja contra el catálogo.

## Errores

`parse` lanza `SqlLexicalError` o `SqlSyntaxError`, ambas subclases de `SqlError`, con `message`,
`line`, `column` y `source_line`. El mensaje formateado incluye la línea ofensora y un cursor:

```
expected a row count after LIMIT, found 'x' (line 1, column 23)
SELECT * FROM t LIMIT x
                      ^
```
