# R-Tree

## Qué es

Un índice para datos **espaciales**: puntos con latitud y longitud. Responde tres preguntas
que un B+ no sabe contestar, porque un punto no tiene un orden total:

- ¿qué hay **a menos de un radio** de aquí?
- ¿cuáles son los **k más cercanos**?
- ¿qué cae **dentro de este polígono**?

El problema que resuelve: sin índice, las tres obligan a medir la distancia a todos los
puntos. El R-Tree agrupa los puntos cercanos bajo un mismo rectángulo, y una búsqueda
**descarta de un golpe** todos los grupos cuyo rectángulo no puede contener resultados.

Es la implementación de Guttman (1984), con división cuadrática de nodos.

## La idea: rectángulos que encierran rectángulos

```
  nivel 1 (raíz)          ┌───────────────── R1 ─────────────────┐   ┌──── R2 ────┐
                          │                                      │   │            │
  nivel 0 (hojas)         │  ┌── A ──┐      ┌──── B ────┐        │   │ ┌── C ──┐  │
                          │  │ ·  ·  │      │ ·    ·    │        │   │ │ ·  ·  │  │
                          │  │   ·   │      │    ·   ·  │        │   │ │  ·    │  │
                          │  └───────┘      └───────────┘        │   │ └───────┘  │
                          └──────────────────────────────────────┘   └────────────┘
```

- Cada **hoja** guarda puntos, cada uno con la dirección de su fila en el heap file.
- Cada **nodo interno** guarda, por cada hijo, su **MBR** (*minimum bounding rectangle*):
  el menor rectángulo que encierra todo lo que cuelga de él.
- Todas las hojas están a la misma profundidad, igual que en un B+.

**Invariante:** el MBR de una entrada interna es exactamente el rectángulo que encierra las
entradas de su hijo, ni más grande ni más pequeño. Es lo que comprueban los tests.

**La diferencia con el B+:** los MBR de dos hermanos **pueden solaparse**. Un punto puede
caer dentro de dos rectángulos a la vez, así que una búsqueda puede tener que bajar por
más de una rama. Por eso toda la calidad del árbol depende de que los rectángulos queden
pequeños y poco solapados.

## El formato en disco

Un nodo ocupa una página. Con páginas de 4 KB:

| Nodo | Entrada | Bytes | Entradas por nodo |
|---|---|---:|---:|
| hoja | latitud, longitud (2 dobles) + dirección (página, ranura) | 22 | 185 |
| interno | MBR (4 dobles) + página del hijo | 36 | 113 |

Las hojas guardan el punto, no un rectángulo: ahorra 14 bytes por entrada y caben un 60 %
más. La página 0 es la cabecera: marca `RTR1`, raíz, altura, número de puntos y de nodos, y
la lista de páginas libres.

Un nodo distinto de la raíz nunca baja del **40 %** de ocupación
(`EngineConfig.rtree_min_fill`): 74 entradas en una hoja y 45 en un nodo interno.

## Cómo funciona

### Insertar

```
insertar el punto P

  1. BAJAR desde la raíz: en cada nodo interno se elige el hijo cuyo MBR
     menos crece al añadirle P (a igual crecimiento, el de menor área).
  2. AÑADIR P a la hoja.
  3. Si la hoja se DESBORDA, se divide en dos (ver abajo) y el padre recibe
     una entrada nueva; si el padre se desborda, también se divide, y así
     hacia arriba. Si se divide la raíz, el árbol gana un nivel.
  4. AJUSTAR los MBR del camino para que sigan cubriendo a sus hijos.
```

En el código es `RTree._place`, con `choose_subtree` para el paso 1. Si al subir un MBR no
cambia y no hubo división, el ajuste se detiene ahí: nada más arriba puede haber cambiado.

### Dividir un nodo: la división cuadrática

Hay que repartir `M + 1` entradas en dos grupos, y repartirlas mal deja rectángulos enormes
que todas las búsquedas tendrán que abrir. `quadratic_split` lo hace en dos pasos:

1. **Elegir las semillas.** De todos los pares de entradas, el que **más área
   desperdiciaría** si fueran juntas: `área(MBR del par) − área(a) − área(b)`. Son las dos
   que menos sentido tiene dejar en el mismo grupo, así que cada una funda el suyo.
2. **Repartir el resto.** De una en una, empezando siempre por la entrada que tiene **más
   clara su preferencia**: la que más diferencia hay entre lo que haría crecer a un grupo
   y al otro. Va al grupo que menos crece.

Si a un grupo le faltan justo las entradas que quedan para llegar al mínimo, se las lleva
todas. Elegir las semillas mira todos los pares, de ahí el nombre: `O(M²)` por división.

### Buscar por radio

```
buscar los puntos a distancia ≤ r de Q

  para cada nodo pendiente:
      interno → se abre solo cada hijo cuyo MBR tenga algún punto a ≤ r de Q
      hoja    → se mide la distancia exacta de cada punto
```

La poda usa `Metric.min_distance(Q, MBR)`: la **menor distancia posible** de `Q` a cualquier
punto del rectángulo. Si esa cota ya supera `r`, nada de lo que hay dentro puede entrar en
el radio y el subárbol entero se descarta sin leerlo.

### Los k vecinos más cercanos

Búsqueda *primero-el-mejor* de Hjaltason y Samet (1999), en `RTree.nearest`. Una **cola de
prioridad** mezcla nodos y puntos:

- un **nodo** entra con `min_distance(Q, su MBR)`, que es una cota inferior de la distancia
  a todo lo que contiene;
- un **punto** entra con su distancia real.

Se saca siempre lo más cercano. Si es un nodo, se abre y sus entradas entran en la cola. Si
es un punto, **es el siguiente vecino**: nada de lo que queda en la cola puede estar más
cerca, porque todo lo demás tiene una cota mayor o igual.

Los puntos **a la misma distancia** salen en orden de dirección de fila, que es el orden
en que los dejaría un ordenamiento estable de la tabla. Para que eso se cumpla, a igual
distancia un nodo sale de la cola antes que un punto: cuando sale el primer punto de un
empate, todos los demás ya están dentro.

Es **incremental**: entrega los vecinos de uno en uno, del más cercano al más lejano. Quien
quiere `k` deja de pedir tras el k-ésimo y el resto del árbol no se llega a abrir. Por eso
un `LIMIT 10` sobre 100 000 puntos abre de media 4.2 nodos de 610, y por eso un filtro encima
(`WHERE rubro = 'gasolinera'`) funciona sin más: se siguen pidiendo vecinos hasta juntar
los que pasan el filtro.

### Intersección con un polígono

*Filtrar y refinar*, en `SpatialIndex.within_polygon`:

1. **Filtrar:** el árbol busca por el rectángulo que encierra al polígono. Es barato y
   descarta casi todo.
2. **Refinar:** cada punto candidato pasa la comprobación exacta de punto en polígono
   (regla par-impar con un rayo, `O(vértices)`). El borde cuenta como dentro.

### Borrar

1. Se busca la hoja que contiene la entrada. Puede haber que probar varias ramas, porque
   los MBR se solapan.
2. Se quita y se sube **condensando**: un nodo que queda por debajo del mínimo **se
   disuelve** y sus entradas se guardan aparte.
3. Las entradas huérfanas se **reinsertan**, cada una en el nivel del que salió, para que
   todas las hojas sigan a la misma profundidad.
4. Si la raíz se queda con un solo hijo, ese hijo pasa a ser la raíz y el árbol pierde un
   nivel.

Reinsertar en vez de fusionar con un hermano (lo que hace el B+) es deliberado: los
hermanos de un R-Tree no son vecinos ordenados, y reinsertar deja que cada entrada vaya al
nodo donde mejor encaja ahora.

### Carga masiva: Sort-Tile-Recursive

`CREATE INDEX … USING RTREE` sobre una tabla ya cargada no inserta punto a punto: usa STR
(Leutenegger, López y Edgington, 1997), en `RTree.bulk_load`.

```
con N puntos y hojas de n entradas hay P = ⌈N/n⌉ hojas

  1. ordenar los puntos por LATITUD y cortarlos en ⌈√P⌉ franjas
  2. ordenar cada franja por LONGITUD y cortarla en hojas de n puntos
  3. repetir con los MBR de las hojas para obtener el nivel de arriba,
     y así hasta que quede un solo nodo: la raíz
```

Cada hoja cubre una baldosa casi cuadrada y **las baldosas no se solapan**. El paso 1 usa
el ordenamiento externo de la Parte 1, así que en memoria solo hay una franja a la vez.
Las hojas se llenan al 90 % (`EngineConfig.rtree_bulk_fill`) para que las inserciones
posteriores no las dividan de inmediato.

Medido con 100 000 puntos: **0.6 s** con carga masiva frente a **19 s** insertando, y un
árbol de 610 nodos en vez de 807.

## Las dos métricas

El árbol no sabe de métricas. Las búsquedas reciben una y solo le piden dos funciones
(`src/spatial/metrics.py`):

| | Euclidiana | Haversine |
|---|---|---|
| `distance(a, b)` | `√(Δlat² + Δlon²)`, en grados | arco de círculo máximo, en metros |
| `min_distance(p, MBR)` | distancia al lado o a la esquina más cercanos | ver abajo |

La cota de Haversine a un rectángulo de latitudes y longitudes no es la distancia a una
esquina. A una latitud dada, el punto más cercano del rectángulo es el de longitud más
próxima; si la longitud de `p` cae fuera del rango, eso es el borde este u oeste, y a lo
largo de ese meridiano la distancia tiene un único mínimo, en
`atan2(sin φ, cos φ · cos Δλ)`. Basta comparar ese punto con los dos extremos del borde.

Es la distancia al punto más cercano, no una aproximación, y los tests la comparan con el
mínimo real sobre una malla densa. Pero una cota tiene que ser inferior **también después
de redondear**: la distancia a un punto y la cota de su rectángulo se calculan por caminos
distintos, y bastaba que la cota saliera una cifra por encima para que el árbol podara un
nodo con un resultado justo en el borde del radio. Por eso se le resta una fracción de
`10⁻⁹` (`BOUND_SLACK`, seis milímetros en 6 000 km): cubre con holgura el error de
redondeo de la fórmula y no le quita poda al árbol.

## Complejidad

Con `N` puntos y `M` entradas por nodo:

| Operación | Coste |
|---|---|
| Inserción | `O(log_M N)` accesos + `O(M²)` de cómputo si hay división |
| Borrado | `O(log_M N)` en el caso típico, más las reinserciones |
| Búsqueda por radio o polígono | `O(log_M N + k)` típico; `O(N)` si los MBR se solapan mucho |
| k vecinos más cercanos | `O(log_M N + k)` típico |
| Carga masiva | `O(N log N)`, el coste de ordenar |
| Espacio | `O(N/M)` páginas |

A diferencia del B+, el caso peor no está acotado: un R-Tree con los MBR muy solapados
puede acabar abriendo todos los nodos. Lo que lo evita es la calidad de la división y, en
la carga masiva, que las baldosas no se solapen.

### Puntos alineados: el desempate por semiperímetro

Con puntos **alineados** sobre un mismo paralelo o meridiano todos los MBR tienen área
cero, y las dos heurísticas de Guttman —menor crecimiento de área al insertar, mayor área
desperdiciada al dividir— empatarían siempre: el árbol repartiría al azar. Por eso cada
comparación desempata por el **semiperímetro** (alto + ancho), que en ese caso es la
longitud del segmento:

| Decisión | Criterio de Guttman | Desempate |
|---|---|---|
| Elegir subárbol | el MBR que menos crece en área | el que menos crece en semiperímetro; después, el de menor área |
| Elegir semillas | el par que más área desperdicia | el que más semiperímetro desperdicia |
| Asignar la siguiente | la de preferencia más marcada en área | la más marcada en semiperímetro |

Con puntos en posición general el área nunca empata y el resultado es exactamente el de
Guttman; el semiperímetro solo se calcula cuando hay empate, así que el caso normal no lo
paga. Con puntos alineados el árbol se organiza como un índice de una dimensión: las hojas
son segmentos consecutivos. Un test lo mide con 1 600 puntos sobre un paralelo, sobre un
meridiano y en una malla entera: buscar un punto o sus 3 vecinos abre menos de la décima
parte de los nodos.

## Uso

```python
from index.rtree import RTree
from spatial import HAVERSINE, Point

with RTree(path, config) as tree:
    tree.bulk_load(puntos_con_direccion)           # o tree.insert(punto, direccion)
    cerca = tree.search_radius(Point(-12.0464, -77.0428), 5_000, HAVERSINE)
    vecinos = itertools.islice(tree.nearest(Point(-12.0464, -77.0428), HAVERSINE), 10)
    tree.delete(punto, direccion)
```

`SpatialIndex` es el adaptador que usa el motor: saca el punto de cada fila, deja fuera las
filas con `NULL`, resuelve la búsqueda por polígono y la de un punto exacto
(`WHERE ubicacion = POINT(…)`, que es buscar un rectángulo de un solo punto).

## Tests

```bash
.venv/bin/python -m pytest tests/index/rtree -q
```

El validador de `rtree_support.py` recorre el árbol entero y comprueba que ningún nodo se
desborda ni baja del mínimo, que **cada MBR es exactamente el ajustado**, que todas las
hojas están a la misma profundidad y que el número de nodos y de páginas libres cuadra con
el archivo. Se ejecuta tras insertar en orden y al azar, con puntos alineados y con puntos
repetidos, tras vaciar el árbol entero y tras intercalar miles de inserciones y borrados.

Además, **cada búsqueda se compara con la fuerza bruta**: por rectángulo, por radio y por
cercanía, con las dos métricas. La carga masiva se prueba con todos los tamaños de 1 a 59
puntos y varios mayores, que es donde aparecen los restos incómodos.

`test_spatial_index.py` prueba el adaptador con registros en lugar de puntos: de qué
columna saca la ubicación, que rechaza una columna que no es `POINT`, que cargar de golpe e
insertar uno a uno responden lo mismo, y que borrar una fila quita solo su entrada.
