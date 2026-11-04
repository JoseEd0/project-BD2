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
| `files_of(path)` | los cuatro archivos, para `DROP INDEX` |
| `lookup(term) -> LexiconEntry \| None` | término, df, frecuencia total y posición de su lista |
| `postings(term)`, `postings_of(entry) -> PostingList` | la lista, vacía si el término no está |
| `document_frequency(term)` | df, 0 si no está |
| `document(doc_id) -> DocumentEntry` | `record_id`, `length`, `norm`, `deleted`; `DocumentNotFoundError` |
| `documents()`, `terms()` | recorridos completos en orden de `doc_id` y de término |
| `document_count`, `total_length`, `average_length` | N, suma de longitudes y la `avgdl` de BM25 |
| `vocabulary_size`, `posting_count`, `block_count`, `merge_passes`, `language` | forma del índice |
| `document_columns() -> DocumentColumns` | longitudes y normas por `doc_id` en memoria, y los borrados; se carga la primera vez |
| `describe(top_terms=10)` | todo lo anterior, tamaños y los términos con más df |
| `flush()`, `close()`, `with` | como el resto de estructuras de disco |

`weights.py` tiene el peso `1 + log₁₀ tf` y la norma lnc. El ranking (TF-IDF y BM25) usa
esas mismas funciones para la consulta, así que los dos lados del coseno salen de una sola
fórmula.

## Los cuatro archivos

Una ruta base `productos.inv` da cuatro archivos con sufijo. La página 0 de cada uno es su
cabecera, con firma y versión (`FileHeader`): abrir un archivo de otra estructura o de
otro formato da `InvertedFormatError` en vez de leer basura.

```
productos.inv.lexicon     IVLX  │ cabecera │ registros del léxico (fijos, ordenados) │ fronteras │
productos.inv.terms       IVTM  │ cabecera │ cabl teclad usb … (términos seguidos, sin relleno) │
productos.inv.postings    IVPS  │ cabecera │ lista de cabl │ lista de teclad │ lista de usb │ … │
productos.inv.documents   IVDC  │ cabecera │ doc 0 │ doc 1 │ doc 2 │ … (registros fijos) │
```

| Archivo | Registro | Bytes | Por página de 4 KB |
|---|---|---:|---:|
| léxico | inicio del término (Q), su longitud (H), df (I), frecuencia total (Q), inicio de la lista (Q) | 30 | 131 |
| términos | el término en UTF-8 | variable | — |
| postings | `doc_id` (I), `tf` (I), little-endian | 8 | 512 |
| documentos | página y ranura de la fila (I H), longitud (I), norma lnc (d) | 18 | 215 |

Las cabeceras guardan:

- **léxico**: idioma del analizador, número de términos, bytes de términos, número de
  postings, bytes de las fronteras, bloques y pasadas de mezcla de la construcción;
- **términos**: sus bytes, que tienen que coincidir con los del léxico;
- **postings**: su número, que tiene que coincidir con el del léxico;
- **documentos**: N y la suma de las longitudes.

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
   en el orden en que llegaron. Llegar a uno es una división.
5. **La marca de borrado es el estado de la ranura** (`SlotState.DELETED`), la lápida de
   `RecordPage`: no cuesta un byte más. Hoy siempre es falsa; la usará el mantenimiento.
6. Las **fronteras** son el primer término de cada página del léxico, en orden.

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
nuevos y el ranking del coseno no tiene que recalcularla.

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

`merge_blocks` mezcla k bloques con el mismo montículo que el ordenamiento externo
(`merge_sorted_streams`, en `external/runs.py`): sale el término menor de entre las
cabeceras pendientes y, a igual término, el del bloque que va primero. Los miembros de un
mismo término se juntan: su df y su frecuencia total se suman, y sus listas se copian una
tras otra, **sin decodificarlas**.

La copia sin comparar es correcta porque **cada bloque tiene `doc_id` mayores que los del
anterior**: concatenar sus listas en el orden de los bloques da una lista ordenada.

Si hay más bloques que `merge_fan_in`, se mezclan por pasadas, como en el ordenamiento
externo: cada grupo de `k` bloques **consecutivos** pasa a ser un bloque, lo que conserva
el orden de los `doc_id` entre bloques. La última pasada escribe el índice definitivo. Si
todo cupo en un bloque, va directo de memoria al índice, sin pasar por disco.

Los bloques viven en `productos.inv.spimi/`, que se borra siempre, también si la
construcción falla; y si falla, tampoco quedan los archivos del índice.

### 5. Un bloque o cien: el mismo índice

El índice que sale es **idéntico byte a byte** se use un bloque o muchos (salvo los dos
contadores de bloques y pasadas de la cabecera): el léxico, los términos, las listas y la
tabla de documentos. Es el test central del módulo, y también se comprobó con las 10 000
fichas de ESCI: con un bloque de 512 KB y abanico 2 salen **111 bloques y 7 pasadas**, y los
cuatro archivos coinciden con los del índice de un solo bloque.

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
| tiempo de construcción | 2,6 s | 24,2 s |
| pico de `tracemalloc` | 20,6 MiB | 26,7 MiB |
| memoria residente máxima | 56 MiB | 69 MiB |
| léxico + términos | 1,3 + 0,3 MB | 4,7 + 1,0 MB |
| postings | 6,3 MB | 63,2 MB |
| documentos | 0,2 MB | 1,9 MB |

Diez veces más documentos cuestan un **30 % más de memoria**, no diez veces más: es el
bloque acotado. Del tiempo, unos 17 s de los 24 son el análisis del texto (plegado, tokens
y raíces), medido aparte sobre los mismos 100 000 documentos; los bloques y la mezcla
suman los 7 s restantes.

Con 100 000 documentos el tamaño del bloque no cambia el tiempo, solo la memoria:

| `spimi_block_bytes` | bloques | pasadas | tiempo | memoria residente |
|---:|---:|---:|---:|---:|
| 8 MiB | 20 | 2 | 24,1 s | 56 MiB |
| 16 MiB | 9 | 2 | 24,2 s | 69 MiB |
| 32 MiB | 4 | 1 | 25,7 s | 82 MiB |

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
| `ScoredDocument` | `doc_id`, `record_id` de la fila y `score` |
| `RankingMethod` | `TF_IDF` o `BM25` |
| `Bm25Parameters(k1, b)` | parámetros de una consulta; sin ellos, `EngineConfig.bm25_k1` y `bm25_b` |
| `RankingStats` | para `EXPLAIN`: cada término analizado con su df, postings leídos y documentos puntuados |
| `RankingParameterError` | `k` negativo, `k1` o `b` fuera de rango, o parámetros de BM25 para TF-IDF |

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
| empate de puntaje | primero el de menor `doc_id` | el resultado es determinista y coincide con el orden de carga |
| término en todos los documentos | TF-IDF le da idf 0: los documentos que solo tienen ese término **entran con puntaje 0** | el conjunto de resultados es el mismo con los dos rankings (los documentos con algún término), que es lo que comparan el experimento y `MATCH` sin `LIMIT` |
| todos los términos en todos los documentos | la consulta es el vector nulo: coseno 0 para todos | el coseno con un vector nulo no está definido; 0 es el valor que no ordena nada |
| `k = 0` | lista vacía | es `LIMIT 0` |
| `k < 0` | `RankingParameterError` | no hay `LIMIT` negativo |
| `k > N` | todos los que coinciden | |
| documento borrado | no sale ni cuenta en `documents_scored` | se filtra al final, sobre los acumuladores; N, `avgdl` y df son los que da el índice, y el mantenimiento decide cuándo dejan de contarlo |

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

**Las longitudes y normas en memoria.** BM25 necesita el `dl` de cada posting y el coseno
la norma de cada documento puntuado, que con una consulta de cinco palabras pueden ser
18 000 de 100 000. Leerlas de la tabla de documentos costaría una página por documento.
`document_columns()` las carga una vez por índice abierto en dos `array` (`I` y `d`), 12
bytes por documento, más el conjunto de los borrados: son las *norms* que Lucene tiene en
memoria por la misma razón. Con 100 000 documentos ocupan 1,2 MB y se cargan en 93 ms. El
mantenimiento, que marcará borrados y añadirá documentos, las actualiza en la misma tabla.

**Top-k.** `heapq.nsmallest(k, (-puntaje, doc_id))`, un montículo de tamaño `k`:
`O(C log k)` para `C` candidatos. La dirección de la fila solo se lee para los `k` que se
devuelven.

**El iterador perezoso** (`ranked`) es para una consulta con filtros (`AND precio < 100`)
que no sabe cuántos documentos va a consumir. Forma un montículo con todos los candidatos,
`O(C)`, y saca uno por cada documento que se pide, `O(log C)`. Ordenar todo de una vez
cuesta `O(C log C)` aunque solo se consuman diez. Medido con 20 000 candidatos: sacar 10
cuesta 0,56 ms con el montículo y 2,7 ms ordenando; sacar 1 000, 0,81 ms frente a 2,7 ms;
solo al consumirlos **todos** gana ordenar (2,8 ms frente a 5,4 ms), y ese caso ya paga
una página por documento devuelto.

### Complejidad del ranking

Con `P` postings en las listas de la consulta, `C` documentos que las contienen y `q`
términos en la consulta:

| | Coste |
|---|---|
| analizar y buscar los términos | `O(q log V)` y una página del léxico por término |
| leer las listas | `⌈8·df / página⌉ + 1` páginas por término |
| puntuar | `O(P)` sumas en los acumuladores, más `O(C)` para normas y borrados |
| `top_k` | `O(C log k)` y una página por documento devuelto |
| `ranked` | `O(C)` al llamarse, y `O(log C)` más una página por documento que se pide |
| memoria | los `C` acumuladores, más las columnas de 12 bytes por documento |

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

## Lo que todavía no hace

El índice se construye de una vez y luego solo se lee: insertar, borrar y deshacer después
de construir es el mantenimiento por segmentos, que se apoya en este mismo formato (la marca
de borrado ya está reservada) y en la misma mezcla.

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
los parámetros por omisión de la configuración y la validación de `k1` y `b`.
