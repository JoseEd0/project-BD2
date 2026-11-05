# Multimedia: de imágenes y audio a vectores

Este paquete convierte un archivo multimedia en un vector de dimensión fija que un índice
puede comparar (§2.4.1 del enunciado). No conoce el SQL ni el API: recibe descriptores y
devuelve vectores.

```
archivo ─► descriptores locales ─► palabras (k-means) ─► histograma ─► TF-IDF ─► vector K
           SIFT / MFCC              vocabulary.py          conteos      weighting.py
```

## Vocabulario ([`vocabulary.py`](vocabulary.py))

Una imagen da cientos de descriptores SIFT y un audio uno MFCC por ventana: ninguno tiene
tamaño fijo. k-means (`faiss.Kmeans`) agrupa una muestra de descriptores de la colección
en K centroides, las **palabras**, y cada objeto pasa a ser cuántos de sus descriptores
caen en cada palabra.

- **La muestra** sale de los descriptores de `vocabulary_training_objects` objetos (2 000)
  con muestreo de reservorio (`training_sample`), sin tenerlos todos en memoria, y se queda
  en `vocabulary_size × kmeans_samples_per_word` descriptores (256 × 256 = 65 536), lo
  más que FAISS usa para entrenar aunque reciba más.
- **La palabra de cada descriptor** es su centroide más cercano: una búsqueda exacta sobre
  `IndexFlatL2` en un solo lote por objeto.
- Con menos descriptores que palabras no hay vocabulario: `NotEnoughDescriptorsError`.

| Parámetro (`EngineConfig`) | Por omisión | Qué es |
|---|---|---|
| `vocabulary_size` | 256 | palabras: la dimensión de los vectores que indexan IVF y HNSW |
| `kmeans_iterations` | 25 | iteraciones de k-means (el valor de FAISS) |
| `kmeans_seed` | 1234 | semilla de k-means y del muestreo (la de FAISS) |
| `kmeans_samples_per_word` | 256 | descriptores por palabra para entrenar, como mucho (el de FAISS) |
| `vocabulary_training_objects` | 2 000 | objetos de los que sale la muestra |

## TF-IDF ([`weighting.py`](weighting.py))

El peso de Sivic y Zisserman («Video Google», ICCV 2003):

```
t_i = (n_id / n_d) · log(N / n_i)
```

`n_id` son los descriptores del objeto en la palabra i, `n_d` todos los del objeto, `N` los
objetos y `n_i` en cuántos aparece la palabra. La frecuencia relativa quita el efecto de
que una imagen grande dé más descriptores; el idf apaga las palabras que salen en casi
todo. Una palabra que está en todos los objetos pesa 0, y un objeto sin descriptores es el
vector cero.

Los vectores se guardan **sin normalizar**. Si se normalizaran, la distancia euclidiana y
el coseno ordenarían igual y la comparación del experimento no diría nada; el coseno ya
normaliza dentro del índice.

## Almacén de características ([`feature_store.py`](feature_store.py))

El primer índice vectorial de una columna lo construye y los demás lo reutilizan: IVF y
HNSW sobre la misma columna se comparan sobre los mismos vectores, y un índice dañado se
reconstruye desde aquí sin volver a extraer nada.

| Archivo | Estructura | Contenido |
|---|---|---|
| `<base>.words` | heap file (Parte 1) | un registro `(fila, descriptores, conteos VECTOR(K))` por fila |
| `<base>.words.map` | hash extendible (Parte 1) | dirección de la fila → dirección de su registro |
| `<base>.model` | `.npz` escrito de una vez | centroides, idf y objetos al construir |

- **Se guardan los conteos, no el TF-IDF.** El idf solo se conoce al terminar de recorrer
  la colección. Con los conteos guardados, el vector de cada fila se calcula al leerlo, sin
  una segunda pasada que reescriba el archivo.
- **El idf se fija al construir.** Una fila que llega después se cuantiza con el vocabulario
  y se pesa con el idf de entonces; reconstruir los recalcula.
- **Borrar** libera la ranura del heap y la entrada del hash.
- `vectorize(descriptores)` da el vector de una consulta que no está en la tabla.

Con 10 000 objetos de 300 descriptores de 128 dimensiones y 256 palabras, generados en
flujo:

| Etapa | Tiempo |
|---|---:|
| muestrear 65 536 descriptores de 2 000 objetos | 0.5 s |
| k-means | 0.4 s |
| cuantizar los 10 000 objetos | 5.6 s |
| leer los 10 000 vectores | 0.16 s |

El almacén ocupa 13.3 MiB, y el proceso no pasó de 142 MiB de memoria.
