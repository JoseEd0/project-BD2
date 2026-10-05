# Reunión por bucles anidados en bloques

## Cuándo hace falta

El *grace hash join* reparte las filas por el hash de la clave de reunión, así que necesita
que la condición tenga una **igualdad**. Cuando no la tiene —`ON a.precio < b.tope`,
`ON a.x <> b.x`— no hay clave por la que repartir y hay que comparar cada fila de un lado
con cada fila del otro. Lo que sí se puede acotar es la memoria y el número de lecturas.

## El algoritmo

```
  derecha   ──► archivo temporal            (se escribe una sola vez)
  izquierda ──► bloques de B filas en memoria
                    │
                    └─► por cada bloque, UNA pasada por el archivo de la derecha,
                        comparando cada fila leída con todas las del bloque
```

El bucle ingenuo relee la tabla derecha una vez **por fila** de la izquierda; por bloques se
relee una vez **por bloque**. Con un bloque de 16 páginas eso divide las lecturas por
varios cientos, y la memoria sigue siendo el bloque más una página de la derecha.

## Reuniones externas

`unmatched` dice qué filas sin pareja se entregan de todos modos:

| Reunión | Qué conserva | Cómo se sabe quién no emparejó |
|---|---|---|
| `LEFT` | filas izquierdas sin pareja | una marca por fila del bloque, al terminar su pasada |
| `RIGHT` | filas derechas sin pareja | un byte por fila derecha, y una pasada final por el archivo |
| `FULL` | las dos | ambas |

La fila sin pareja sale con `None` en el lugar de la otra, y el operador la completa con
NULL.

## Complejidad

Con `N` filas a la izquierda, `M` a la derecha y bloques de `B` filas:

| | Coste |
|---|---|
| Comparaciones | `N · M`: inevitables sin una igualdad que las reparta |
| E/S | `M` escrituras + `⌈N/B⌉ · M` lecturas, frente a las `N · M` del bucle ingenuo |
| Memoria | un bloque de la izquierda (`sort_buffer_pages` páginas) y una página de la derecha |

## Quién lo usa

- El operador `NestedLoopJoin`, cuando el `ON` no contiene ninguna igualdad entre columnas
  de los dos lados.
- El propio *grace hash join*, para la partición de una **clave muy repetida**: si una sola
  clave tiene más filas de las que caben en memoria, ningún hash las separa, y esa partición
  se reúne por bloques (ver [`hash/`](../hash/README.md)).

## Uso

```python
from external.joining import Unmatched
from external.loop import BlockNestedLoopJoin

with BlockNestedLoopJoin(directorio, tam_izq, tam_der, cumple, config, Unmatched.LEFT) as joiner:
    for izquierda, derecha in joiner.join(filas_izq, filas_der):
        ...
```

## Tests

```bash
.venv/bin/python -m pytest tests/external/loop -q
```

Cubren: entradas vacías a cada lado, un par que cumple la condición y uno que no, todos
los pares comparados a través de varios bloques (varias pasadas por la derecha), las cuatro
reuniones (`INNER`, `LEFT`, `RIGHT`, `FULL`) —también con un lado vacío y sin ningún par
que cumpla— y limpieza del archivo temporal.
