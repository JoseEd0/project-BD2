# Árbol B+

## Qué es

Un árbol de búsqueda **equilibrado** donde cada nodo ocupa una página de disco. Al ser
ancho —cientos de claves por nodo— es bajísimo: con páginas de 4 KB y claves de 8 bytes,
tres niveles bastan para decenas de millones de filas. Buscar cuesta tantos accesos a disco
como niveles tenga el árbol.

Dos reglas lo definen:

1. **Todos los datos están en las hojas.** Los nodos internos solo guardan *separadores*
   para saber por dónde bajar.
2. **Las hojas están encadenadas.** Por eso una consulta por rango baja una sola vez y luego
   avanza en línea recta, sin volver a subir.

```
                       ┌────────┐
                       │   30   │            ← nodo interno (separadores)
                       └───┬────┘
              ┌────────────┴────────────┐
        ┌─────┴─────┐              ┌────┴─────┐
        │  10   20  │              │  40   50 │
        └──┬──┬──┬──┘              └──┬──┬──┬─┘
           │  │  │                    │  │  │
        ┌──▼─┐│┌─▼──┐              ┌──▼─┐│┌─▼──┐
        │ 5 8│││20 25│  ─────────► │30 35│││50 60│  ← hojas encadenadas
        └────┘│└─────┘             └─────┘│└─────┘
```

**Invariante de los separadores:** toda clave del hijo `c_i` cumple `k_(i−1) ≤ clave < k_i`.
Es lo que hace que bajar por el árbol sea correcto y lo que verifican los tests.

## Cómo funciona

### Buscar

Desde la raíz, en cada nodo interno se busca el primer separador mayor que la clave y se
baja por ese hijo. Al llegar a la hoja, una búsqueda binaria dentro de la página. Coste:
**una lectura de página por nivel**.

### Insertar y dividir

Se baja hasta la hoja y se inserta en su sitio. Si la hoja se pasa de capacidad:

```
hoja llena          [ 5  8 12 20 ]  + insertar 15
                          │
                          ▼  se parte por la mitad
izquierda [ 5  8 ]   derecha [ 12 15 20 ]
                          │
                          ▼  la primera clave de la derecha sube al padre
                     padre: ... 12 ...
```

Si el padre también se desborda, se parte igual y sube su clave central — **esta vez la
clave sube, no se copia**, porque en un nodo interno el separador no es un dato. Si el que
se desborda es la raíz, se crea una raíz nueva y **el árbol gana un nivel**. Por eso todas
las hojas quedan siempre a la misma profundidad: el árbol crece por arriba, nunca por abajo.

### Borrar, redistribuir y fusionar

Si tras borrar un nodo baja del mínimo (la mitad de su capacidad), hay dos salidas:

- **Redistribuir**, si un hermano vecino tiene de sobra: se le pide una entrada y se ajusta
  el separador del padre.
- **Fusionar**, si ningún hermano tiene de sobra: los dos nodos se juntan en uno y el
  separador desaparece del padre. Eso puede dejar al padre bajo mínimos, y la reparación
  sube. Si la raíz se queda sin claves, su único hijo pasa a ser la raíz y **el árbol pierde
  un nivel**.

Las páginas que quedan libres al fusionar entran en una lista de libres y se reutilizan.

## Claves repetidas

El árbol exige **claves únicas**. Para indexar una columna con valores repetidos, la clave
del árbol es el par `(valor, dirección del registro)`: sigue siendo única y las entradas de
un mismo valor quedan contiguas, así que buscarlas es un recorrido por rango. Es la misma
solución que usan los B+ de PostgreSQL.

## Las dos variantes

### Agrupado (`clustered.py`) — el árbol **es** la tabla

Las hojas guardan la fila completa. Leer por clave no cuesta ningún acceso extra y un rango
devuelve las filas ya ordenadas.

- Solo puede haber **uno por tabla** (los datos solo pueden estar ordenados de una forma).
- Las hojas son grandes → menos entradas por página → el árbol es más alto.

### No agrupado (`unclustered.py`) — el árbol apunta a la tabla

Las hojas guardan direcciones `(página, ranura)` que apuntan a un heap file.

- Puede haber **varios por tabla**.
- El árbol es mucho más compacto, porque una dirección ocupa 6 bytes.
- Cada resultado exige un **salto al heap**: un acceso extra por fila. Si una consulta
  devuelve media tabla, ese salto sale más caro que recorrerla entera.

```
        índice no agrupado                       heap file
        hoja: ("lima" → (3,2))  ──────────────►  página 3, ranura 2: (7,"ana","lima")
```

## Complejidad

Con `N` entradas y `m` entradas por nodo:

| Operación | Coste |
|---|---|
| Búsqueda por igualdad | `O(log_m N)` accesos a página |
| Inserción | `O(log_m N)` + división amortizada |
| Borrado | `O(log_m N)` + fusión amortizada |
| Rango de `k` resultados | `O(log_m N + k/m)` |
| Recorrido ordenado completo | `O(N/m)` siguiendo la cadena de hojas |
| Espacio | `O(N/m)` páginas, con ocupación ≥ 50 % |

## Uso

```python
from index.bplustree import ClusteredBPlusIndex, UnclusteredBPlusIndex

with ClusteredBPlusIndex(path, serializer, key_field="id", config=config) as pk:
    pk.insert(serializer.pack((1, "ana", "lima")))
    fila = pk.search(1)
    for fila in pk.range_search(10, 50):
        ...

with UnclusteredBPlusIndex(path, serializer, key_field="ciudad", config=config) as idx:
    idx.insert(record, record_id)
    direcciones = idx.search("lima")        # [(página, ranura), ...]
```

## Tests

```bash
.venv/bin/python -m pytest tests/index/bplustree -q
```

No comprueban solo que `search` devuelva lo correcto —un árbol puede responder bien y estar
roto por dentro—. El validador de `conftest.py` recorre el árbol entero y verifica: orden
dentro de cada nodo, respeto de los separadores, ocupación mínima de todo nodo que no sea la
raíz, **todas las hojas a la misma profundidad** y que la cadena de hojas contenga
exactamente las claves esperadas. Se ejecuta tras inserciones ascendentes, descendentes y
aleatorias, y cada pocos borrados de una secuencia que vacía el árbol por completo.
