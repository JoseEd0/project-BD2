# Índice de similitud: `SIMILAR_TO` sobre imágenes y audios

Es lo que crea `CREATE INDEX ... USING HNSW (ruta)` o `USING IVF (ruta)`: un índice
secundario sobre la columna que guarda la ruta de cada imagen o audio. Junta las piezas de
la Parte 4:

```
ruta ─► ExtractFeatures (SIFT / MFCC) ─► vocabulario + TF-IDF ─► vector K ─► HNSW o IVF
        multimedia/extraction.py          multimedia/feature_store.py      index/hnsw, index/ivf
```

## Construir

`build` recibe todas las filas de la tabla y:

1. resuelve cada ruta dentro de los directorios permitidos (el mismo control que
   `CREATE TABLE ... FROM FILE`) y exige que la columna sea toda de imágenes o toda de
   audios;
2. extrae los descriptores de una muestra de filas (`vocabulary_training_objects`, al azar
   con la semilla de k-means) para entrenar el vocabulario;
3. extrae los de todas, las cuantiza y fija el idf en el almacén de características;
4. llena el índice de vectores con los vectores TF-IDF y lo guarda.

La extracción va en paralelo (`feature_workers` procesos) cuando hay al menos un lote por
proceso; si no, en el propio proceso. Un archivo que no se puede leer detiene la
construcción con su error, y como `CREATE INDEX` es todo o nada, no queda nada a medias.
Una tabla vacía no tiene con qué entrenar el vocabulario: el índice se crea sobre una
tabla con filas.

| Opción del `WITH` | Método | Por omisión |
|---|---|---|
| `metric` | los dos | `cosine` |
| `m`, `ef_construction`, `ef_search` | HNSW | 16, 64 y 40 (`EngineConfig`) |
| `lists`, `probes` | IVF | `√N` y `√listas` |
| `vocabulary` | los dos | `vocabulary_size` (256) |

## Mantener

- `INSERT`: extrae el archivo de la fila nueva, lo cuantiza con el vocabulario y el idf
  fijados, y lo añade al almacén y al índice.
- `DELETE`: lo quita de los dos (HNSW deja una lápida).
- `UPDATE` de la ruta: borrar y volver a insertar.
- `ROLLBACK`: pasa por los mismos caminos, como en el resto de los índices.

## Archivos

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

## Buscar

`similar(archivo, k, metric, allowed, effort)` extrae y cuantiza el archivo de la consulta
(unos 5 ms con una foto de ABO) y busca:

- con la métrica del índice, en el índice;
- con otra, comparando contra todas las filas con una búsqueda exacta que se arma una vez y
  se descarta cuando la tabla cambia, porque el índice se construyó para ordenar con la suya;
- `allowed` son las filas que deja el resto del `WHERE`: la búsqueda solo puede devolver
  esas (ver [`index/vector/README.md`](../vector/README.md#la-búsqueda)).

Una consulta de otro tipo que la columna (un audio contra fotos) es un `MixedMediaError`.
