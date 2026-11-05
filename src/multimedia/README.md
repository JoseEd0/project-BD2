# Multimedia: de imágenes y audio a vectores

Este paquete convierte un archivo multimedia en un vector de dimensión fija que un índice
puede comparar (§2.4.1 del enunciado). No conoce el SQL ni el API: recibe rutas o
descriptores y devuelve vectores.

```
archivo ─► descriptores locales ─► palabras (k-means) ─► histograma ─► TF-IDF ─► vector K
           SIFT / MFCC              vocabulary.py          conteos      weighting.py
```

## Extracción: `ExtractFeatures`

La primera etapa de la Parte 4 (§2.4.1 del enunciado): `ExtractFeatures(objeto)`, que
convierte una imagen o un audio en la matriz de sus **descriptores locales**. Las etapas
siguientes —el vocabulario con k-means, el histograma de palabras visuales o auditivas y su
TF-IDF— reciben esa matriz y no saben de qué archivo salió.

```
archivo ──► tipo por extensión ──► SIFT (imagen) | MFCC (audio) ──► matriz (n, d) float32
                                                                        │
                                       cuantización, histograma y TF-IDF (siguiente etapa)
```

No conoce el SQL, el planificador ni el API: depende solo de `config` y `errors`. OpenCV,
librosa y libsndfile se usan detrás de funciones tipadas de `image.py` y `audio.py`, así
que el resto del motor no ve su API.

### Uso

```python
from pathlib import Path

from config import EngineConfig
from multimedia import extract_features, extract_in_parallel, ExtractedFeatures

config = EngineConfig()
extract_features(Path("bolso.jpg"), config).shape     # (n, 128), n ≤ 500
extract_features(Path("yes.wav"), config).shape       # (101, 39) para un clip de 1 s

for outcome in extract_in_parallel(paths, config, workers=8):
    if isinstance(outcome, ExtractedFeatures):
        consume(outcome.path, outcome.descriptors)
    else:
        report(outcome.path, outcome.error)
```

| API | Qué da |
|---|---|
| `extract_features(path, config) -> Descriptors` | la matriz `(n, d)` en `float32` de un archivo; `n` puede ser 0 |
| `descriptor_dimension(kind, config) -> int` | la `d` de un tipo de archivo, antes de extraer nada |
| `media_kind_of(path) -> MediaKind` | `IMAGE` o `AUDIO` según la extensión, sin leer el archivo |
| `extract_each(paths, config)` | un `ExtractedFeatures` o un `FailedExtraction` por archivo, en este proceso |
| `extract_in_parallel(paths, config, workers)` | lo mismo, en el mismo orden, repartido en procesos |

`Descriptors` es `numpy.typing.NDArray[np.float32]`, siempre contigua en C: una fila por
punto clave o por ventana de audio.

### Errores

Todos derivan de `MediaError`, que deriva de `DomainError`: son culpa del archivo, no del
motor.

| Error | Cuándo |
|---|---|
| `UnsupportedMediaTypeError` | la extensión no está en `IMAGE_EXTENSIONS` ni en `AUDIO_EXTENSIONS` |
| `UnreadableMediaError` | el archivo falta, no se puede leer o no se puede decodificar |

Que de un archivo no salga ningún descriptor **no es un error**: una foto lisa o un audio
vacío dan una matriz `(0, d)`, y la etapa siguiente les asigna un histograma de ceros.

### Imagen: SIFT (`image.py`)

1. Se leen los bytes y OpenCV los decodifica en **escala de grises**: SIFT trabaja solo
   con la intensidad.
2. Si el lado mayor pasa de `image_max_side`, la imagen se **reduce** con `INTER_AREA`
   conservando la proporción. Nunca se agranda.
3. `cv2.SIFT` detecta los puntos clave y calcula su descriptor de **128 dimensiones**: una
   rejilla de 4 × 4 celdas con un histograma de 8 orientaciones en cada una.
4. Los puntos se ordenan por **respuesta** decreciente —con la posición, la escala y el
   ángulo como desempate— y se conservan los `image_max_descriptors` primeros.

El orden explícito del paso 4 hace la extracción determinista: OpenCV reparte la detección
entre hilos y puede devolver los mismos puntos en otro orden según cuántos use. Con el
orden fijo, la misma imagen da la misma matriz, bit a bit, en el proceso principal (con
todos los hilos) y en un proceso de la extracción en paralelo (con uno).

### Audio: MFCC (`audio.py`)

1. libsndfile abre el archivo (WAV, FLAC, OGG y MP3). Abrirlo antes de dárselo a librosa
   hace que un archivo dañado dé siempre el error de libsndfile, en vez de pasar al lector
   de respaldo de librosa, que cambia entre versiones.
2. librosa lee el **segmento** que empieza en `audio_offset_seconds` y dura
   `audio_duration_seconds` (`None`, hasta el final), lo **mezcla a mono** y lo
   **remuestrea** a `audio_sample_rate`. Un inicio después del final da un segmento vacío.
3. `librosa.feature.mfcc` calcula `mfcc_coefficients` coeficientes por ventana de Hann de
   `mfcc_window_length` muestras, con una FFT de `mfcc_fft_length` y un salto de
   `mfcc_hop_length`. Las ventanas van centradas, así que un segmento de `s` muestras da
   `1 + s // mfcc_hop_length` ventanas.
4. A cada coeficiente se le añaden su **primera y segunda derivada** (filtro de
   Savitzky-Golay de `mfcc_delta_width` ventanas). Cada fila del resultado es una ventana:
   `[c₁…c₁₃, Δc₁…Δc₁₃, Δ²c₁…Δ²c₁₃]`, **39 dimensiones** con los valores por omisión.

Las derivadas son las que distinguen un sonido que sube de uno que baja con el mismo timbre
medio; sin ellas, una palabra y la misma palabra al revés darían casi la misma bolsa de
palabras auditivas. El borde de la derivada repite la ventana extrema (`nearest`), porque
el modo por omisión de librosa falla con un audio de menos ventanas que el ancho del
filtro, unos 90 ms.

### Valores por omisión

Viven en `EngineConfig` (`src/config.py`), como el resto de los parámetros que un
experimento puede querer variar. Se midieron sobre 200 fotos reales de ABO (`images/small`,
JPEG de 256 px de lado mayor) en un Apple M4 Pro:

| Parámetro | Valor | Por qué |
|---|---:|---|
| `image_max_side` | 512 | las fotos de ABO pasan sin tocarse; una de 2048 px baja de 128 ms a 26 ms |
| `image_max_descriptors` | 500 | mediana de 137 descriptores, percentil 95 de 406, máximo de 601: solo se recorta el 2 % de las fotos |
| `audio_sample_rate` | 16 000 Hz | la frecuencia de Speech Commands; una pista de FMA solo pierde la octava de 8 a 11 kHz |
| `mfcc_coefficients` | 13 | el descriptor clásico del reconocimiento de voz |
| `mfcc_window_length` / `mfcc_fft_length` | 400 / 512 | ventana de 25 ms y la FFT en la potencia de dos siguiente |
| `mfcc_hop_length` | 160 | 10 ms: 101 ventanas por clip de Speech Commands y 3 001 por pista de FMA |
| `mfcc_delta_width` | 9 | el de librosa: cuatro ventanas a cada lado |
| `feature_chunk_files` | 16 | ver la extracción en paralelo |

Tres de las 200 fotos no dan ningún descriptor. Con estos valores, una
pista de 30 s ocupa 3 001 × 39 × 4 B ≈ 457 KiB de descriptores y una foto de ABO, en
promedio, 158 × 128 × 4 B ≈ 79 KiB.

### Extracción en paralelo (`parallel.py`)

`extract_in_parallel` reparte los archivos en un `ProcessPoolExecutor` de `workers`
procesos:

- **En flujo.** Toma los archivos de a lotes de `feature_chunk_files` y nunca tiene más de
  `workers × 2` lotes pedidos y sin entregar (`IN_FLIGHT_CHUNKS_PER_WORKER`): con dos por
  proceso, cada uno tiene el siguiente esperando mientras el anterior vuelve. La memoria no
  crece con el número de archivos, así que 100 000 imágenes no están nunca a la vez en
  memoria. Si quien itera se detiene, los lotes que no empezaron se cancelan.
- **En orden.** Entrega los resultados en el orden de entrada, y cada lote se extrae con
  `extract_each`, el mismo código que el camino secuencial: el resultado es idéntico.
- **Sin cortar.** Un archivo dañado da un `FailedExtraction` con su error y el flujo
  sigue. Cualquier otra excepción es un fallo del motor y se propaga.
- **`spawn`** en todos los sistemas, el método de macOS: hacer `fork` de un proceso con
  hilos (el API, los de OpenCV) puede heredar un candado tomado.
- **Un hilo de OpenCV por proceso**, para no repartir los núcleos dos veces. Limitar
  además los hilos de BLAS y de numba no cambió ningún tiempo, así que no se hace.

#### Tiempos medidos

Apple M4 Pro (8 núcleos de rendimiento y 4 de eficiencia), Python 3.12, archivos en la
caché del sistema. «Secuencial» es `extract_each` en el proceso principal, con todos los
hilos de OpenCV; el tiempo en paralelo incluye arrancar los procesos.

| Archivos | Descriptores por archivo | Secuencial | 2 procesos | 4 procesos | 8 procesos | Arranque |
|---|---:|---:|---:|---:|---:|---:|
| 5 000 fotos de ABO (256 px, JPEG) | 158 | 3,73 ms | 2,59 ms | 1,36 ms | 0,81 ms | 0,3 s |
| 5 000 clips de 1 s (WAV, 16 kHz) | 101 | 0,97 ms | 0,71 ms | 0,50 ms | 0,48 ms | 1,0–1,5 s |
| 384 pistas de 30 s (MP3, 44,1 kHz, estéreo) | 3 001 | 28,4 ms | 17,5 ms | 10,4 ms | 8,5 ms | 1,5–1,9 s |

Con 8 procesos las fotos van 4,6 veces más rápido: 100 000 fotos de ABO en unos 80 s. El
audio se acelera menos porque cada proceso paga al arrancar la importación de librosa y la
carga de sus funciones compiladas con numba (el «arranque» de la tabla); en un lote de
miles de archivos ese costo se reparte. La primera llamada a librosa tras instalarlo tarda
unos 12 s más, porque numba compila y guarda esas funciones en disco.

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
