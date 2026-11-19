# Copyright en audio (Parte 5, opción C)

Detecta si una canción contiene fragmentos de otras. Este paquete, `songcheck`, trae **el
catálogo de canciones originales** (#75): las 8 000 pistas de `fma_small` del Free Music
Archive, cortadas en tramos de 5 s e indexadas en el gestor con el HNSW de la Parte 4 sobre
sus MFCC; el trabajo de carga que lo deja listo y un API de lectura; y **la detección**
(#76): `POST /detections` recibe un audio y devuelve las canciones del catálogo que suenan
dentro, alineadas por votación por desfase. La evaluación (#77) está [abajo](#evaluación); la interfaz, un estudio de escucha con la línea de tiempo de los tramos, vive en [`web/`](web/README.md). Como todo servicio de la
Parte 5, usa el gestor solo por su API REST ([`apps/README.md`](../README.md)).

El paquete se llama `songcheck` y no `copyright`, que es un nombre interno de Python.

| Módulo | Qué hace |
|---|---|
| [`windows.py`](src/songcheck/windows.py) | las ventanas de 5 s cada 2,5 s, las mismas en el catálogo y en la consulta |
| [`fma/tracks.py`](src/songcheck/fma/tracks.py) | las pistas de `fma_small` según `tracks.csv`, leído por rangos |
| [`fma/archive.py`](src/songcheck/fma/archive.py) | el índice de `fma_small.zip` y la descarga de cada pista por rangos, verificada |
| [`fma/prepare.py`](src/songcheck/fma/prepare.py) | el catálogo completo en disco y `catalog.csv` |
| [`catalog.py`](src/songcheck/catalog.py) | `catalog.csv`, el CRC-32 de una pista y la escritura atómica |
| [`schema.py`](src/songcheck/schema.py) | las tablas `cr_canciones` y `cr_segmentos` y sus índices |
| [`seed.py`](src/songcheck/seed.py) | el trabajo de carga: `python -m songcheck.seed` |
| [`api.py`](src/songcheck/api.py) | el API: `uvicorn --factory songcheck.api:create_app` |
| [`upload.py`](src/songcheck/upload.py) | el audio subido: su formato por el contenido, su tamaño y su duración |
| [`loudness.py`](src/songcheck/loudness.py) | el nivel de cada ventana, para que el silencio no vote |
| [`detection.py`](src/songcheck/detection.py) | la votación por desfase y el puntaje, sin HTTP |
| [`detection_api.py`](src/songcheck/detection_api.py) | `POST /detections` |
| [`sample.py`](src/songcheck/sample.py) | un catálogo de juguete con melodías generadas, para probar un despliegue |
| [`settings.py`](src/songcheck/settings.py) | la configuración, con el prefijo `COPYRIGHT_` |

## El catálogo en disco

`fma_small` son 8 000 fragmentos de 30 s en MP3, mil por cada uno de ocho géneros, en un
`.zip` de 7,7 GB. **No se baja el `.zip`**: con `appkit.ranges` se lee su índice del final,
que da el tamaño y el CRC-32 de cada pista, y después cada pista con una sola petición HTTP
`Range`. De `fma_metadata.zip` (358 MB) se lee solo `tracks.csv`: título, artista y género.

```bash
uv run python -m songcheck.fma --data-dir data/datasets          # solo el disco
```

```
<data-dir>/fma_small/
  pistas/<xxx>/<id>.mp3   el audio, tal como sale del .zip
  catalog.csv             track_id, title, artist, genre, duration_seconds, path,
                          size_bytes, crc32
```

- **Verificada.** `zipfile` comprueba el CRC-32 de cada pista al descomprimirla; una que
  llega con otro se rechaza (`TracksError`) y no se escribe.
- **Reanudable.** Cada pista se escribe con otro nombre y se renombra al terminar. Una que
  ya está en disco se da por buena solo si mide y suma lo que dice el índice; las demás se
  vuelven a pedir. `catalog.csv` es lo último que se escribe: si existe y todas sus pistas
  coinciden, una segunda ejecución no va a la red.
- **Tres pistas sin audio** (099134, 108925 y 133297 pesan menos de 2 KB) se dejan fuera
  antes de bajar nada, por el tamaño que anota el índice. El catálogo son **7 994
  canciones**.
- **Decodificada entera.** La duración de cada pista sale de decodificarla con libsndfile,
  la misma librería con que la lee el gestor (unos 20 ms por pista, en paralelo). La
  cabecera no basta: tres pistas (098565, 098567 y 098569) tienen una cabecera legible y un
  MP3 que se corta a mitad, y el `CREATE INDEX` fallaba con ellas. Quedan fuera del
  catálogo, anotadas en el log.

**Quién es dueño de la descarga.** La descarga del FMA vivía en `demos/descargar_fma.py`
(2 000 pistas para la demo de la Parte 4) sobre `benchmarks/remote.py`. Ahora el dueño es
el servicio, porque el catálogo completo es su dato de partida y un servicio no puede
importar nada del árbol del gestor: la lectura por rangos pasó a `appkit.ranges` (la usan
también los lugares de la Parte 2) y la del FMA a `songcheck.fma`. El guion de la demo
quedó en elegir su subconjunto y escribir su CSV, llamando a `songcheck.fma`; los dos usan
la misma carpeta `pistas/`, así que las 2 000 pistas de la demo no se vuelven a bajar.

## Las tablas en el gestor

| Tabla | Columnas | Índices |
|---|---|---|
| `cr_canciones` | `id` (el `track_id` de FMA), `titulo`, `artista`, `genero`, `duracion`, `ruta`, `busqueda` | B+ de la clave; invertido en `busqueda` (título y artista, analizador inglés) |
| `cr_segmentos` | `id`, `cancion`, `inicio`, `fin`, `ruta` (`pista.mp3#t=inicio,fin`) | B+ de la clave; **HNSW** coseno en `ruta` |

Cada tramo es una fila cuya ruta es un fragmento temporal de W3C Media Fragments: el
gestor la resuelve, decodifica cada pista una vez y describe cada tramo con sus MFCC en
bolsa de palabras auditivas (Parte 4, issue #74), sin cortar archivos en disco. El id de un
tramo es `id de la canción · 1000 + número de ventana`: dice de qué canción es y el rango de
la clave da los tramos de una canción.

Las tablas y el índice invertido son migraciones repetibles (`CREATE … IF NOT EXISTS`) que
aplica el API al arrancar. **El HNSW no**: el gestor entrena su vocabulario con las filas de
la tabla y no crea uno sobre una tabla vacía, así que lo crea el trabajo de carga después
de cargar los tramos.

## Las ventanas: 5 s cada 2,5 s

`SEGMENT_WINDOW_SECONDS = 5.0` y `SEGMENT_HOP_SECONDS = 2.5`, en
[`windows.py`](src/songcheck/windows.py). Viven como constantes del paquete y no en la
configuración de cada proceso: el catálogo se cortó con ellas y la detección (#76) tiene
que consultar con las mismas (`POST /media/segments` con `window` y `hop`), o los vecinos de
una ventana de la consulta serían tramos de otra duración. La regla de corte es la del
gestor: entran solo las ventanas que caben enteras y un audio más corto que una ventana es
una sola.

| | 5 s cada 2,5 s (elegida) | 10 s cada 5 s (la de omisión de `/media/segments`) |
|---|---:|---:|
| tramos por pista de FMA (29,96 a 30,00 s decodificadas) | 10 u 11 | 4 o 5 |
| tramos del catálogo (7 994 canciones) | 83 290 | 35 326 |
| copia más corta que llena entera una ventana del catálogo | 7,5 s | 15 s |
| desfase máximo entre una ventana de la consulta y la del catálogo más cercana | 1,25 s (75 % de solape) | 2,5 s (75 % de solape) |
| ventanas MFCC por tramo (salto de 10 ms) | 501 | 1 001 |

- **Granularidad.** Una copia de `L` segundos contiene entera alguna ventana del catálogo
  si `L ≥ ventana + salto`: con 5 s y 2,5 s basta un fragmento de 7,5 s, la duración típica
  de un sample; con 10 s y 5 s haría falta uno de 15 s, la mitad de una pista de FMA.
- **Tamaño del índice.** La mitad de ventana da el doble de filas: 83 290 tramos
  en vez de 35 326 (2,4 veces). Medido abajo: el HNSW y el almacén de características crecen en
  proporción, y el `CREATE INDEX` lo paga en los MFCC, no en el grafo.
- **El salto es media ventana** en las dos: cada instante cae en dos ventanas, y el
  desfase entre la consulta y el catálogo es como mucho un cuarto de ventana. Bajar más el
  salto daría más filas sin mejorar ese solape relativo.
- **Lo que no baja de 5 s.** El enunciado pide ventanas de 5 a 10 s; con menos de 5 s, un
  tramo tiene menos de 500 ventanas MFCC para llenar un histograma de 256 palabras.
- **La consulta.** Una canción de 3 min se consulta con 71 ventanas de 5 s cada 2,5 s,
  dentro de las 256 que admite el gestor por consulta (`max_segments`).

## El trabajo de carga

```bash
COPYRIGHT_ENGINE_URL=http://127.0.0.1:8000 COPYRIGHT_ENGINE_KEY=… \
COPYRIGHT_DATA_DIR=data/datasets uv run python -m songcheck.seed
```

1. deja el catálogo completo y verificado en disco (lo de arriba);
2. aplica las migraciones;
3. carga `cr_canciones` y `cr_segmentos` con la carga masiva NDJSON del cliente
   (`bulk_insert`), anotando el avance cada 10 000 filas;
4. crea el HNSW de los tramos con `CREATE INDEX IF NOT EXISTS`.

**Repetible y reanudable.** Una tabla que ya tiene todas sus filas no se toca. Una carga
cortada sigue después de la última fila confirmada: las filas van en orden de id y el
gestor confirma los lotes en orden, así que lo cargado es siempre un prefijo. Si una tabla
termina con otro número de filas que el catálogo, o el gestor rechaza un lote, el trabajo
falla diciendo cuál (`SeedError`). La segunda ejecución solo verifica las pistas y cuenta
filas (medido abajo).

| Variable | Para qué | Por omisión |
|---|---|---|
| `COPYRIGHT_ENGINE_URL`, `COPYRIGHT_ENGINE_KEY` | el gestor y la clave del servicio (las de `appkit`) | **obligatorias** |
| `COPYRIGHT_MIGRATION_KEY` | clave `admin` para el DDL y el `CREATE INDEX` | la de `ENGINE_KEY` |
| `COPYRIGHT_DATA_DIR` | carpeta de datasets tal como la ve el servicio | **obligatoria** |
| `COPYRIGHT_ENGINE_DATA_DIR` | la misma carpeta tal como la ve el gestor | `DATA_DIR` en absoluto |
| `COPYRIGHT_DOWNLOAD_THREADS` | pistas que se bajan a la vez | 4 |
| `COPYRIGHT_AUDIO_URL`, `COPYRIGHT_METADATA_URL` | los dos `.zip` del FMA | el servidor oficial |
| `COPYRIGHT_INDEX_TIMEOUT_SECONDS` | cuánto se espera el `CREATE INDEX` | 3 600 |

**El gestor tiene que poder leer el audio.** Las filas guardan rutas, y el gestor solo lee
archivos de su `MINIGESTOR_DATA_DIR` y de `MINIGESTOR_IMPORT_DIRS` (separadas por comas):
la carpeta de datasets tiene que estar ahí. En Docker el gestor monta los datasets en
`/app/data/datasets`, que ya está en su `MINIGESTOR_IMPORT_DIRS`; si el trabajo de carga
los ve en otra ruta, `COPYRIGHT_ENGINE_DATA_DIR=/app/data/datasets` hace que las rutas
guardadas sean las del gestor. El `CREATE INDEX` del catálogo entero dura minutos; el
gestor no lo corta por `MINIGESTOR_STATEMENT_TIMEOUT` (el plazo se mira entre fila y fila
de las consultas, no en la construcción de un índice), y el cliente lo espera hasta
`COPYRIGHT_INDEX_TIMEOUT_SECONDS`.

## El API

```bash
COPYRIGHT_ENGINE_URL=… COPYRIGHT_ENGINE_KEY=… \
uv run uvicorn --factory songcheck.api:create_app --port 8101
```

Lo arma `appkit.create_service_app`: `/health`, `/ready`, `X-Request-ID` y `Server-Timing`,
errores uniformes y las migraciones al arrancar.

| Ruta | Devuelve |
|---|---|
| `GET /songs?query=&genre=&limit=&offset=` | canciones: con `query`, las que mejor responden por BM25 sobre título y artista (con su `score`); sin ella, en orden de id; `genre` filtra por género exacto. `limit` de 1 a 100 (20) |
| `GET /songs/{id}` | una canción con los tramos con que está indexada; `404` si no está |
| `GET /songs/{id}/audio` | el audio original, reenviado del gestor por trozos; con `Range`, `206` y solo ese trozo, para escucharlo desde el instante de una coincidencia |
| `GET /catalog` | canciones, tramos, ventana y salto, si el HNSW ya existe y canciones por género |
| `POST /detections` | las canciones del catálogo que suenan dentro del audio subido (campo `file`); ver abajo |

## Detección

```bash
curl -F "file=@cancion.mp3" http://localhost:8101/api/detections
```

1. **El audio se reconoce por su contenido** ([`upload.py`](src/songcheck/upload.py)): la
   firma de sus primeros bytes (`RIFF…WAVE`, `fLaC`, `OggS`, `ID3` o la sincronía de una
   trama MPEG) y después libsndfile, la librería con que lo decodifica el gestor, tiene que
   abrirlo y reconocer el mismo formato. El nombre no cuenta: un PNG llamado `cancion.mp3`
   es un `415` y un WAV llamado `cancion.mp3` se acepta como WAV. Al gestor, que reconoce un
   audio por la extensión, le llega con la de su formato real.
2. **Las ventanas en silencio no votan** ([`loudness.py`](src/songcheck/loudness.py)). Un
   tramo en silencio se parece a cualquier otro, y como todas las ventanas en silencio de
   una subida reciben los mismos vecinos, se encadenarían en rachas falsas. El audio se
   decodifica por bloques de 0,1 s y una ventana por debajo de `SILENCE_THRESHOLD_DBFS`
   (−60 dBFS) no vota y corta la racha. Una subida en silencio entera no llega al gestor.
3. **El gestor busca los vecinos de cada ventana**, con las mismas ventanas del catálogo
   (`windows.WINDOWING`, 5 s cada 2,5 s), en una sola petición (`POST /media/segments`): lo
   decodifica una vez, saca los MFCC de cada ventana y busca todas en un lote del HNSW.
4. **La votación por desfase** ([`detection.py`](src/songcheck/detection.py)) arma las
   coincidencias, y sus canciones se leen de `cr_canciones` en una consulta.

### Votación por desfase

Buscar cada ventana por separado da falsos positivos: dos canciones del mismo género se
parecen en un tramo suelto. Lo que delata una copia es que **varias ventanas seguidas**
tengan por vecina a la misma canción con el **mismo desfase** `t_subida − t_original` (la
idea de Shazam y de Content ID). Por cada canción candidata:

- cada vecino de cada ventana vota por un desfase, en saltos de 2,5 s: las ventanas de la
  subida y las del catálogo empiezan en la misma rejilla, así que el desfase es entero;
- el **apoyo** de un desfase son las ventanas con un voto a un salto o menos de él: una copia
  que no empieza en la rejilla cae entre dos tramos del catálogo y vota por los dos. A igual
  apoyo gana el pico del histograma, la mayor similitud por ese desfase exacto;
- el desfase con más apoyo da las **rachas** de ventanas consecutivas que lo apoyan y en las
  que **el original avanza**: el tramo elegido para cada ventana no va hacia atrás ni se
  repite en más de dos ventanas seguidas. Una copia fuera de la rejilla repite tramo una vez;
  un tramo del catálogo que se parece a todo (un «hub» del índice) aparece en muchas
  ventanas seguidas con un desfase que crece un salto por ventana, y sin esa condición la
  tolerancia lo encadenaría;
- el desfase que se reporta es el que votan más de los tramos elegidos de la racha, no el
  desfase con que se buscó: un tramo suelto de la misma canción entre los vecinos de la ventana
  siguiente le da un voto de apoyo de más a un desfase vecino, que gana con la misma racha (#137);
- las rachas de `MIN_RUN_WINDOWS` ventanas o más (2: 7,5 s de audio) son coincidencias; sus
  ventanas ya no votan por otro desfase de la misma canción y se sigue con el siguiente. Una
  subida de una sola ventana pide rachas de una.

**Puntaje = similitud media de la racha × cobertura**, y la cobertura es la fracción de las
ventanas que las dos grabaciones podían compartir: `ventanas de la racha / min(ventanas
audibles de la subida, ventanas del original)`. Dividir solo por la subida castigaría un
sample de 10 s dentro de una canción de 3 min, justo lo que se busca; dividir solo por el
original, un fragmento corto de un original largo. Con el mínimo, una copia que cubre todo
lo que podía cubrir vale 1 sea la subida más larga o más corta que el original. Todo es puro
y determinista: el orden de los vecinos no cambia la respuesta y los empates se rompen por
similitud, desfase, canción y posición.

### La respuesta

Su forma, con valores de ejemplo:

```json
{
  "format": "MP3", "duration_seconds": 180.0, "sample_rate": 44100, "channels": 2,
  "window_seconds": 5.0, "hop_seconds": 2.5, "windows": 71, "silent_windows": 0,
  "threshold": 0.43, "detected": true,
  "matches": [{
    "song": {"id": 2, "title": "Food", "artist": "AWOL"},
    "upload": {"start_seconds": 60.0, "end_seconds": 87.5},
    "original": {"start_seconds": 0.0, "end_seconds": 27.5},
    "offset_seconds": 60.0, "windows": 10, "mean_similarity": 0.997,
    "coverage": 1.0, "score": 0.997, "above_threshold": true
  }],
  "engine_timings": {"decode_ms": 128.0, "extraction_ms": 160.4, "search_ms": 1.5}
}
```

Las coincidencias van de mayor a menor puntaje, hasta `MAX_MATCHES`; `detected` dice si
alguna pasa el umbral. `Server-Timing` lleva cada etapa: `probe`, `loudness`, `segments` (la
ida y vuelta al gestor), las tres del gestor (`decode`, `extraction`, `search`, sobre todas
las ventanas a la vez), `voting` y `songs`.

| Subida | Respuesta |
|---|---|
| copia del catálogo, en MP3, WAV, FLAC u OGG, mono o estéreo, a cualquier frecuencia | `200` con la coincidencia; el gestor mezcla a mono y remuestrea (probado a 16 y 44,1 kHz) |
| en silencio | `200` sin coincidencias, `silent_windows` = `windows` |
| más corta que una ventana (de 1 a 5 s) | `200`, una sola ventana, como en el gestor |
| más corta que 1 s | `400` `AudioTooShortError` |
| vacía, o con firma de audio y contenido dañado | `400` `CorruptAudioError` |
| otro formato, aunque se llame `.mp3` | `415` `UnsupportedAudioError` |
| más pesada que `MAX_UPLOAD_BYTES` o más larga que `MAX_UPLOAD_SECONDS` | `413` `UploadTooLargeError` o `AudioTooLongError` |

| Variable (`COPYRIGHT_…`) | Para qué | Por omisión |
|---|---|---|
| `MAX_UPLOAD_BYTES` | el audio más pesado | 48 MiB (el gestor admite 64) |
| `MAX_UPLOAD_SECONDS` | el audio más largo | 600 s: 239 ventanas, dentro de las 256 del gestor |
| `NEIGHBOURS_PER_WINDOW` | tramos que se piden por ventana | 10 |
| `MIN_RUN_WINDOWS` | ventanas seguidas que hacen una coincidencia | 2 |
| `MATCH_THRESHOLD` | puntaje desde el que una coincidencia es copia | 0,43, de la curva ROC (abajo) |
| `MAX_MATCHES` | coincidencias que se devuelven | 10 |
| `SILENCE_THRESHOLD_DBFS` | nivel bajo el cual una ventana no vota | −60 |

**El umbral sale de la evaluación**: es el de máxima J de Youden en la curva ROC de 400
remixes sin distorsión ([abajo](#evaluación)). El de #76 era 0,45, de 20 casos de control.

### Medido

Medido el 19-11-2026 en el mismo equipo, con carga media de 4,9 a 5,1, contra una copia del
gestor con el catálogo entero de la que se borraron 40 canciones para usarlas como pistas no
catalogadas. El detalle está en el
[§14 de `benchmarks/README.md`](../../benchmarks/README.md#14-detección-de-copyright--latencia-de-post-detections-y-casos-de-control)
y los datos crudos en
[`benchmarks/results/copyright_deteccion.json`](../../benchmarks/results/copyright_deteccion.json).

| Subida en MP3 | Ventanas | p50 | p95 |
|---:|---:|---:|---:|
| 30 s | 11 | 79,8 ms | 87,2 ms |
| 60 s | 23 | 157,4 ms | 165,4 ms |
| 180 s | 71 | 489,8 ms | 531,9 ms |

| Caso (20 de cada uno) | Puntaje de la copiada (mediana) | Pasa 0,45 |
|---|---:|---:|
| pista del catálogo entera | 0,997 | 20, con desfase 0 |
| tramo de 15 s fuera de la rejilla, desfase real 4 s | 0,494 | 18, con desfase 5 o 2,5 s |
| tramo de 7,5 s | 0,266 | 0 |
| pista no catalogada (mejor de cualquier canción) | 0,272 | 3 |

- Unos **7 ms por ventana**, casi todo en decodificar y sacar MFCC; el HNSW y la votación no
  llegan a 2 ms con 3 min. El servicio decodifica el audio una vez más para medir el nivel
  de las ventanas, un tercio de la latencia.
- Las copias largas se separan bien de lo que no se copió; un tramo de 7,5 s no: cubre dos
  o tres de las 11 ventanas que podía compartir y la cobertura lo deja en el rango de las
  canciones no copiadas. Las tres pistas no catalogadas que pasan tienen rachas de 6 a 8
  ventanas con otra canción del FMA y hay que escucharlas para saber si comparten material.

### Evaluación

Un conjunto de prueba generado con la verdad conocida (#77): 200 remixes de 1 a 3 fragmentos
de 5 a 20 s de canciones del catálogo, pegados con fundidos de 1 s, y 200 iguales con
canciones que se borraron del catálogo; cada uno sin distorsión y con 12 distorsiones por
separado. Medido el 19-11-2026 contra una copia del gestor con 7 794 canciones, con la
máquina cargada (carga media de 6,9 a 24,4). El detalle está en el
[§17 de `benchmarks/README.md`](../../benchmarks/README.md#17-evaluación-de-la-detección-de-copyright--remixes-con-la-verdad-conocida)
y los datos crudos en
[`benchmarks/results/copyright_evaluacion_casos.json`](../../benchmarks/results/copyright_evaluacion_casos.json).

![Precisión, recall y F1 por distorsión](../../benchmarks/figures/copyright-evaluacion-deteccion.png)

| Versión (umbral 0,43) | Recall | Precisión | F1 | AUC |
|---|---:|---:|---:|---:|
| sin distorsión | 0,55 | 0,43 | 0,48 | 0,62 |
| MP3 a 64 kbit/s | 0,54 | 0,42 | 0,47 | 0,59 |
| volumen −1 / −3 / −12 dB | 0,47 / 0,11 / 0,00 | 0,37 / 0,11 / 0,00 | 0,41 / 0,11 / 0,00 | 0,55 / 0,17 / 0,01 |
| ruido a 30 / 20 / 10 dB de SNR | 0,34 / 0,18 / 0,03 | 0,31 / 0,16 / 0,02 | 0,32 / 0,17 / 0,03 | 0,40 / 0,21 / 0,04 |
| pasa bajos a 4 kHz | 0,01 | 0,01 | 0,01 | 0,02 |
| tempo ±5 % | 0,08 | 0,08-0,09 | 0,08-0,09 | 0,13 |
| tono ±1 semitono | 0,05-0,07 | 0,06-0,07 | 0,06-0,07 | 0,11 |

- **El umbral, 0,43**, encuentra el 55 % de los fragmentos copiados y da alguna coincidencia
  en el 29 % de los remixes sin copias. Ningún umbral separa mucho mejor.
- **Dónde falla: el nivel.** El descriptor (MFCC con su c0, la energía, cuantizado en una
  bolsa de palabras) cambia con el volumen: −3 dB dejan el recall en 0,11 y −12 dB en 0. El
  ruido, el pasa bajos, el tempo y el tono también lo rompen; el MP3 a 64 kbit/s no.
- **El puntaje castiga los remixes de varios fragmentos.** La canción copiada aparece entre
  las coincidencias en todos los fragmentos de 10 s o más, pero la cobertura los divide por
  todo lo que podían compartir: el recall es 1,00 con un fragmento, 0,58 con dos y 0,36 con
  tres. Contar las ventanas de la racha cuando su similitud es alta subiría el área bajo la
  curva de 0,62 a 0,78 sobre las mismas coincidencias; queda propuesto, igual que quitar c0
  del descriptor, que lo haría inmune al volumen.
- **Cuando la encuentra, la ubica**: IoU mediana de 0,91 en la subida y 0,87 en el original.
  Revisar un minuto de audio cuesta unos 180 ms.

## Docker

[`compose.yaml`](compose.yaml) monta la app como las demás (ver
[`apps/README.md`](../README.md#desplegar-una-app)) y lo incluye el compose raíz:

| Servicio | Qué es | Redes | Puertos |
|---|---|---|---|
| `copyright-api` | `uvicorn --factory songcheck.api:create_app`, imagen de `deploy/service` con `APP_DIR=copyright` y `APP_PACKAGE=songcheck` | `data`, `copyright` | — |
| `copyright-web` | su interfaz ([`web/`](web/README.md)) servida por el nginx del kit web, que reenvía `/api` a su API | `copyright` | 8101 / 8401 |
| `copyright-seed` (perfil `seed`) | `python -m songcheck.seed` con la imagen del API | `data` | — |
| `copyright-tunnel` (perfil `tunnel`) | una URL HTTPS temporal para la web | `copyright` | — |

**El audio no viaja en ninguna imagen.** La carpeta de datasets (`MINIGESTOR_DATASETS_DIR`,
por omisión `data/datasets`) entra de solo lectura en el gestor y en la carga, en la misma
ruta, `/app/data/datasets`, que ya está en `MINIGESTOR_IMPORT_DIRS` del gestor: las rutas que
guarda la carga son las que el gestor abre, sin `COPYRIGHT_ENGINE_DATA_DIR`. Como el montaje
es de solo lectura, el catálogo se baja antes en el anfitrión:

```bash
uv run python -m songcheck.fma --data-dir data/datasets      # 7,4 GB, una vez
python3 -m tools.compose_keys                                 # claves de cada servicio en .env
docker compose up --build --detach --wait
docker compose run --rm copyright-seed                        # repetible
curl http://localhost:8101/api/catalog
```

Para probar el despliegue sin bajar FMA, `songcheck.sample` escribe un catálogo de juguete
—cuatro melodías al azar de 12 s y su `catalog.csv`— con solo la biblioteca estándar:

```bash
PYTHONPATH=apps/copyright/src python3 -m songcheck.sample --data-dir data/datasets
```

Es lo que hace el trabajo `compose` de la CI: carga ese catálogo dos veces (la segunda no
carga nada), consulta `/api/health`, `/api/ready`, `/api/catalog` y `/api/songs/2` por el
puerto 8101 y sube a `/api/detections` la pista 2 desde los 2,5 s, con nombre de MP3: tiene
que salir detectada, como WAV, con desfase −2,5 s. La web deja pasar cuerpos de hasta
`COPYRIGHT_WEB_MAX_BODY_SIZE` (49 MiB: el audio máximo y el multipart). Las claves salen de `tools/compose_keys.py`, donde la app ya está con el prefijo
`COPYRIGHT_`: el API atiende con `COPYRIGHT_ENGINE_KEY` (`write`) y migra y carga con
`COPYRIGHT_MIGRATION_KEY` (`admin`).

## Medido

Medido el 18-11-2026 en un Apple M4 Pro (12 núcleos), macOS, Python 3.12, con una conexión
doméstica y la máquina cargada por otros procesos (carga media de 21 a 37). Los datos crudos
están en [`benchmarks/results/copyright_catalogo.json`](../../benchmarks/results/copyright_catalogo.json)
y el detalle en el [§12 de `benchmarks/README.md`](../../benchmarks/README.md#12-catálogo-de-copyright--descargar-cargar-e-indexar-fma-por-tramos).

![Cargar e indexar el catálogo de copyright](../../benchmarks/figures/copyright-catalogo.png)

**El catálogo de verdad.** `python -m songcheck.seed` contra un gestor servido por uvicorn
en otro proceso (`api.main:app_from_environment`, datos en `data/copyright-engine/` del
checkout y `data/datasets` en `MINIGESTOR_IMPORT_DIRS`), con las pistas ya descargadas:

| Etapa | Primera ejecución | Segunda ejecución |
|---|---:|---:|
| verificar el catálogo en disco (7 994 CRC-32, 7,43 GiB) | 6,4 s | 6,1 s |
| `cr_canciones`: 7 994 filas por NDJSON | 8,4 s | 0,1 s |
| `cr_segmentos`: **83 290 tramos** por NDJSON | 92,4 s | 0,4 s |
| `CREATE INDEX ... USING HNSW` de los tramos | 141,3 s | 0,0 s (ya estaba) |
| **total del proceso** | **249 s** | **7,5 s** |

El gestor quedó con **240 MB** en disco. Un tramo se encuentra a sí mismo:
`SIMILAR_TO('…/000002.mp3#t=7.5,12.5', k = 3)` devuelve la canción 2 en 7,5 s (puntaje
1,0000) y sus dos vecinas solapadas, 5,0 s (0,951) y 10,0 s (0,910).

**La descarga** se midió aparte, con el experimento: 5 997 pistas (5,58 GiB) en 909,6 s, 6,6
MB/s con 8 conexiones; las otras 2 000 ya estaban (las de la demo) y solo se verificaron.

**Por tamaño de catálogo** (el experimento, con el gestor en el mismo proceso):

| Canciones | Tramos | Carga NDJSON | `CREATE INDEX` | de él, extraer MFCC | de él, cuantizar | Disco del gestor |
|---:|---:|---:|---:|---:|---:|---:|
| 1 000 | 10 422 | 9,3 s | 27,8 s | 18,3 s | 8,2 s | 29,4 MiB |
| 2 000 | 20 823 | 22,4 s | 43,4 s | 24,3 s | 17,2 s | 58,5 MiB |
| 4 000 | 41 650 | 58,3 s | 77,6 s | 37,7 s | 36,4 s | 116,9 MiB |
| 7 994 | 83 290 | 108,1 s | 190,4 s | 53,7 s | 132,1 s | 233,6 MiB |

- **Listo en unos 4 minutos sobre la descarga, y repetirlo cuesta 7,5 s**, casi todo en
  releer las pistas para comprobar su CRC-32: el trabajo de carga se puede correr en cada
  despliegue.
- **El índice lo pagan los MFCC y su cuantización**, no el grafo: el HNSW de 83 290 vectores
  se arma en 3,8 s y el vocabulario en 1 s. La extracción se reparte en 12 procesos y cada
  pista se decodifica una vez para sus 10 u 11 tramos; la cuantización corre en el proceso
  del gestor y con el catálogo entero es el 69 % del `CREATE INDEX`. Llevarla a los procesos
  de extracción es la mejora siguiente, del motor.
- **2,9 KiB por tramo** en disco, lineal: el gestor guarda rutas, no audio, así que el
  catálogo ocupa un 3 % de sus 7,43 GiB.
- Con 10 s cada 5 s habría 35 326 tramos en vez de 83 290: como el disco y el índice crecen
  en proporción a los tramos, la ventana elegida cuesta 2,4 veces más en los dos, a cambio
  de detectar copias de 7,5 s en vez de 15 s.

## Tests

```bash
uv run pytest apps/copyright -q     # ventanas, catálogo, descarga, votación por desfase
uv run pytest tests/copyright -q    # carga, API y detección contra el gestor real
```

La descarga se prueba contra un servidor HTTP local que sirve un `.zip` de juguete por
rangos: qué pistas entran, que una segunda ejecución no hace ninguna petición, que solo se
vuelven a pedir las pistas que faltan o están dañadas y que una pista con otro CRC-32 se
rechaza. Los de `tests/copyright/` viven en el árbol del gestor porque construyen su
aplicación: cargan audios generados, comprueban que un tramo se encuentra a sí mismo por el
HNSW, que una segunda carga no cambia nada, que una carga cortada sigue donde quedó y que el
trabajo completo (`run_seed`, con el FMA de juguete y el gestor servido por uvicorn) se
puede repetir sin hacer nada.

La votación se prueba con listas de ventanas sintéticas: copia exacta, desplazada y fuera de
la rejilla, dos canciones pegadas, la misma copiada dos veces, ruido, empates, un tramo que
no avanza y el orden de los vecinos. `POST /detections` se prueba contra el gestor real con
el catálogo de `songcheck.sample` (pistas de 30 s): un tramo copiado con su desfase y sus
tramos, en FLAC, OGG y MP3 con nombre de WAV, a 44,1 kHz estéreo, dos canciones pegadas, una
subida de una ventana, silencio, ruido, un PNG, un audio dañado y los límites.

## Licencias

Free Music Archive (Defferrard et al., *FMA: A Dataset for Music Analysis*, ISMIR 2017): los
metadatos son CC BY 4.0; cada pista conserva la licencia Creative Commons que eligió su
artista, anotada en `tracks.csv`.
