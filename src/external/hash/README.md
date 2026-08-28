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
2. Se carga **una** partición en memoria y se construye un diccionario `clave → filas`.
3. Se emiten sus grupos y se pasa a la siguiente partición.

Como todas las filas de una misma clave están en la misma partición, ningún grupo queda
partido entre dos particiones. El resultado **no sale ordenado**: quien necesite orden pide
además un ordenamiento externo.

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

## Por qué el hash no puede ser `hash()` de Python

Dos motivos: está aleatorizado por proceso, y `1`, `1.0` y `True` **son iguales para
Python**. Si `1` cayera en la partición 2 y `1.0` en la 5, un join entre una columna entera
y una real no encontraría nunca sus coincidencias. Por eso `canonical_key_bytes` normaliza
la clave antes de hashearla con Blake2b: valores iguales, bytes iguales, misma partición.

## Complejidad

| | Coste |
|---|---|
| E/S del GROUP BY | `2N` |
| E/S del JOIN | `2(N + M)` |
| Memoria | la partición más grande: `≈ N/P` |
| Comparaciones del JOIN | `O(N + M)` frente a `O(N · M)` del bucle anidado |
| Resultado ordenado | no |

## Límite conocido

Una partición tiene que caber en memoria. Si una sola clave concentra millones de filas, su
partición será enorme por mucho que se suba `P` — el hash no puede separar valores iguales.
Los gestores reales reparticionan recursivamente con otra semilla; aquí el límite práctico
es `P × memoria disponible`, y `hash_partitions` es configurable justamente para subirlo.

## Uso

```python
from external.hash import ExternalHashGrouper, ExternalHashJoin

with ExternalHashGrouper(directorio, serializer.size, key_of, config) as grouper:
    for clave, filas in grouper.group(registros):
        ...

with ExternalHashJoin(directorio, tam_izq, tam_der, key_izq, key_der, config) as joiner:
    for izquierda, derecha in joiner.join(filas_izq, filas_der):
        ...
```

## Tests

```bash
.venv/bin/python -m pytest tests/external/hash -q
```

Cubren: entrada vacía, cada fila en exactamente un grupo, un único grupo con todas las
filas, uso real de varias particiones, join con coincidencias, join sin ninguna, join que
multiplica claves repetidas (3 × 4 = 12 pares) y join con un lado vacío.
