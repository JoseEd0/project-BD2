# Hashing externo

## La idea

Si dos filas tienen la misma clave, su hash es el mismo, así que **caen en la misma
partición**. Eso permite dividir un problema grande en `P` problemas independientes, cada
uno lo bastante pequeño para caber en memoria.

```
              hash(clave) mod 4
  entrada  ─────────────────────►  ┌──────────┐ partición 0
                                   ├──────────┤ partición 1
                                   ├──────────┤ partición 2
                                   └──────────┘ partición 3
                                        │
                       se procesa una partición a la vez, en memoria
```

Es una sola pasada de escritura y una de lectura: `2N` operaciones de E/S.

## GROUP BY

1. Se reparte cada fila en su partición según el hash de la clave de agrupación.
2. Se recorre **una** partición construyendo un diccionario `clave → estado`.
3. Se emiten sus grupos y se pasa a la siguiente partición.

Como todas las filas de una misma clave están en la misma partición, ningún grupo queda
partido entre dos particiones. El resultado **no sale ordenado**: quien necesite orden pide
además un ordenamiento externo.

### De cada grupo se guarda un estado, no sus filas

`reduce(registros, start, step)` no junta las filas de un grupo en una lista: les aplica
`step` de una en una y se queda con lo que va acumulando. Para `COUNT(*)` el estado es un
contador; para `SUM`, un total; para `DISTINCT`, la primera fila que llegó.

```
  partición 2:   (lima, 14.5) (cusco, 9.0) (lima, 11.0) (lima, 17.5) …

  estados:       lima  → cuenta 3, suma 43.0
                 cusco → cuenta 1, suma  9.0
```

La consecuencia: **una clave con un millón de filas ocupa lo mismo que una con dos**. Lo
que tiene que caber en memoria es el número de grupos de una partición, no el de filas.

### Y si los grupos tampoco caben: se vuelve a repartir

Si una partición trae más grupos de los que caben en el buffer (`sort_buffer_pages`
páginas), se **reparte otra vez con una función de hash distinta** —el mismo Blake2b con
otra sal—, que separa las claves que con la primera coincidían. Cada nivel divide los
grupos entre `P`, así que con `G` grupos y sitio para `B` hacen falta `⌈log_P(G/B)⌉`
niveles. Con los valores por omisión (16 particiones, 16 páginas de buffer) y filas de 80
bytes caben 800 grupos en memoria: un nivel basta hasta 12 800 grupos y dos hasta 204 800.

## JOIN (grace hash join)

```
  izquierda ──► particiones L0 L1 L2 L3
  derecha   ──► particiones R0 R1 R2 R3       (mismo hash, mismo número)

  para i en 0..3:
      cargar Li en un diccionario  clave → filas
      recorrer Ri y emitir las coincidencias
```

Comparar cada fila izquierda con cada derecha costaría `O(N · M)`. Aquí solo se comparan
filas dentro de la misma partición, y dentro de ella la búsqueda es por diccionario: el
coste baja a `O(N + M)`.

La clave puede ser **compuesta** (`ON a.x = b.x AND a.y = b.y`), y una clave con NULL no
empareja con ninguna, ni con otro NULL. Dos parámetros completan la reunión:

- `accepts`, una condición adicional sobre cada par de claves iguales: el resto del `ON`
  (`… AND a.precio < b.tope`). Un par que no la cumple cuenta como no emparejado.
- `unmatched`, qué filas sin pareja se entregan de todos modos, con `None` en el otro lado:
  es lo que distingue un `LEFT`, un `RIGHT` o un `FULL JOIN` de uno interno. Las derechas
  se saben al sondear; las izquierdas, al terminar su partición.

### Cuando la partición izquierda no cabe

La partición izquierda es la que se carga en memoria. Si tiene más filas que el buffer:

```
  ¿cabe Li?  ── sí ──►  diccionario en memoria                       (el caso normal)
      │
      no ──►  repartir Li y Ri con otra función de hash y repetir
                  │
                  └─ ¿la nueva función tampoco separó nada?
                         todas las filas comparten clave: ningún hash las reparte
                         ──►  bucles anidados en bloques sobre Li y Ri
```

Una clave muy repetida es el punto débil clásico del hash join: sus filas tienen el mismo
hash con cualquier función. En vez de cargarla entera se reúne por
[bucles anidados en bloques](../loop/README.md), que solo necesita un bloque en memoria, y
las demás claves siguen su camino normal.

## Por qué el hash no puede ser `hash()` de Python

Dos motivos: está aleatorizado por proceso, y `1`, `1.0` y `True` **son iguales para
Python**. Si `1` cayera en la partición 2 y `1.0` en la 5, un join entre una columna entera
y una real no encontraría nunca sus coincidencias. Por eso `canonical_key_bytes` normaliza
la clave antes de hashearla con Blake2b: valores iguales, bytes iguales, misma partición.

## Complejidad

Con `N` y `M` registros, `G` grupos, `P` particiones y un buffer de `B` registros:

| | Coste |
|---|---|
| E/S del GROUP BY | `2N` si `G ≤ P · B`; en general `2N` por nivel, `⌈log_P(G/B)⌉` niveles |
| E/S del JOIN | `2(N + M)` si las particiones caben; otro tanto por cada nivel extra |
| Memoria | como mucho `B` estados (GROUP BY) o `B` filas de la izquierda (JOIN) |
| Comparaciones del JOIN | `O(N + M)`, salvo entre las filas de una clave muy repetida |
| Resultado ordenado | no |

## Uso

```python
from external.hash import ExternalHashGrouper, ExternalHashJoin

def contar(vistas: int, registro: bytes) -> int:
    return vistas + 1

with ExternalHashGrouper(directorio, serializer.size, key_of, config) as grouper:
    for clave, cuantas in grouper.reduce(registros, int, contar):
        ...

with ExternalHashJoin(directorio, tam_izq, tam_der, key_izq, key_der, config) as joiner:
    for izquierda, derecha in joiner.join(filas_izq, filas_der):
        ...
```

## Tests

```bash
.venv/bin/python -m pytest tests/external/hash -q
```

Cubren, del agrupamiento: entrada vacía, cada fila en exactamente un grupo, un único
grupo con todas las filas, uso real de varias particiones, reducción en orden de llegada,
**más grupos de los que caben** (baja de nivel y cada clave sale una vez) y un grupo enorme
que no necesita pasadas extra.

De la reunión: con coincidencias, sin ninguna, claves repetidas (3 × 4 = 12 pares), un lado
vacío, las cuatro reuniones externas, claves NULL, condición adicional, **lado izquierdo
mayor que la memoria** y **clave con más filas que la memoria** (por bloques), estas dos
comparadas con el cálculo de todos contra todos en las cuatro reuniones.

En los dos casos, que abandonar el recorrido a medias no deja archivos temporales.
