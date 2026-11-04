# Índices

Un índice responde "¿dónde están las filas que cumplen esto?" sin recorrer la tabla entera.
Cada estructura vive en su carpeta con su propia explicación:

| Carpeta | Estructura | Sirve para |
|---|---|---|
| [`bplustree/`](bplustree/README.md) | Árbol B+ (agrupado y no agrupado) | igualdad, rango y orden |
| [`hash/`](hash/README.md) | Hash extendible | igualdad, nada más |
| [`rtree/`](rtree/README.md) | R-Tree | puntos: radio, vecinos más cercanos y polígono |
| [`inverted/`](inverted/README.md) | Índice invertido (SPIMI) | texto: las listas de documentos de cada término |

## Claves (`keys.py`)

Todas las estructuras comparten la forma de tratar una clave: se **guarda** como bytes de
tamaño fijo y se **compara** como valor de Python.

- `ScalarKeyCodec` — una columna.
- `CompositeKeyCodec` — varias columnas; la clave es una tupla y Python ya las compara en
  orden lexicográfico, que es exactamente el orden que necesita un índice.
- `RecordIdKeyCodec` — la dirección física, usada como componente de desempate.

Una clave compuesta con componentes de más o de menos se rechaza con `KeyShapeError`.

Cada códec se describe como un formato de `struct` y la conversión entre la clave y sus
campos. Una clave compuesta es entonces un solo `struct` —los mismos bytes que sus
componentes uno detrás de otro—, y un nodo del B+ lee o escribe todas sus claves de una
pasada (`unpack_many`, con `iter_unpack`) en vez de una a una. Leer un nodo sigue costando
lo que tiene, porque el árbol trabaja con nodos decodificados, pero ese costo bajó a la
mitad: insertar 20 000 filas pasó de 8,2 s a 4,2 s en el B+ no agrupado y de 1,5 s a
0,7 s en el agrupado.

## Índices secundarios (`secondary.py`)

Los tres índices que el motor pone sobre una columna de un heap file —B+ no agrupado, hash
y R-Tree— cumplen el mismo protocolo, `SecondaryIndex`, y heredan de `ColumnIndex` la parte
que es igual en los tres: sacar de cada fila el valor de su columna y dejar fuera las filas
en las que es NULL. Cada estructura aporta solo cómo guarda, borra y busca un par
`(valor, dirección)`. Los errores de nodo que comparten el B+ y el R-Tree viven en
[`errors.py`](errors.py).

## Cuál elegir

| Consulta | B+ agrupado | B+ no agrupado | Hash extendible |
|---|---|---|---|
| `WHERE id = 5` | `O(log N)`, sin saltos | `O(log N)` + 1 salto al heap | **`O(1)`** |
| `WHERE id BETWEEN 10 AND 90` | `O(log N + k)` | `O(log N + k)` + k saltos | ✗ imposible |
| `ORDER BY id` | gratis | gratis (con saltos) | ✗ hay que ordenar |
| Muchas filas por valor | — | sí | sí |
| Índices por tabla | uno solo | los que hagan falta | los que hagan falta |

Regla práctica: **hash** si solo se busca por igualdad, **B+** en cuanto aparezca un rango o
un `ORDER BY`, y **agrupado** para la clave primaria de la tabla.

Una columna `POINT` es otro mundo: un punto no tiene un orden que un B+ pueda aprovechar ni
un hash que sirva para algo más que coordenadas exactas. Solo admite un **R-Tree**, y un
R-Tree solo indexa columnas `POINT`. La igualdad también la resuelve él:
`WHERE ubicacion = POINT(…)` busca en el árbol el rectángulo de un solo punto.

## Los NULL no se indexan

Un índice secundario deja fuera las filas cuyo campo es NULL. No puede hacer otra cosa:
`columna = NULL` no es cierto para ninguna fila, así que ninguna búsqueda por igualdad o
por rango las devolvería, y un B+ no tiene dónde ordenar un NULL. La fila sigue en el
heap; al borrarla o al cambiarle el valor, el índice simplemente no tiene nada que quitar.
Y buscar NULL en cualquiera de los tres índices no encuentra nada.

La única consulta que nota la ausencia es el k-NN del R-Tree, que sustituye a un recorrido
completo: por eso el motor añade aparte las filas sin ubicación (ver
[`query/`](../query/README.md)).

## Tests

```bash
.venv/bin/python -m pytest tests/index -q            # todos
.venv/bin/python -m pytest tests/index/bplustree -q  # solo el B+
.venv/bin/python -m pytest tests/index/hash -q       # solo el hash
.venv/bin/python -m pytest tests/index/rtree -q      # solo el R-Tree
```

Además de los tests de cada estructura, `tests/index/test_keys.py` cubre los códecs de
clave, y cada índice tiene uno que abre un archivo ajeno o con las páginas pisadas: el
error tiene que ser el de formato, no una excepción cualquiera.
