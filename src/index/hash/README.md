# Hash extendible

## Qué es

Un índice de **igualdad** que responde en un acceso a disco y que crece sin tener que
reconstruirse. Es la alternativa al B+ cuando las consultas son siempre `WHERE columna =
valor` y nunca hay rangos ni `ORDER BY`.

El problema que resuelve: un hash estático con `B` cubetas se degrada en cuanto se llena, y
rehacerlo obliga a releer y reescribir todo. El hash extendible **duplica solo el directorio
—que es pequeño— y parte únicamente la cubeta que se llenó**.

## Las dos profundidades

```
  profundidad global = 2          (el directorio usa los 2 bits bajos del hash)

  directorio                      cubetas
  ┌────┐
  │ 00 │──────────────┐
  ├────┤              ├────────►  ┌──────────────┐  profundidad local = 1
  │ 10 │──────────────┘           │ 4  8  12  16 │  (llegó con 1 bit: '0')
  ├────┤                          └──────────────┘
  │ 01 │──────────────────────►   ┌──────────────┐  profundidad local = 2
  ├────┤                          │ 1  5  9      │  (llegó con 2 bits: '01')
  │ 11 │──────────────────────►   ┌──────────────┐  profundidad local = 2
  └────┘                          │ 3  7  11     │
                                  └──────────────┘
```

- **Profundidad global (`gd`)**: cuántos bits del hash usa el directorio. Tiene `2^gd`
  entradas.
- **Profundidad local (`ld`)**: cuántos bits hicieron falta para llegar a *esa* cubeta.

**Invariante:** una cubeta de profundidad local `ld` es apuntada por exactamente `2^(gd−ld)`
entradas del directorio. Es lo que verifican los tests.

## Cómo funciona

### Buscar

1. `h = hash(clave)`; se toman los `gd` bits bajos → índice del directorio.
2. Se lee el puntero y se va a la cubeta.
3. Se recorre la cubeta.

**Dos accesos a página, independientemente del tamaño del índice.**

### Insertar y partir

Si la cubeta tiene sitio, se escribe y ya está. Si está llena:

```
insertar 20 en una cubeta llena de ld = 1

  ¿ld == gd?  ──► sí: se DUPLICA el directorio (gd++)
                       cada entrada nueva copia el puntero de su gemela;
                       ninguna cubeta se toca
                  no: se salta este paso

  se PARTE la cubeta mirando el bit ld de cada clave:
      bit 0 → se queda en la cubeta original
      bit 1 → se va a una cubeta nueva
  ambas pasan a ld + 1
  el directorio repunta las entradas cuyo bit ld está encendido
```

La clave del diseño: **duplicar el directorio es barato** (`2^gd` enteros de 4 bytes) y
**no mueve ni un registro**. Solo la cubeta que se partió se reescribe.

### El caso degenerado

Si muchísimas filas comparten el mismo valor, todas tienen el mismo hash y **siempre caen
del mismo lado**: partir no reparte nada. Este índice lo detecta **antes** de duplicar el
directorio —comprueba si el reparto movería algo— y, si no, encadena una página de
desbordamiento a la cubeta. Sin esa comprobación, insertar 300 filas con la misma clave
duplicaría el directorio 300 veces.

### Borrar

Se libera la ranura y la reutiliza la siguiente inserción. Esta implementación **no fusiona
cubetas ni reduce el directorio**: es una decisión deliberada, porque encoger obliga a
comprobar la cubeta gemela en cada borrado y la mayoría de gestores tampoco lo hacen.

## Por qué el hash no es el de Python

`hash("lima")` devuelve un valor distinto en cada proceso de Python (protección contra
ataques de colisión). Un índice construido con él sería ilegible al reabrirlo. Aquí se usa
**Blake2b** sobre los bytes ya serializados de la clave, que siempre da lo mismo.

## Complejidad

| Operación | Coste |
|---|---|
| Búsqueda por igualdad | `O(1)` — 1 lectura de directorio + 1 de cubeta |
| Inserción | `O(1)` amortizado |
| Borrado | `O(1)` |
| Partir una cubeta | `O(c)`, c = entradas por cubeta |
| Duplicar el directorio | `O(2^gd)` escrituras, **0 lecturas de cubetas** |
| Búsqueda por rango | **imposible** — el hash destruye el orden |
| Espacio | `O(N/c)` páginas de cubetas + `2^gd × 4` bytes de directorio |

## Hash extendible vs árbol B+

| | Hash extendible | B+ |
|---|---|---|
| `columna = valor` | **O(1)** | O(log N) |
| `columna BETWEEN a AND b` | ✗ | **O(log N + k)** |
| `ORDER BY columna` | ✗ | **gratis** |
| Coste de crecer | duplicar directorio (barato) | dividir nodos |
| Sensible a claves repetidas | sí (cadenas) | no |

## Uso

```python
from index.hash import ExtendibleHashIndex
from index.keys import ScalarKeyCodec

with ExtendibleHashIndex(path, ScalarKeyCodec(campo), value_size=6, config=config) as idx:
    idx.insert(1, direccion.pack())
    valores = idx.search(1)
    idx.delete(1)
```

## Tests

```bash
.venv/bin/python -m pytest tests/index/hash -q
```

El validador de `conftest.py` comprueba los invariantes de verdad: que la profundidad local
nunca supere a la global, que **toda clave esté en la cubeta que le toca según sus `ld` bits
bajos**, y que cada cubeta reciba exactamente `2^(gd−ld)` punteros del directorio. Se
ejecuta tras provocar divisiones, duplicaciones del directorio, el caso degenerado de claves
repetidas y ciclos de borrado y reinserción.
