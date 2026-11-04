# Índice invertido (SPIMI)

## Qué es

El índice de la búsqueda de texto. Para cada **término** guarda la lista de documentos en
los que aparece y cuántas veces (su *lista de postings*). Una consulta `MATCH(texto, 'cable
usb')` lee la lista de `cabl` y la de `usb` en vez de analizar todos los textos de la tabla.

Se construye con **SPIMI** (*Single-Pass In-Memory Indexing*, Heinz y Zobel, 2003), el
algoritmo que pide el enunciado: una sola pasada sobre los documentos con la memoria
acotada por `EngineConfig.spimi_block_bytes`, sin importar cuántos documentos haya.

El índice no sabe de tablas ni de SQL. Un documento es un par `(RecordId, texto)`: la
dirección de su fila y lo que hay que indexar.

Sobre el mismo índice, `Ranker` ordena los documentos que contienen algún término de la
consulta con las dos técnicas que pide el enunciado: **TF-IDF + similitud del coseno** y
**BM25** (ver [Ranking](#ranking)).

Después de construirse, el índice admite inserciones y borrados sin reconstruirse: los
documentos nuevos van a **segmentos** que se fusionan con la misma mezcla de SPIMI, y los
borrados dejan una **lápida** (ver [Mantenimiento](#mantenimiento-segmentos-y-lápidas)).
`TextSearchIndex` lo conecta a una tabla como índice secundario, con sus transacciones.

## Uso

```python
from config import EngineConfig
from index.inverted import InvertedIndex
from text import Analyzer

config = EngineConfig()
documentos = [(record_id, texto), ...]          # cualquier iterable, se recorre una vez
with InvertedIndex.build(ruta, documentos, Analyzer(config), config) as indice:
    lista = indice.postings("cabl")             # PostingList, con el término ya analizado
    lista.doc_ids, lista.term_frequencies       # array('I') en orden de doc_id
    indice.document_frequency("cabl")           # df
    indice.document(7)                          # RecordId, longitud, norma lnc, borrado
    indice.document_count, indice.average_length

InvertedIndex(ruta, config)                     # abrir uno ya construido
indice.insert(record_id, "teclado mecánico")    # doc_id nuevo; las búsquedas ya lo ven
indice.delete(record_id)                        # lápida, O(1) páginas
indice.flush()                                  # escribe el segmento y fusiona si toca

ranker = Ranker(indice, config)                 # analiza la consulta en el idioma del índice
ranker.top_k("cable usb", RankingMethod.BM25, 10)          # list[ScoredDocument]
ranker.top_k("cable usb", RankingMethod.BM25, 10, Bm25Parameters(k1=2.0, b=0.5))
for documento in ranker.ranked("cable usb", RankingMethod.TF_IDF):   # perezoso, en orden
    documento.doc_id, documento.record_id, documento.score
```

| API | Qué da |
|---|---|
| `InvertedIndex.build(path, documents, analyzer, config)` | construye con SPIMI y abre; `InvertedIndexExistsError` si ya hay uno |
| `InvertedIndex(path, config)` | abre; `InvertedIndexNotFoundError` o `InvertedFormatError` |
| `files_of(path)` | todos sus archivos, los de cada segmento incluidos, para `DROP INDEX` |
| `lookup(term) -> TermEntry \| None` | término, df y frecuencia total sumados entre segmentos, y dónde está su lista en cada uno |
| `postings(term)`, `postings_of(entry) -> PostingList` | la lista de todos los segmentos, en orden de `doc_id`; vacía si el término no está |
| `document_frequency(term)` | df, 0 si no está; cuenta los borrados hasta la próxima fusión |
| `document(doc_id) -> DocumentEntry` | `record_id`, `length`, `norm`, `deleted`; `DocumentNotFoundError` |
| `documents()`, `terms()` | recorridos completos en orden de `doc_id` y de término |
| `document_count`, `deleted_count`, `total_length`, `average_length` | N y la suma de longitudes de los vivos, los borrados y la `avgdl` de BM25 |
| `vocabulary_size`, `posting_count`, `block_count`, `merge_passes`, `language` | forma del índice |
| `segment_count`, `pending_document_count`, `merge_count` | segmentos en disco, documentos aún en memoria y fusiones completas |
| `document_columns() -> DocumentColumns` | longitudes, normas y direcciones por `doc_id` en memoria, y los borrados; se carga la primera vez y se mantiene al día |
| `insert(record_id, text) -> int`, `delete(record_id) -> bool` | indexar una fila y quitarla; `DocumentAlreadyIndexedError` si la dirección ya tiene documento |
| `flush()`, `merge()` | volcar (escribe el bloque en memoria y fusiona si toca) y fusionar todo ya |
| `describe(top_terms=10)` | todo lo anterior, los segmentos, el bloque en memoria, tamaños y los términos con más df |
| `close()`, `with` | `flush` y soltar los archivos |

`weights.py` tiene el peso `1 + log₁₀ tf` y la norma lnc. El ranking (TF-IDF y BM25) usa
esas mismas funciones para la consulta, así que los dos lados del coseno salen de una sola
fórmula.

## Los archivos

Una ruta base `productos.inv` da varios archivos con sufijo. Los que guardan términos y
listas forman un **segmento** (léxico, términos y postings); la construcción escribe el
primero, el `seg0`, y el mantenimiento añade otros. La tabla de documentos, el manifiesto
y el mapa de direcciones son uno por índice. La página 0 de cada archivo paginado es su
cabecera, con firma y versión (`FileHeader`): abrir un archivo de otra estructura o de
otro formato da `InvertedFormatError` en vez de leer basura.

```
productos.inv.seg0.lexicon      IVLX  │ cabecera │ registros del léxico (fijos, ordenados) │ fronteras │
productos.inv.seg0.terms        IVTM  │ cabecera │ cabl teclad usb … (términos seguidos, sin relleno) │
productos.inv.seg0.postings     IVPS  │ cabecera │ lista de cabl │ lista de teclad │ lista de usb │ … │
productos.inv.documents         IVDC  │ cabecera │ doc 0 │ doc 1 │ doc 2 │ … (registros fijos) │
productos.inv.manifest          IVMF  │ cabecera │ generación de cada segmento │   (sin páginas)
productos.inv.docmap, .docmap.dir     hash extendible: dirección de la fila → doc_id
```

| Archivo | Registro | Bytes | Por página de 4 KB |
|---|---|---:|---:|
| léxico | inicio del término (Q), su longitud (H), df (I), frecuencia total (Q), inicio de la lista (Q) | 30 | 131 |
| términos | el término en UTF-8 | variable | — |
| postings | `doc_id` (I), `tf` (I), little-endian | 8 | 512 |
| documentos | página y ranura de la fila (I H), longitud (I), norma lnc (d) | 18 | 215 |

Las cabeceras guardan:

- **léxico**: idioma del analizador, número de términos, bytes de términos, número de
  postings, bytes de las fronteras, y los bloques (o segmentos fusionados) y las pasadas
  de mezcla con que se escribió el segmento;
- **términos**: sus bytes, que tienen que coincidir con los del léxico;
- **postings**: su número, que tiene que coincidir con el del léxico;
- **documentos**: los `doc_id` repartidos, cuántos están borrados y la suma de las
  longitudes de los vivos;
- **manifiesto**: la siguiente generación, los borrados ya purgados, las fusiones hechas y
  cuántos segmentos enumera.

Si los contadores repetidos no coinciden, los archivos no son del mismo índice y abrirlo es
un `InvertedFormatError`.

### Invariantes

1. **El léxico está ordenado por los bytes UTF-8 del término**, que es el mismo orden que
   el de los puntos de código: `ano < año < nandu < ñandu`.
2. **La lista de un término es contigua** en el archivo de postings y está **ordenada por
   `doc_id`**, sin repetidos. Su posición se cuenta en postings, no en bytes.
3. **Los términos de una página del léxico son contiguos** en el archivo de términos, en el
   mismo orden. Una página se resuelve leyendo un solo tramo.
4. **El `doc_id` es la posición del documento** en la tabla de documentos: denso, desde 0,
   en el orden en que llegaron, y **nunca se reutiliza**. Llegar a uno es una división.
5. **La marca de borrado es el estado de la ranura** (`SlotState.DELETED`), la lápida de
   `RecordPage`: no cuesta un byte más.
6. Las **fronteras** son el primer término de cada página del léxico, en orden.
7. **Cada segmento tiene `doc_id` mayores que los del anterior** en el manifiesto, y el
   bloque en memoria, mayores que todos: la lista de un término en el índice es la
   concatenación de sus listas en ese orden, ya ordenada.

### Por qué así

**Pares de ancho fijo y sin comprimir.** Una lista se decodifica con un solo
`array('I').frombytes`, en C, y los `doc_id` y los `tf` salen de dos rebanadas con salto
2. Comprimir (deltas con *varint*, como Lucene) ahorraría espacio, pero decodificar un
entero de longitud variable es un bucle de Python por posting: en este motor el disco es
más barato que el intérprete.

**El `tf` en 32 bits.** Con 16 bits, un documento que repite un término 65 536 veces (un
código de producto pegado mil veces, por ejemplo) desbordaría. Recortarlo cambiaría el
ranking en silencio; ensancharlo cuesta dos bytes por posting y deja los dos campos del
mismo tipo, que es lo que permite decodificar con un solo `array`. El `doc_id` en 32 bits
admite cuatro mil millones de documentos.

**Los términos en un archivo aparte.** La primera versión guardaba el término dentro del
registro del léxico, en un campo del ancho del término más largo. Con las 100 000 fichas de
ESCI el término medio mide **6,9 bytes**, pero unas frases en japonés (sin espacios, un
solo token) llegan a **96**, y el léxico ocupaba 18,2 MB. Con un registro fijo de 30 bytes
que apunta al término, léxico y términos ocupan **5,8 MB**, a cambio de leer una página más
por búsqueda.

**Las fronteras en disco.** Se escriben al construir y se leen al abrir, una entrada por
página del léxico (1 152 con 100 000 documentos). Sin ellas, abrir el índice obligaría a
leer todas las páginas del léxico para saber cuál es su primer término.

**La norma lnc se calcula al indexar.** La «n» de lnc es «sin idf», así que la norma de un
documento solo depende de sus propias frecuencias: no cambia cuando llegan documentos
nuevos y el ranking del coseno no tiene que recalcularla. Como casi todos los `tf` son 1, 2
o 3, el cuadrado `(1 + log₁₀ tf)²` de cada `tf` se calcula una vez y se reutiliza: las
normas de las 100 000 fichas de ESCI pasan de 0,49 a 0,10 s (la mejor de 5 pasadas), y son
las mismas bit a bit, porque `math.fsum` redondea una sola vez, al final.

**Base 10 en `1 + log tf`.** Es la de SMART y la del libro de Manning, Raghavan y Schütze.
No es un detalle de escala: con el `1 +`, cambiar la base cambia el ranking.

**Longitud máxima de un término.** El analizador descarta tokens de más de
`text_max_token_length` caracteres (40), y UTF-8 usa como mucho cuatro bytes por carácter:
ningún término pasa de `max_term_bytes` = 160 bytes. SPIMI lo comprueba con cada término
nuevo y lanza `TermTooLongError` si no se cumple, que solo puede pasar si el analizador y
el índice usan configuraciones distintas.

## SPIMI

```
documentos ──► analizar ──► bloque en memoria ──(lleno)──► bloque en disco ─┐
                            término → array('I')                              │
                                                                              ▼
            léxico + términos + postings ◄── mezcla k-vías ◄── … bloques en disco
```

### 1. La pasada sobre los documentos

Cada documento se analiza (`Analyzer.analyze`), recibe el siguiente `doc_id`, se apunta en
la tabla de documentos con su longitud y su norma, y sus términos van al **bloque**: un
diccionario `término → array('I')` con los postings intercalados
`doc_id, tf, doc_id, tf, …`. Es lo que distingue a SPIMI de BSBI: no hay un diccionario
global término→id ni pares `(id, doc)` que ordenar después; la lista de cada término crece
en su sitio, y como los documentos llegan en orden, ya está ordenada por `doc_id`.

Guardar un `array` por término y no una lista de tuplas importa: con 7,9 millones de
postings, una tupla de dos enteros por posting serían 7,9 millones de objetos de Python.

Cuando el bloque llega a `spimi_block_bytes`, sus términos se ordenan, se escribe en disco y
se empieza uno nuevo (no se vacía el viejo: un diccionario no devuelve la memoria de su
tabla al vaciarse). Un documento nunca se parte entre dos bloques.

### 2. Cuánto ocupa un bloque

`SpimiBlock.estimated_bytes` es una cuenta determinista, la misma en cualquier máquina,
para que los tests fuercen un número exacto de bloques:

```
estimación = Σ por término (TERM_ENTRY_BYTES + caracteres del término)
           + postings · POSTING_ENTRY_BYTES            con 160 y 10 bytes
```

Las dos constantes salen de medir con `tracemalloc` un bloque de las 10 000 fichas de ESCI
(40 936 términos y 782 838 postings): la entrada del diccionario, la cadena y el `array`
vacío de cada término nuevo; y los 8 bytes de cada posting más lo que el `array` reserva de
sobra al crecer. Para ese bloque la estimación da 14,6 MB y `tracemalloc` midió 11,0 MB sin
contar las cadenas de los términos, que en esa medida ya existían. Las constantes quedan
del lado alto a propósito: el bloque real no pasa del presupuesto.

### 3. El formato de un bloque

Un bloque son dos archivos temporales, flujos de bytes sin cabecera
(`storage/stream.py`):

```
blockN.terms      │ len df cf término │ len df cf término │ …     (en orden de término)
blockN.postings   │ lista del 1.º      │ lista del 2.º      │ …     (en el mismo orden)
```

Separarlos es lo que deja mezclar sin cargar listas. Para saber que un término terminó, la
mezcla tiene que leer la cabecera del siguiente; si término y lista estuvieran juntos, eso
la obligaría a saltar (o cargar) la lista. Así, cada flujo de postings se lee una sola vez
y en orden, con una página en memoria.

### 4. La mezcla

`merge_sources` mezcla k bloques con el mismo montículo que el ordenamiento externo
(`merge_sorted_streams`, en `external/runs.py`): sale el término menor de entre las
cabeceras pendientes y, a igual término, el del bloque que va primero. Los miembros de un
mismo término se juntan: su df y su frecuencia total se suman, y sus listas se copian una
tras otra, **sin decodificarlas**.

La copia sin comparar es correcta porque **cada bloque tiene `doc_id` mayores que los del
anterior**: concatenar sus listas en el orden de los bloques da una lista ordenada.

Si hay más bloques que `merge_fan_in`, se mezclan por pasadas, como en el ordenamiento
externo: cada grupo de hasta `k` bloques **consecutivos** pasa a ser un bloque, lo que
conserva el orden de los `doc_id` entre bloques. La última pasada escribe el primer
segmento del índice. Si todo cupo en un bloque, va directo de memoria al segmento, sin
pasar por disco.

Una pasada intermedia junta **solo los bloques que sobran**, empezando por el final: con
9 bloques y abanico 8 basta mezclar los dos últimos para que queden 8, en lugar de
reescribir los nueve en dos bloques. Por el final, porque ahí están los más chicos: el
último bloque de SPIMI es el que quedó a medias, y al mantener el índice los segmentos
más nuevos son los menores. El índice que sale es el mismo byte a byte, y las pasadas son
las mismas; con las 100 000 fichas de ESCI (9 bloques), la construcción baja de 14,4 a
14,0 s (mediana de 3, alternando las dos versiones).

La mezcla recibe **fuentes** (`TermSource`): cualquier cosa que entregue términos en orden
y, tras cada uno, su lista. Al construir son bloques; al mantener el índice, sus segmentos
(ver [Mantenimiento](#mantenimiento-segmentos-y-lápidas)). Las pasadas las lleva
`MergePasses`, que solo borra los bloques intermedios que escribió él.

Los bloques viven en `productos.inv.spimi/`, que se borra siempre, también si la
construcción falla; y si falla, tampoco quedan los archivos del índice.

### 5. Un bloque o cien: el mismo índice

El índice que sale es **idéntico byte a byte** se use un bloque o muchos (salvo los dos
contadores de bloques y pasadas de la cabecera): el léxico, los términos, las listas y la
tabla de documentos. Es el test central del módulo, y también se comprobó con las 10 000
fichas de ESCI: con un bloque de 512 KB y abanico 2 salen **111 bloques y 7 pasadas**, y los
archivos coinciden con los del índice de un solo bloque.

### Complejidad

Con `T` postings, `V` términos, `B` bloques y abanico `k`:

| | Coste |
|---|---|
| pasada sobre los documentos | `O(T)` inserciones en el diccionario, más ordenar los términos de cada bloque |
| mezcla | `⌈log_k B⌉` pasadas; cada una lee y escribe todos los postings una vez, y hace `O(log k)` comparaciones por término de cada bloque |
| memoria al construir | el bloque, más una página por flujo de cada bloque abierto en la mezcla |
| buscar un término | `O(log V)` comparaciones en memoria, una página del léxico y una o dos de términos |
| leer una lista de `df` postings | `⌈8·df / página⌉ + 1` páginas consecutivas |
| un documento por `doc_id` | una página |

## Medido con ESCI

Medición manual, no un experimento versionado (ese es el de la Parte 3, en
`benchmarks/`): `InvertedIndex.build` sobre la columna `texto` de las fichas de producto
del subconjunto en español de Amazon ESCI, leídas del CSV con `csv`, con la configuración
por omisión: páginas de 4 KB, bloques de 16 MiB, abanico 8. Apple M4 Pro,
Python 3.12. La memoria pico de Python se midió con `tracemalloc` en una ejecución aparte
(trazar enlentece la construcción); la memoria residente, con `/usr/bin/time -l`.

| | 10 000 documentos | 100 000 documentos |
|---|---:|---:|
| términos tras el análisis | 1 310 824 | 13 184 680 |
| vocabulario | 40 936 | 150 896 |
| postings | 782 838 | 7 896 118 |
| bloques / pasadas de mezcla | 1 / 0 | 9 / 2 |
| tiempo de construcción | 1,8 s | 15,1 s |
| pico de `tracemalloc` | 17,6 MiB | 24,4 MiB |
| memoria residente máxima | 59 MiB | 79 MiB |
| léxico + términos | 1,3 + 0,3 MB | 4,7 + 1,0 MB |
| postings | 6,3 MB | 63,2 MB |
| documentos | 0,2 MB | 1,9 MB |

Diez veces más documentos cuestan un **35 % más de memoria**, no diez veces más: es el
bloque acotado. Del tiempo, 9,4 s de los 15 son el análisis del texto (plegado, tokens
y raíces), medido aparte sobre los mismos 100 000 documentos; leer el CSV, los bloques y
la mezcla suman el resto. La memoria residente incluye la caché de términos del
analizador, que tiene tope (dos generaciones de 65 536 entradas). Estas cifras son de SPIMI solo: la construcción escribe además el
mapa de direcciones del mantenimiento, que con 99 000 documentos suma 1,1 s y 1,9 MB (ver
[De la dirección de la fila al `doc_id`](#de-la-dirección-de-la-fila-al-doc_id)).

Con 100 000 documentos el tamaño del bloque mueve poco el tiempo (16,2 s con 8 MiB y 14,8 s
con 32 MiB: menos bloques que mezclar) y bastante más la memoria:

| `spimi_block_bytes` | bloques | pasadas | tiempo | memoria residente |
|---:|---:|---:|---:|---:|
| 8 MiB | 20 | 2 | 16,2 s | 66 MiB |
| 16 MiB | 9 | 2 | 15,1 s | 79 MiB |
| 32 MiB | 4 | 1 | 14,8 s | 89 MiB |

La comparación con GIN de PostgreSQL, las consultas y la relevancia son ese experimento.

## Ranking

`ranking.py` puntúa los documentos que contienen **algún** término de la consulta, con
TF-IDF + coseno o con BM25. Los dos leen las mismas listas, el mismo df y la misma tabla
de documentos, así que la comparación entre ellos solo mide la fórmula.

| API | Qué da |
|---|---|
| `Ranker(index, config)` | el ranking de un índice; su analizador es el del idioma del índice |
| `top_k(query, method, k, parameters=None, stats=None)` | los `k` mejores, `list[ScoredDocument]` |
| `ranked(query, method, parameters=None, stats=None)` | todos los que coinciden, perezoso y en el mismo orden |
| `analyzed(query)` | los términos de la consulta ya analizados, con su df, sin leer postings: lo que muestran `EXPLAIN` y el resaltado de la interfaz |
| `ScoredDocument` | `doc_id`, `record_id` de la fila y `score` |
| `RankingMethod` | `TF_IDF` o `BM25` |
| `Bm25Parameters(k1, b)` | parámetros de una consulta; sin ellos, `EngineConfig.bm25_k1` y `bm25_b` |
| `RankingStats` | para `EXPLAIN`: cada término analizado con su df, postings leídos y documentos puntuados |
| `RankingParameterError` | `k` negativo, `k1` o `b` fuera de rango, o parámetros de BM25 para TF-IDF |
| `require_fitting_parameters(method, parameters)` | la regla de los parámetros de BM25 pedidos a TF-IDF, que el planificador aplica antes de leer filas |

`RankingMethod` es un enum propio del índice y no el de `sql.nodes`: ningún módulo de
`index` depende del front-end SQL, y el planificador traduce uno al otro.

### La consulta se analiza como los documentos

La consulta pasa por el mismo `Analyzer`, construido con el idioma que guarda la cabecera
del léxico. Un término de la consulta que no está en el índice no aporta nada: no tiene
lista y su idf sería infinito, así que se deja fuera también de la norma de la consulta.
Una consulta vacía o de solo stopwords no tiene términos y no devuelve ningún documento.

### TF-IDF + coseno: SMART lnc.ltc

Es el esquema del capítulo 6 de Manning, Raghavan y Schütze (*Introduction to Information
Retrieval*). Con `N` documentos y `df_t` los que contienen el término `t`:

```
documento (lnc):  w(t, d) = (1 + log₁₀ tf(t, d)) / ‖d‖     ‖d‖ = √Σ_t (1 + log₁₀ tf(t, d))²
consulta  (ltc):  w(t, q) = (1 + log₁₀ tf(t, q)) · log₁₀(N / df_t) / ‖q‖
puntaje:          cos(q, d) = Σ_t w(t, q) · w(t, d)
```

**Por qué lnc.ltc.** El idf va en un solo lado del producto, el de la consulta. Así la
norma del documento no depende del corpus: se calcula al indexar (`weights.py`), se guarda
en la tabla de documentos y nunca hay que recalcularla cuando llegan documentos o cambia
el df. Con ltc.ltc, cada documento nuevo cambiaría el idf de sus términos y con él la
norma de todos los documentos que los contienen. Es también el esquema que el libro usa
de ejemplo para la búsqueda ad hoc.

Las dos normas cumplen funciones distintas: `‖d‖` es la que ordena (un documento largo no
gana por repetir palabras); `‖q‖` es la misma para todos los documentos y solo lleva el
puntaje a [0, 1], que es lo que hace del resultado un coseno.

### BM25

```
idf_t       = ln(1 + (N - df_t + 0,5) / (df_t + 0,5))
puntaje(d)  = Σ_t idf_t · tf · (k1 + 1) / (tf + k1 · (1 - b + b · dl / avgdl))
```

con `dl` la longitud del documento en términos y `avgdl` la media del índice. Es el idf de
Lucene: el de Robertson y Spärck Jones sin el `1 +` se vuelve negativo para un término
que está en más de la mitad de los documentos, y un documento perdería puntaje por
contenerlo. Con el `1 +` es positivo siempre.

`k1 = 1,2` y `b = 0,75` por omisión (`EngineConfig`), los valores de Robertson y
Zaragoza y los de Lucene. Una consulta puede cambiarlos con `Bm25Parameters`; `k1` tiene
que ser finito y no negativo (con 0 solo cuenta si el término aparece) y `b` estar entre
0 (sin normalizar por longitud) y 1 (normalización completa). La misma regla valida la
configuración (`bm25_parameters_error`, en `config.py`).

### Decisiones

| Caso | Qué pasa | Por qué |
|---|---|---|
| término repetido en la consulta | TF-IDF lo pondera con `1 + log₁₀ tf(t, q)`; BM25 lo cuenta **una vez** | es la «l» de ltc; en BM25 es la fórmula sin el factor de la consulta (`k3 = 0` de Robertson), la de consultas cortas. Tras el stemming, una repetición suele ser la misma palabra en singular y plural («funda fundas»), que no debería pesar el doble |
| empate de puntaje | primero la fila de menor dirección (`RecordId`) | el orden depende solo de las filas vivas, no de cuándo llegó cada una al índice: tras un `ROLLBACK` o un `UPDATE` del texto, la fila tiene un `doc_id` nuevo pero la misma posición entre sus empates. En un índice recién construido desde una tabla coincide con el orden de `doc_id`, porque el heap se recorre en orden de dirección |
| término en todos los documentos | TF-IDF le da idf 0: los documentos que solo tienen ese término **entran con puntaje 0** | el conjunto de resultados es el mismo con los dos rankings (los documentos con algún término), que es lo que comparan el experimento y `MATCH` sin `LIMIT` |
| todos los términos en todos los documentos | la consulta es el vector nulo: coseno 0 para todos | el coseno con un vector nulo no está definido; 0 es el valor que no ordena nada |
| `k = 0` | lista vacía | es `LIMIT 0` |
| `k < 0` | `RankingParameterError` | no hay `LIMIT` negativo |
| `k > N` | todos los que coinciden | |
| documento borrado | no sale ni cuenta en `documents_scored` | se filtra al final, sobre los acumuladores. N y `avgdl` ya no lo cuentan; el df sí, hasta la próxima fusión (ver [Mantenimiento](#mantenimiento-segmentos-y-lápidas)) |
| df mayor que N | se usa `min(df, N)` | solo pasa con borrados sin fusionar: con `df > N`, el idf de TF-IDF, `log₁₀(N/df)`, sería negativo y contener el término restaría. Tras una fusión `df ≤ N` y la cota no cambia nada |
| ningún documento vivo con términos | no devuelve nada | lo que quede en las listas es de borrados, y N o `avgdl` serían 0 en las fórmulas |

### Evaluación término a término

```
para cada término t de la consulta que está en el índice:
    peso de t en la consulta (TF-IDF) o su idf (BM25)
    para cada (doc_id, tf) de su lista:
        acumulador[doc_id] += contribución(tf, doc_id)
TF-IDF: acumulador[doc_id] /= ‖d‖          (la norma es común a todos sus términos)
quitar los borrados
```

- Las listas son `array('I')` y se recorren con `zip(doc_ids, tfs)`, sin un objeto por
  posting y con los métodos y constantes sacados a variables locales.
- En TF-IDF, `w(t, q) · (1 + log₁₀ tf)` se calcula una vez por **tf distinto** de la lista
  (casi todos son 1, 2 o 3) y el bucle solo suma.
- En BM25, `k1 · (1 - b + b · dl / avgdl)` se escribe como `a + c · dl`, con `a` y `c`
  calculados una vez por consulta.
- La división por `‖d‖` se hace una vez por documento y no una vez por posting.

**Las longitudes, normas y direcciones en memoria.** BM25 necesita el `dl` de cada
posting y el coseno la norma de cada documento puntuado, que con una consulta de cinco
palabras pueden ser 18 000 de 100 000; el desempate, la dirección de la fila de cada uno.
Leerlas de la tabla de documentos costaría una página por documento. `document_columns()`
las carga una vez por índice abierto en tres `array` (`I`, `d` y `Q`, con la dirección
como el entero `página << 16 | ranura`, que se ordena igual que ella), 20 bytes por
documento, más el conjunto de los borrados: son las *norms* que Lucene tiene en memoria
por la misma razón. Con 100 000 documentos ocupan 2 MB. Cada inserción y cada borrado las
actualiza en el momento.

**Top-k.** `heapq.nlargest(k, puntajes)`, un montículo de tamaño `k` sobre los puntajes
sueltos, da el `k`-ésimo mejor; solo los documentos que lo alcanzan reciben su clave
`(-puntaje, doc_id)` y se ordenan, con la dirección de la fila para los empatados.
`O(C log k)` para `C` candidatos, sin leer ninguna página para devolverlos. Antes se
armaba la tupla de los `C` candidatos y se le pedía el top a `heapq.nsmallest`; con las
150 consultas de ESCI y 100 000 fichas (tiempo de CPU, la mejor de 15 pasadas, alternando
las dos versiones, mismo resultado), elegir sobre los puntajes sueltos baja TF-IDF de
0,46 / 2,38 / 5,61 ms a **0,35 / 1,83 / 4,31 ms** con 1 / 3 / 5+ palabras, y BM25 (ya
con la poda de abajo) de 0,46 / 1,63 / 4,37 ms a **0,34 / 1,44 / 4,13 ms**.

### Poda MaxScore en el top-k de BM25

Para los `k` mejores no hace falta el puntaje de todos los candidatos, solo saber que los
demás no pueden entrar. Es la poda **MaxScore** de Turtle y Flood (1995), en su versión
término a término:

```
cota(t) = idf_t · (k1 + 1)          (tf / (tf + k1 · (…)) no pasa de 1)
tras puntuar cada término:
    resto  = Σ cota de los términos que faltan
    umbral = k-ésimo mejor puntaje parcial entre los vivos
    si resto < umbral:
        ningún documento nuevo puede entrar: de las listas que faltan solo se
        puntúan los que ya tienen acumulador (`PostingList.restricted_to`)
        y se descarta el que tiene parcial + resto < umbral
```

- **El resultado es el mismo, bit a bit.** Los términos se recorren en el orden de la
  consulta, como sin poda, así que cada documento que sigue suma las mismas
  contribuciones en el mismo orden. El umbral nunca pasa del `k`-ésimo puntaje final,
  porque los puntajes solo crecen; un documento se descarta solo si su cota queda **por
  debajo**, nunca si lo iguala, para que el empate lo siga resolviendo la dirección de la
  fila. Las cotas se inflan en `UPPER_BOUND_SLACK_FACTOR` (1 + 10⁻⁹) contra el redondeo,
  y el umbral no cuenta a los borrados. Un test compara el top-k podado con los `k`
  primeros del orden completo (`ranked`, que no poda) en corpus al azar, con cuatro
  pares de `k1` y `b` y `k` de 1, 3 y 10.
- **Solo en `top_k`.** `ranked` no sabe cuántos documentos le van a pedir.
- **La lista se lee igual,** porque las listas no tienen saltos: lo que se ahorra es
  puntuar. Restringir una lista a los que siguen se hace con `map` y `compress`, en C.
- **El plan lo dice:** `EXPLAIN ANALYZE` añade «poda MaxScore desde *término*».
- **Las cotas no se guardan:** `idf · (k1 + 1)` sale del df y de los parámetros de la
  consulta. En el orden de la consulta, la poda empieza tras haber puntuado entero el 60 %
  de los postings con tres palabras y el 70 % con cinco o más. Recorriendo los términos
  de mayor a menor cota bajaría al 33 % y al 27 %, y con la cota exacta de cada lista (su
  contribución máxima, que habría que guardar en el léxico) al 31 % y al 23 %; pero el
  orden de las sumas cambiaría, y con él el último bit de algunos puntajes.

Medido sobre las 150 consultas de ESCI con 100 000 fichas (tiempo de CPU, la mejor de
15 pasadas, alternando las dos versiones; el resultado es idéntico en las 300
búsquedas):

| BM25, `k = 10` | 1 palabra | 3 palabras | 5+ palabras |
|---|---:|---:|---:|
| sin poda | 0,44 ms | 2,25 ms | 5,34 ms |
| con poda | 0,45 ms | **1,60 ms** | **4,35 ms** |

Con una palabra no hay nada que podar.

**TF-IDF no poda.** Sin estadísticas por lista, la única cota de un término es `q_t`, la
del coseno de un documento de un solo término: `(1 + log₁₀ tf) / ‖d‖` no pasa de 1. Una
ficha tiene decenas de términos y su cociente real es mucho menor, así que la poda casi no
se activa: con la cota exacta de cada lista se habría saltado el 12 % de los postings con
tres palabras y el 17 % con cinco o más, y con `q_t`, ninguno y el 3 %. Una versión que
podaba también TF-IDF lo confirmó: buscar el umbral costaba más de lo que ahorraba, y
TF-IDF tardaba 3,1 ms en lugar de 2,6 con tres palabras y 8,3 ms en lugar de 6,1 con
cinco o más.

**El iterador perezoso** (`ranked`) es para una consulta con filtros (`AND precio < 100`)
que no sabe cuántos documentos va a consumir. Forma un montículo con todos los candidatos,
`O(C)`, y saca uno por cada documento que se pide, `O(log C)`. Ordenar todo de una vez
cuesta `O(C log C)` aunque solo se consuman diez. Medido con 20 000 candidatos: sacar 10
cuesta 0,56 ms con el montículo y 2,7 ms ordenando; sacar 1 000, 0,81 ms frente a 2,7 ms;
solo al consumirlos **todos** gana ordenar (2,8 ms frente a 5,4 ms).

### Complejidad del ranking

Con `P` postings en las listas de la consulta, `C` documentos que las contienen y `q`
términos en la consulta:

| | Coste |
|---|---|
| analizar y buscar los términos | `O(q · S log V)` y una página del léxico por término y segmento |
| leer las listas | `⌈8·df / página⌉ + 1` páginas por término y segmento |
| puntuar | `O(P)` sumas en los acumuladores, más `O(C)` para normas y borrados |
| `top_k` | `O(C log k)` |
| `ranked` | `O(C)` al llamarse, y `O(log C)` por documento que se pide |
| memoria | los `C` acumuladores, más las columnas de 20 bytes por documento |

`S` es el número de segmentos: 1 tras una fusión.

### El ranking medido con ESCI

Medición manual sobre los índices de la sección anterior y las 150 consultas del
subconjunto (50 por cubeta de longitud): `top_k` con `k = 10`, media de 5 repeticiones por
consulta, con las columnas ya cargadas. Relevante = etiqueta E (exacto). Apple M4 Pro,
Python 3.12. El experimento completo, contra GIN y con Recall@10, es el de `benchmarks/`.

| Documentos | Cubeta | Postings leídos | Documentos puntuados | TF-IDF | BM25 |
|---:|---|---:|---:|---:|---:|
| 10 000 | 1 palabra | 176 | 157 | 0,08 ms | 0,06 ms |
| 10 000 | 3 palabras | 915 | 832 | 0,23 ms | 0,19 ms |
| 10 000 | 5+ palabras | 2 252 | 1 836 | 0,49 ms | 0,40 ms |
| 100 000 | 1 palabra | 1 620 | 1 464 | 0,35 ms | 0,32 ms |
| 100 000 | 3 palabras | 8 781 | 8 203 | 1,62 ms | 1,49 ms |
| 100 000 | 5+ palabras | 21 911 | 18 296 | 3,76 ms | 3,38 ms |

El tiempo crece con los postings leídos: unos 170 ns por posting con 100 000 documentos.

Precision@10, como comprobación de que el ranking ordena (no es el experimento):

| Documentos | Cubeta | TF-IDF | BM25 | Al azar |
|---:|---|---:|---:|---:|
| 10 000 | 1 / 3 / 5+ | 0,54 / 0,48 / 0,39 | 0,53 / 0,57 / 0,44 | |
| 10 000 | todas | **0,47** | **0,52** | 0,0013 |
| 100 000 | 1 / 3 / 5+ | 0,33 / 0,29 / 0,22 | 0,33 / 0,43 / 0,29 | |
| 100 000 | todas | **0,28** | **0,35** | 0,00013 |

«Al azar» es la precisión esperada de diez documentos cualesquiera: la fracción de
relevantes en el corpus. Con una palabra los dos rankings casi empatan; con tres o más,
BM25 gana por 5 a 14 puntos.

## Mantenimiento: segmentos y lápidas

Una tabla con índice invertido tiene que admitir `INSERT`, `UPDATE`, `DELETE` y `ROLLBACK`
como cualquier otra, sin reconstruir el índice en cada cambio. El diseño es el de Lucene:
lo nuevo va a **segmentos** pequeños que después se fusionan, y lo borrado deja una
**lápida** que la fusión limpia.

```
insert ──► bloque SPIMI en memoria ──flush──► seg5 ─┐
                                               seg4 ─┤ compactar (más de 7 pendientes)
                                               seg3 ─┘        │
seg0 (principal) ◄──────── fusión completa (lo nuevo o lo borrado pasa del 30 %) ◄──┘
delete ──► lápida en la tabla de documentos (la fusión completa quita sus postings)
```

### Insertar

El documento recibe el siguiente `doc_id`, se analiza y sus postings van a un
`SpimiBlock` en memoria, **la misma clase** que llena la construcción. Se apunta en la
tabla de documentos (una página) y en el mapa de direcciones (ver más abajo). Las
búsquedas lo ven al instante: el índice se lee como una lista de segmentos —los de disco y,
al final, el bloque en memoria— y la lista de un término es la concatenación de las suyas,
ya ordenada por el invariante 7. El ranking no sabe cuántos segmentos hay: recibe una sola
`PostingList` por término (`postings_of`).

Si el bloque llega a `spimi_block_bytes` a mitad de una sentencia, se escribe como un
segmento más, sin fusionar: la memoria queda acotada también al insertar.

### Borrar

La tabla avisa con la dirección de la fila. El mapa de direcciones da el `doc_id`, y el
documento queda marcado como borrado en la tabla de documentos: `O(1)` páginas. Sus
postings siguen en los segmentos; el ranking los descarta sobre los acumuladores.

- **N y la longitud media cambian al instante**: la cabecera de la tabla de documentos
  lleva los borrados y la suma de las longitudes de los vivos, y las columnas en memoria
  se actualizan en el momento.
- **El df sigue contando al documento borrado hasta la próxima fusión completa**, como en
  Lucene: saber qué términos tenía obligaría a volver a analizar su texto y a guardar un
  contador por término. Para que el idf de TF-IDF no se vuelva negativo cuando quedan más
  borrados que vivos, el ranking usa `min(df, N)`.

Con `cable rojo`, `cable azul usb` y `teclado`, borrar el segundo deja (son los números
del test):

| | antes de fusionar | después |
|---|---:|---:|
| N | 2 | 2 |
| suma de longitudes / `avgdl` | 3 / 1,5 | 3 / 1,5 |
| df de `cabl` | **2** | 1 |
| df de `azul` | **1** | 0 |
| vocabulario / postings | 5 / 6 | 3 / 3 |

Un `UPDATE` que cambia el texto es un borrado y una inserción, con `doc_id` nuevo. Uno que
no lo cambia no llega al índice: la tabla le pregunta antes con `changes(vieja, nueva)`,
que compara el valor lógico de la columna (ver [`index/README.md`](../README.md)).

### `doc_id` estables

Un `doc_id` nunca cambia ni se reutiliza. La fusión quita los postings de los borrados pero
**no renumera**: la lápida se queda en la tabla de documentos (18 bytes en disco y 20 en
las columnas en memoria por documento borrado). A cambio, ni el mapa de direcciones ni la
tabla de documentos se reescriben al fusionar, y el orden de los `doc_id` entre segmentos
se conserva solo. El orden del resultado no depende de los `doc_id`: a igual puntaje
decide la dirección de la fila (ver [Decisiones](#decisiones)).

### `flush` y las fusiones

`flush` escribe el bloque en memoria como un segmento nuevo y después mira dos umbrales
(`EngineConfig`):

| Cuándo | Qué hace | Coste |
|---|---|---|
| hay más de `inverted_max_segments` (7) segmentos pendientes | los **compacta** en uno, copiando las listas sin decodificarlas, como la mezcla de bloques | proporcional a lo pendiente |
| los pendientes suman el 30 % (`inverted_pending_fraction`) de los postings del principal, o los borrados con postings son el 30 % (`inverted_deleted_fraction`) de los documentos indexados | **fusión completa**: todos los segmentos en uno, sin los postings de los borrados | lee y escribe todos los postings |

Las dos son la mezcla k-vías de la construcción (`merge_sources`), con los segmentos como
fuentes y por pasadas si son más que `merge_fan_in`. La fusión completa pone delante del
segmento nuevo un `PurgingSink`: decodifica la lista de cada término, quita los `doc_id`
borrados, recuenta su df y su frecuencia total, y deja fuera el término que se queda sin
postings. La compactación no purga: así todos los borrados se limpian a la vez, y basta un
contador (los purgados, en el manifiesto) para saber cuántos siguen en las listas.

Dos niveles porque la fusión completa es cara (1,7 s con 100 000 documentos) y la
compactación barata: con `COMMIT` de una fila, cada `flush` deja un segmento de un
documento, y fusionarlo todo cada ocho confirmaciones costaría más que lo insertado. Con
el umbral del 30 % —el mismo que reorganiza el archivo secuencial de la Parte 1— la
fusión completa llega cuando el índice creció o se vació en casi un tercio, y su coste
repartido entre esos cambios es constante por posting.

**Tras una fusión completa, el índice es el de reconstruir de cero** con los documentos
vivos: mismo df, mismo vocabulario, mismos postings y el mismo ranking, puntaje a puntaje
y en el mismo orden (lo compara el test contra una reconstrucción y contra el oráculo).

**Nunca a mitad de una sentencia.** Solo `flush` fusiona, y el motor solo vuelca al
confirmar una transacción.

### El manifiesto

Los segmentos vivos los enumera `productos.inv.manifest` por su **generación**, un número
que nunca se reutiliza. No es un archivo paginado sino un descriptor de unos bytes, con la
misma cabecera de firma y versión (`FileHeader.encode` y `decode`). Se escribe aparte, se
lleva al disco y reemplaza al anterior de una vez (`Path.replace`): es el momento en que
un segmento nuevo o una fusión pasan a existir, como el `segments_N` de Lucene. Los
segmentos que una fusión reemplazó se borran después. Leerlo no necesita la configuración
del motor, y por eso `files_of(path)` puede enumerar los segmentos para `DROP INDEX`.

`flush` y `close` dejan en disco el bloque en memoria, las lápidas, los contadores y el
manifiesto. Sin ellos, como en el resto del motor, lo último puede perderse: el motor
vuelca al confirmar cada transacción (política *force*).

### De la dirección de la fila al `doc_id`

Para borrar hace falta el `doc_id` de una dirección. Se evaluaron dos opciones, medidas con
100 000 direcciones:

| | Diccionario en memoria | Hash extendible de la Parte 1 (`DocumentMap`) |
|---|---|---|
| memoria | 7,7 MiB (`tracemalloc`) | ninguna fuera del buffer pool |
| al abrir el índice | recorrer la tabla de documentos, `O(N)` | nada (abrir el índice entero: 1,2 ms) |
| al construir | — | 1,1 s de 21,7 s |
| buscar y quitar una dirección | `O(1)` | 0,1 ms, dos páginas |
| disco | — | 1,9 MB |

Se eligió el hash: ocupa en disco lo que el diccionario ocuparía en memoria en cada índice
abierto, y abrir no cuesta nada. El mapa guarda solo los documentos vivos: un borrado quita
la entrada, porque el heap reutiliza la ranura y la misma dirección puede ser después de
otra fila. Insertar una dirección que ya tiene documento es un
`DocumentAlreadyIndexedError`.

### Transacciones

El deshacer del motor vuelve a pasar por la tabla, y la tabla avisa a sus índices como
siempre:

| Cambio | Deshacerlo | En el índice |
|---|---|---|
| `INSERT` | se borra la fila | lápida del documento |
| `DELETE` | se vuelve a insertar | documento nuevo, con otro `doc_id` |
| `UPDATE` del texto | se restaura el valor anterior | lápida y documento nuevo |

Tras un `ROLLBACK`, una búsqueda encuentra los mismos documentos que antes. Los puntajes
difieren solo por el df, que cuenta las lápidas que dejó el deshacer; tras la fusión son
idénticos, y el orden también, porque los empates no dependen del `doc_id`. Es el test de
`test_text_search_index.py`, que pasa por el motor y las sesiones con un índice creado con
`CREATE INDEX … USING INVERTED`.

### El índice como índice secundario

`TextSearchIndex` (`secondary.py`) es un `ColumnIndex`: recibe de la tabla `build`,
`insert` y `delete` con la fila y su dirección y `changes` con las dos filas de un
`UPDATE`, deja fuera los NULL y declara sus archivos con `files_of` para `DROP INDEX`.

- `build` construye con SPIMI de una vez, no fila a fila, y solo sobre un índice vacío.
- El idioma del analizador (`language`, que en SQL será la opción `idioma`) se elige al
  crearlo; al abrir uno guardado tiene que coincidir con el suyo
  (`IndexLanguageMismatchError`).
- `search(valor)` es una igualdad, y un índice invertido no la responde:
  `TextSearchEqualityError`. Se consulta con un `Ranker` sobre `inverted_index`.
- El valor de la columna tiene que ser texto (`NotTextValueError`). Lo recibe del mismo
  `_value_of` que los demás índices, así que no depende de cómo guarde la tabla el texto.

En SQL es el método `INVERTED` de [`query/index_methods.py`](../../query/index_methods.py):
`CREATE INDEX ON productos USING INVERTED (texto) WITH (idioma='es')`, solo sobre columnas
`VARCHAR` o `TEXT` de un heap file. El registro le pasa al abrirlo las opciones de su
`WITH`, que el catálogo guarda, así que reabrir el motor lo abre en su idioma. `ranker()`
da el `Ranker` del índice, que es lo que usa el acceso `TextSearchScan` de un `MATCH` (ver
[`query/README.md`](../../query/README.md#búsqueda-de-texto)).

### El mantenimiento medido con ESCI

Medición manual, no un experimento versionado: un índice construido con las primeras
99 000 fichas del subconjunto de 100 000, al que se insertan una a una las 1 000 restantes
o se borran 1 000 al azar. Configuración por omisión (páginas de 4 KB), sin fusiones
automáticas salvo donde se dice. Apple M4 Pro, Python 3.12.

| Operación | Media | Mediana | p95 |
|---|---:|---:|---:|
| insertar un documento | 0,45 ms | 0,34 ms | 1,20 ms |
| borrar un documento | 0,11 ms | 0,09 ms | 0,15 ms |

| Operación | Tiempo |
|---|---:|
| `flush` tras insertar 1 000 (escribe un segmento) | 34 ms |
| `flush` tras borrar 1 000 | 4 ms |
| compactar 7 segmentos de unos 140 documentos | 0,11 s |
| fusión completa de 8 segmentos, 100 000 documentos, sin borrados | 0,99 s |
| fusión completa de 99 000 documentos con 1 000 borrados | 1,69 s |

Insertar cuesta sobre todo analizar el texto, más la búsqueda del mapa que comprueba que la
dirección no está repetida. La fusión con borrados es más lenta que la que solo copia
porque decodifica cada lista para quitar los `doc_id` borrados.

Las 150 consultas, `top_k` con `k = 10`, media de 5 repeticiones, con los mismos 100 000
documentos repartidos entre el principal y los 1 000 insertados:

| Los 1 000 nuevos están… | TF-IDF 1 / 3 / 5+ palabras | BM25 1 / 3 / 5+ palabras |
|---|---:|---:|
| fusionados (un segmento) | 0,32 / 1,62 / 3,78 ms | 0,31 / 1,54 / 3,71 ms |
| en el bloque en memoria | 0,33 / 1,66 / 3,96 ms | 0,31 / 1,52 / 3,67 ms |
| en 1 segmento pendiente | 0,35 / 1,72 / 3,98 ms | 0,34 / 1,65 / 4,12 ms |
| en 3 | 0,36 / 1,72 / 4,00 ms | 0,34 / 1,65 / 3,90 ms |
| en 5 | 0,37 / 1,77 / 4,02 ms | 0,37 / 1,69 / 4,01 ms |
| en 7 | 0,40 / 1,88 / 4,26 ms | 0,38 / 1,75 / 4,02 ms |

Cada segmento suma una búsqueda en su léxico por término de la consulta: con siete, una
consulta de una palabra tarda un 25 % más y una de cinco o más, entre un 8 y un 13 %. Es lo
que acota `inverted_max_segments`. Con un solo segmento, el ranking mide lo mismo que antes
del mantenimiento: sobre el mismo índice, con el código anterior y el nuevo alternados, las
diferencias quedan dentro de un 3 %.

### Complejidad del mantenimiento

Con `T` postings en el índice, `P` en los segmentos pendientes, `S` segmentos y abanico `k`:

| | Coste |
|---|---|
| insertar | analizar el texto, `O(1)` páginas de la tabla de documentos y del mapa |
| borrar | `O(1)` páginas |
| `flush` sin fusión | escribir el bloque en memoria |
| compactar | `O(P)`, `⌈log_k S⌉` pasadas |
| fusión completa | `O(T)`, `⌈log_k S⌉` pasadas; con borrados, decodifica cada lista |
| memoria | el bloque en memoria (`spimi_block_bytes`) y una página por flujo de cada segmento en la mezcla |

## Optimizaciones medidas que no entraron

**Postings con deltas y VByte.** Guardar cada `doc_id` como la diferencia con el anterior
y cada número en bytes de 7 bits reduciría los postings de las 100 000 fichas de ESCI de
60,2 MiB a **16,7 MiB**, 3,6 veces menos, lo que hace GIN. Pero decodificar VByte es un
bucle de Python por byte, y el ancho fijo es un solo `array.frombytes` en C. Medido sobre
las listas de las 150 consultas, decodificar cuesta por consulta 0,26 / 1,45 / 3,58 ms con
VByte frente a 0,001 / 0,003 / 0,006 ms con ancho fijo (1 / 3 / 5+ palabras): más que toda
la consulta de hoy. Se queda el ancho fijo; el espacio es la desventaja que el experimento
reporta frente a GIN.

**Leer el TEXT solo cuando se usa.** El valor de una columna TEXT se lee de su archivo al
leer la fila, aunque la consulta no lo use (medido al añadir las columnas TEXT: recorrer
10 000 filas con 1 KB de texto cuesta 81 ms, frente a 62 ms sin leerlo). Leerlo a demanda obligaría a que una fila lleve
direcciones sin resolver por el ejecutor, los índices y el registro de deshacer, y la
invariante de [`storage/out_of_row.py`](../../storage/out_of_row.py) es justamente la
contraria: por encima del serializador una fila es siempre la tupla lógica. Hacerlo
limpio es podar columnas en el planificador (que cada recorrido sepa qué columnas pide),
un cambio del ejecutor entero. No toca este experimento: el índice se construye y se
consulta sin leer la tabla.

**Un stemmer en C.** `snowballstemmer` usa PyStemmer si está instalado, y el stemmer es
lo que más cuesta del análisis que queda (265 769 llamadas con las 100 000 fichas). No se
probó: cambiaría una dependencia del proyecto y habría que comprobar que las dos
implementaciones dan las mismas raíces.

## Lo que todavía no hace

- **SQL.** `CREATE INDEX … USING INVERTED`, `MATCH` y `SCORE()` son la integración con el
  planificador, que registra `TextSearchIndex` en `query/index_methods.py`.
- **Recuperar las lápidas.** La tabla de documentos y las columnas en memoria solo crecen:
  un documento borrado conserva su `doc_id` (ver [`doc_id` estables](#doc_id-estables)).

## Tests

```bash
.venv/bin/python -m pytest tests/index/inverted -q
```

Cubren el índice vacío, un documento, un documento de solo stopwords, un término en todos
los documentos, una lista que cruza páginas, términos de 40 caracteres de cuatro bytes,
un término demasiado largo, términos con ñ, búsquedas antes, entre y después de los
términos de un léxico de muchas páginas, la reapertura, `describe()`, `files_of`, abrir
archivos de otro formato, de otra versión o de índices distintos, que la construcción
fallida no deja archivos ni bloques, y:

- **un bloque o muchos**: con bloques diminutos y abanico 2 (3 o más bloques, 2 o más
  pasadas), los archivos son idénticos byte a byte a los de un solo bloque;
- **contra un modelo**: corpus al azar con semillas fijas, bloques y abanicos al azar,
  comparados con un índice `término → {doc: tf}` construido directamente en memoria;
- **memoria acotada**: con `tracemalloc`, cuadruplicar los documentos no sube el pico de la
  construcción más de un 25 % (sin bloques acotados, lo triplica).

El ranking (`test_ranking.py`) se compara con un **oráculo de fuerza bruta**: las fórmulas
de este README aplicadas directo a los textos analizados, sin índice. En corpus al azar con
semillas fijas, consultas al azar (con términos repetidos, stopwords y palabras que no
están) y varios `k1` y `b`, los puntajes coinciden con tolerancia 1e-9 y el orden es el
mismo con la regla de empates. Cubren además la consulta vacía o de solo stopwords, el
término inexistente, el término repetido, `k = 0`, `k < 0` y `k > N`, un solo documento,
el término en todos los documentos, empates, documentos borrados, que el iterador
perezoso da el mismo orden que el top-k completo, las estadísticas, el idioma del índice,
los parámetros por omisión de la configuración y la validación de `k1` y `b`. El
oráculo vive en `inverted_support.py`, indexado por la dirección de la fila, para que lo
use también el mantenimiento. La poda MaxScore se compara además, bit a bit, con el orden
completo sin poda: en corpus al azar (y se comprueba que la poda llegó a activarse), en un
corpus hecho para que pode, con más empatados que `k` y con borrados que, si contaran en
el umbral, harían descartar documentos vivos.

El mantenimiento (`test_maintenance.py`) cubre la inserción que aparece antes y después del
`flush`, el borrado que deja de aparecer, borrar lo que no está, la dirección repetida, el
`UPDATE`, los números de N, `avgdl`, df y vocabulario antes y después de fusionar, la cota
`min(df, N)`, un índice sin vivos, la reapertura con segmentos pendientes y con un bloque
que nunca se volcó, el bloque que se escribe a mitad de las inserciones, la compactación,
los dos disparadores de la fusión completa, las pasadas con abanico 2, los segmentos que la
fusión borra, `describe()` y el léxico mezclado. Lo central:

- **la fusión es una reconstrucción**: tras fusionar, el índice da los mismos df, el mismo
  vocabulario, los mismos postings y el mismo ranking, puntaje a puntaje y en el mismo
  orden, que uno construido de cero con los documentos vivos en otro orden;
- **contra un modelo**: 260 operaciones al azar (inserciones que reutilizan direcciones,
  borrados, actualizaciones, `flush` y reaperturas) con tres semillas, bloques diminutos,
  abanico 2 y umbrales bajos, para que haya compactaciones y fusiones. Antes de cada
  fusión el ranking se compara con el oráculo que cuenta en el df los borrados cuyos
  postings siguen ahí; al final, con una reconstrucción.

`test_text_search_index.py` prueba el índice por la interfaz de `ColumnIndex` (NULL,
igualdad, idioma, `files_of`, reapertura) y, con el método registrado en el test como lo
hará el SQL, a través del motor: el `COMMIT` vuelca el bloque, un `UPDATE` de otra columna
no lo toca y un `ROLLBACK` de inserciones, borrados y actualizaciones deja los mismos
documentos, y tras la fusión los mismos puntajes.
