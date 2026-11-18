# Índice de similitud: `SIMILAR_TO` sobre imágenes, audios y vectores

Es lo que crea `CREATE INDEX ... USING HNSW (columna)` o `USING IVF (columna)`: un índice
secundario que responde `SIMILAR_TO`. Hay dos, según lo que guarde la columna:

| Clase | Columna | De dónde salen los vectores | Consulta |
|---|---|---|---|
| `MediaSimilarityIndex` ([`media.py`](media.py)) | `VARCHAR`/`TEXT` con la ruta de una imagen o un audio | SIFT o MFCC → vocabulario → TF-IDF, en un almacén de características | otro archivo |
| `VectorColumnIndex` ([`column.py`](column.py)) | `VECTOR(n)` | la propia columna: embeddings calculados fuera del gestor | un vector |

Los dos heredan de `SimilarityIndex` ([`base.py`](base.py)), que busca, y comparten
`IndexedVectors` ([`vectors.py`](vectors.py)): el índice de vectores (`HNSWIndex` o
`IVFIndex`, sobre FAISS) mantenido al día con su fuente de verdad. Ahí vive lo que no
depende de dónde salen los vectores: los vectores cero fuera del índice, el archivo que se
escribe al cerrar y no en cada `COMMIT`, y la búsqueda exacta cuando la consulta pide otra
métrica.

## Columnas de rutas

Junta las piezas de la Parte 4:

```
ruta ─► ExtractFeatures (SIFT / MFCC) ─► vocabulario + TF-IDF ─► vector K ─► HNSW o IVF
        multimedia/extraction.py          multimedia/feature_store.py      index/hnsw, index/ivf
```

### Construir

`build` recibe todas las filas de la tabla y:

1. resuelve cada ruta dentro de los directorios permitidos (el mismo control que
   `CREATE TABLE ... FROM FILE`) y exige que la columna sea toda de imágenes o toda de
   audios;
2. extrae los descriptores de una muestra de filas (`vocabulary_training_objects`, al azar
   con la semilla de k-means) para entrenar el vocabulario;
3. extrae los de todas, las cuantiza y fija el idf en el almacén de características;
4. llena el índice de vectores con los vectores TF-IDF y lo guarda.

La extracción va en paralelo (`feature_workers` procesos) cuando hay al menos un lote por
proceso; si no, en el propio proceso. Los descriptores de la muestra se guardan en un
archivo temporal ([`spill.py`](spill.py)) y el paso 3 los lee de ahí en vez de volver a
decodificar: con una tabla de hasta 2 000 filas, la muestra es la tabla entera. El orden de
las filas no cambia, así que el almacén y el índice salen idénticos a los de antes.

| `CREATE INDEX` (dos corridas alternadas, la misma máquina) | Decodificando dos veces | Con la muestra guardada |
|---|---:|---:|
| 1 000 fotos de ABO, HNSW | 2,68 s y 2,85 s | 2,03 s y 1,91 s |
| 2 000 pistas de FMA de 30 s, IVF | 22,81 s y 23,19 s | 13,12 s y 12,95 s |

El archivo temporal crece con la muestra, no con la tabla: unos 160 MB con 2 000 fotos y
unos 900 MB con 2 000 pistas de 30 s, y se borra al terminar. Un archivo que no se puede leer detiene la
construcción con su error, y como `CREATE INDEX` es todo o nada, no queda nada a medias.
Una tabla vacía no tiene con qué entrenar el vocabulario: el índice se crea sobre una
tabla con filas.

| Opción del `WITH` | Método | Por omisión |
|---|---|---|
| `metric` | los dos | `cosine` |
| `m`, `ef_construction`, `ef_search` | HNSW | 16, 64 y 40 (`EngineConfig`) |
| `lists`, `probes` | IVF | `√N` y `√listas` |
| `vocabulary` | los dos, solo sobre rutas | `vocabulary_size` (256) |

### Mantener

- `INSERT`: extrae el archivo de la fila nueva, lo cuantiza con el vocabulario y el idf
  fijados, y lo añade al almacén y al índice.
- `DELETE`: lo quita de los dos (HNSW deja una lápida).
- `UPDATE` de la ruta: borrar y volver a insertar.
- `ROLLBACK`: pasa por los mismos caminos, como en el resto de los índices.

### Archivos

| Archivo | Qué es |
|---|---|
| `<tabla>.<índice>.idx` | el índice de vectores de FAISS (`VectorIndex.save`) |
| `<tabla>.<índice>.idx.features.*` | el almacén de características: modelo, heap y hash |

**El índice de vectores es un derivado.** Con 100 000 vectores pesa más de cien megabytes, y
reescribirlo en cada `COMMIT` haría que un `INSERT` de una fila costara eso. Por eso el
`flush` del `COMMIT` lleva al disco las páginas del almacén —la fuente de verdad— y borra el
archivo del índice si quedó viejo; `close` lo vuelve a escribir. Si el proceso termina sin
cerrar, al abrir no hay archivo y el índice se reconstruye desde el almacén. Lo mismo si el
archivo está dañado.

Cada índice tiene su propio almacén. Dos índices sobre la misma columna (un HNSW y un IVF)
extraen dos veces, pero la extracción, la muestra y k-means son deterministas, así que sus
vectores son idénticos y la comparación entre los dos es justa; y `DROP INDEX` borra lo suyo
sin mirar a los demás.

### Buscar

`similar(archivo, k, metric, allowed, effort)` extrae y cuantiza el archivo de la consulta
(unos 5 ms con una foto de ABO) y busca:

- con la métrica del índice, en el índice;
- con otra, comparando contra todas las filas con una búsqueda exacta que se arma una vez y
  se descarta cuando la tabla cambia, porque el índice se construyó para ordenar con la suya;
- `allowed` son las filas que deja el resto del `WHERE`: la búsqueda solo puede devolver
  esas (ver [`index/vector/README.md`](../vector/README.md#la-búsqueda)).

Una consulta de otro tipo que la columna (un audio contra fotos) es un `MixedMediaError`.

## Columnas `VECTOR(n)`

Es lo que pgvector agrega a PostgreSQL, para los servicios que guardan embeddings de un
modelo que corre fuera del gestor (el RAG con multilingual-e5-small, de 384 dimensiones):

```sql
CREATE TABLE fragmentos (id INT PRIMARY KEY, tema VARCHAR(12), embedding VECTOR(384));
CREATE INDEX idx_emb ON fragmentos USING HNSW (embedding) WITH (metric = 'cosine', m = 16);
SELECT id, SIMILARITY_SCORE() FROM fragmentos WHERE SIMILAR_TO(embedding, $1, k = 10);
```

**Los vectores de la tabla son la fuente de verdad.** No hay vocabulario ni extracción:
`build` lee la columna de todas las filas por lotes de 4 096 y llena el índice de vectores
de una vez —un IVF entrena sus listas con todos—. Las opciones son las de la tabla de
arriba salvo `vocabulary`, que se rechaza.

- **Mantener.** La tabla avisa de cada fila como a cualquier índice secundario: `INSERT`
  añade el vector, `DELETE` lo quita (HNSW deja una lápida), `UPDATE` de la columna es
  borrar y volver a insertar, y `ROLLBACK` pasa por los mismos caminos. Al llegar las
  lápidas o el cambio de tamaño al 30 %, el `flush` del `COMMIT` reconstruye el índice.
- **Dimensión.** La valida la columna al insertar (`InvalidValueError`: «se esperaba un
  vector de dimensión 384 y llegó uno de dimensión 3»), y la consulta, el planificador,
  antes de leer ninguna fila.
- **NULL y vectores cero.** Las filas con `NULL` no entran, como en todo índice secundario.
  Las de vector cero tampoco, igual que en las columnas de rutas (#64): con el coseno no
  tienen dirección, y muchas en un mismo punto degradan el grafo. Salen con su puntaje
  exacto, que es el mismo para todas.
- **Otra métrica** es una búsqueda exacta contra los vectores de la tabla, que se arma la
  primera vez leyendo la tabla y se descarta cuando cambia.

**Archivos.** Solo `<tabla>.<índice>.idx` y la lista de vectores cero: el índice es un
derivado de la tabla. Se escribe al cerrar, no en cada `COMMIT` (con 100 000 vectores de
384 dimensiones pesa ~150 MB). Si el proceso termina sin cerrar, al abrir la tabla falta el
archivo, `needs_rows` lo dice (`RebuildableIndex` en [`index/secondary.py`](../secondary.py))
y la tabla lo reconstruye con `build` desde sus filas.

Las medidas con 100 000 vectores de 384 dimensiones están en
[`benchmarks/README.md`](../../../benchmarks/README.md#6-columnas-vectorn--hnsw-e-ivf-por-sql).

## Tests

- [`tests/index/similarity/test_similarity_index.py`](../../../tests/index/similarity/test_similarity_index.py)
  y [`tests/query/test_similarity_search.py`](../../../tests/query/test_similarity_search.py):
  las columnas de rutas.
- [`tests/index/similarity/test_vector_column_index.py`](../../../tests/index/similarity/test_vector_column_index.py):
  las columnas `VECTOR(n)`, contra la búsqueda por fuerza bruta con numpy: tabla vacía y de
  un vector, las tres métricas con HNSW e IVF, dimensión equivocada, vector cero y NULL,
  filtro con menos de k filas y filtro dentro de la búsqueda, `UPDATE`, `DELETE`,
  `ROLLBACK`, reconstrucción al 30 %, reabrir y perder el archivo.
