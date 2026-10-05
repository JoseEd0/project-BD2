# Archivo secuencial paginado

## Qué es

Los registros se mantienen **ordenados por una clave**. Eso convierte la búsqueda en una
búsqueda binaria sobre las páginas y hace que un recorrido ordenado o una consulta por
rango sean gratis: basta leer las páginas en orden.

El problema del orden es la inserción: meter una clave en medio obligaría a desplazar todo
el archivo. La solución clásica —la que implementa este módulo— es tener **dos espacios**.

## Cómo funciona

```
ESPACIO PRINCIPAL (tabla.seq)            ESPACIO AUXILIAR (tabla.seq.overflow)
┌──────────────────────────────┐
│ pág 0: cabecera              │
├──────────────────────────────┤
│ pág 1: 10 14 21 30           │──┐
├──────────────────────────────┤  │      ┌──────────────────┐
│ pág 2: 35 41 47 52           │──┼─────►│ 44  38  ...      │  cadena de la pág 2
├──────────────────────────────┤  │      └──────────────────┘
│ pág 3: 60 71 79              │  │
└──────────────────────────────┘  └────► −1 (sin desbordamiento)

Invariante: toda clave de la página p (y de su cadena) es menor o igual que la primera
clave de la página p+1. Por eso la búsqueda binaria sobre las páginas es correcta.
```

Dos casos de borde salen de ese invariante, y los dos tienen su test:

- **Claves por debajo de la primera.** Si la primera página está llena y llega una clave
  menor que todas, va al desbordamiento de esa página. La primera clave de la página 1
  deja entonces de ser la menor del archivo: una búsqueda por rango no puede descartar
  esa página mirando solo su primera clave.
- **Una clave repetida que cruza de página.** El «menor **o igual**» importa: tras una
  reorganización, las copias de una misma clave quedan seguidas y pueden ocupar el final
  de una página y el principio de la siguiente. Buscar, recorrer un rango y borrar empiezan
  en la última página que comienza por una clave **estrictamente menor** y siguen mientras
  las páginas empiecen por esa clave.

### Insertar

1. **Búsqueda binaria** sobre las páginas principales: se busca la última página cuya
   primera clave no supera a la nueva. Cuesta `O(log P)` lecturas.
2. Si esa página tiene sitio → se inserta **desplazando a la derecha** las ranuras
   posteriores, de modo que la página siga ordenada.
3. Si está llena y la clave es mayor que todas las suyas y es la última página → se añade
   una página principal nueva al final. Gracias a este caso, cargar datos ya ordenados
   llena el espacio principal sin generar desbordamiento.
4. Si está llena y la clave va en medio → el registro se manda a la **cadena de
   desbordamiento** de esa página. La cadena no está ordenada: se lee entera cuando hace
   falta.

### Buscar

1. Búsqueda binaria hasta la página, `O(log P)`.
2. Búsqueda binaria dentro de la página (sus ranuras están ordenadas).
3. Recorrido lineal de su cadena de desbordamiento.
4. Si la página termina justo en esa clave, se mira también la siguiente; casi nunca
   hace falta, y cuando la clave es única cuesta como mucho una página más.

### Buscar por rango

Se localiza la página donde empezaría el extremo inferior y se avanza leyendo cada página
con su cadena, ya ordenadas, hasta el primer registro que se pasa del extremo superior.
Cualquiera de los dos extremos puede faltar: `id >= 500` empieza en su página y llega al
final, y `id <= 500` empieza en la primera, sin recorrer antes el archivo para averiguar
dónde acaba.

El recorrido ordenado (`scan`) va página por página y, en cada una, mezcla en memoria las
ranuras de la página con las de su cadena y las ordena. La cadena es corta por
construcción, así que esto no viola la regla de no cargar el archivo entero en memoria.

### Borrar — eliminación lazy

Borrar de verdad obligaría a desplazar media página por cada baja. En vez de eso se marca
una **lápida** (estado `DELETED`): el registro sigue ocupando su ranura y su clave sigue
marcando su posición dentro del orden, pero deja de ser visible.

### Reorganizar

Las lápidas y el desbordamiento degradan el archivo: ocupan espacio y obligan a recorridos
lineales. El archivo mide su desperdicio:

```
desperdicio = (lápidas + registros en desbordamiento) ÷ registros almacenados
```

Cuando supera `sequential_waste_ratio` (**30 %** por defecto), se dispara la
**reorganización**: se recorre todo en orden de clave, se descartan las lápidas y se
reescribe el espacio principal llenando cada página hasta `sequential_fill_factor`
(**80 %** por defecto). El espacio auxiliar queda vacío.

El factor de llenado es deliberado: dejar un 20 % de hueco en cada página hace que las
inserciones siguientes quepan en el sitio que les toca en vez de irse al desbordamiento.

```
antes:  60 registros, 8 lápidas, 15 en desbordamiento  → desperdicio 34 % ⇒ reorganiza
después: 52 registros, 0 lápidas, 0 desbordamiento, páginas al 80 %
```

## Complejidad

| Operación | Coste | Nota |
|---|---|---|
| Búsqueda por igualdad | `O(log P)` + cadena | P = páginas principales |
| Inserción | `O(log P)` + `O(c)` bytes movidos | c = registros por página |
| Búsqueda por rango | `O(log P + k/c)` | k = registros devueltos |
| Recorrido ordenado | `O(P)` | sin ordenar nada |
| Borrado | `O(log P)` + cadena | solo marca la lápida |
| Reorganización | `O(N)` lecturas + `O(N)` escrituras | amortizada entre muchas operaciones |

## Heap file vs archivo secuencial

| | Heap | Secuencial |
|---|---|---|
| Insertar | O(1) | O(log P) + desplazamiento |
| Buscar por clave | O(P) | O(log P) |
| Rango / orden | hay que ordenar todo | directo |
| Coste oculto | ninguno | reorganizaciones periódicas |

Regla práctica: **heap** si se escribe mucho y se consulta por índice; **secuencial** si se
consulta por rango o se necesita el orden.

## Uso

```python
from storage.sequential import SequentialFile

with SequentialFile(path, serializer, key_field="id", config=config) as archivo:
    archivo.insert(serializer.pack((10, "ana", 4.5)))
    filas = archivo.search(10)
    for raw in archivo.range_search(10, 50):      # (10, None) o (None, 50): rango abierto
        ...
    archivo.delete(10)
    archivo.reorganize()          # también se dispara sola al superar el umbral
```

## Tests

```bash
.venv/bin/python -m pytest tests/storage/sequential -q
```

Cubren: archivo vacío, un registro, inserción desordenada que debe leerse ordenada, carga
ordenada sin desbordamiento, inserción en medio de una página llena que **sí** genera
desbordamiento, rangos cerrados, abiertos por cualquier lado e invertidos, claves
duplicadas —también cuando cruzan de una página a la siguiente—, claves menores que la
primera del archivo, borrado lazy y borrado repetido, umbral de desperdicio que dispara la
reorganización, reorganización manual, persistencia al reabrir y archivos que no son de
este formato.

Además, una prueba aleatoria compara el archivo con una lista ordenada a lo largo de más
de mil inserciones, borrados y reorganizaciones, con claves repetidas y negativas: tras
cada tanda, `scan`, `search` y los tres tipos de rango tienen que coincidir con la lista.
