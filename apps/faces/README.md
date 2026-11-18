# Rostros (`faces`)

Servicio de la opción D del Anexo A: reconocimiento facial en vivo. Esta primera pieza es el
**pipeline del modelo** —de los bytes de una foto a los embeddings de sus rostros— y la
descarga de los modelos y del dataset LFW. El API, el registro y la búsqueda en el HNSW del
gestor llegan en los issues siguientes (#90, #91); como todo servicio, no importa el
código del gestor (ver [`apps/README.md`](../README.md)).

```
src/faces/
  config.py        PipelineConfig: tope de píxeles, lados de detección, umbrales de YuNet
  image.py         bytes → BGR de 8 bits: formatos aceptados, tope de píxeles, EXIF, canales
  detection.py     YuNet: caja, cinco puntos y confianza; escala de detección; «un solo rostro»
  recognition.py   SFace: alineación con los cinco puntos y embedding de norma 1
  pipeline.py      FacePipeline: decodificar → detectar → alinear → describir
  models.py        los dos modelos de OpenCV Zoo, fijados por commit y SHA-256
  lfw.py           LFW y sus pares oficiales: descarga, extracción y lectura de pliegues
  errors.py        InvalidImageError, ImageTooLargeError, NoFaceError, MultipleFacesError...
  __main__.py      python -m faces models | lfw
```

## Pipeline

```
bytes ─▶ decode_image ─▶ detection_scale + YuNet ─▶ rostros por área ─▶ alignCrop ─▶ SFace ─▶ ‖·‖ = 1
         (Pillow)         (cv2.FaceDetectorYN)                          (112 x 112)   (128 dim)
```

| Etapa | Qué hace | Por qué así |
|---|---|---|
| Decodificar | Pillow abre la imagen solo si es JPEG, PNG, WebP, BMP, GIF o TIFF; rechaza la que **declara** más de `max_image_pixels` (50 M) antes de decodificarla; aplica la orientación EXIF; pasa el gris a tres canales, aplana la transparencia sobre blanco, toma el byte alto de 16 bits y el primer cuadro de un GIF | Pillow lee la cabecera antes de reservar memoria, así que una «bomba» de pocos bytes que promete gigapíxeles no llega a decodificarse; `cv2.imdecode` no permite ese tope por llamada y, para conservar el alfa, ignora el EXIF |
| Escalar | el lado mayor queda entre 320 y 640 px (`detection_scale`) | YuNet cuesta lo mismo con cualquier foto grande, y una foto chica ampliada a 320 px sí muestra su rostro (con 96 px de lado se encuentra; sin ampliar, no) |
| Detectar | YuNet 2023mar con confianza ≥ 0,9 y NMS a 0,3; las coordenadas vuelven a los píxeles de la foto original | caja y cinco puntos (ojos, punta de la nariz, comisuras), que es lo que necesita la alineación |
| Elegir | todos los rostros, del de mayor área al de menor; `ImageAnalysis.only_face()` exige exactamente uno | la búsqueda quiere a todos los de la foto y el registro, a una sola persona |
| Alinear | `alignCrop`: semejanza que lleva los cinco puntos a la plantilla de 112 x 112 de SFace, sobre la foto **original** | se pierde menos detalle que alineando sobre la imagen reducida para detectar |
| Embeber | SFace (pérdida de margen angular tipo ArcFace), 128 `float32` divididos por su norma | con norma 1, el producto punto es el coseno, que es la métrica del HNSW |

`FacePipeline` carga las dos redes al crearse y **no se comparte entre hilos** (OpenCV DNN
guarda el tamaño de la última entrada): cada hilo o proceso del servicio crea el suyo.

```python
from pathlib import Path
from faces import FacePipeline, PipelineConfig, locate_models

pipeline = FacePipeline(locate_models(Path("data/models/faces")), PipelineConfig())
analysis = pipeline.analyze(Path("foto.jpg").read_bytes())
for found in analysis.faces:            # del rostro más grande al más chico
    print(found.face.box, found.face.score, found.embedding.shape)   # (128,)
selfie = analysis.only_face()           # NoFaceError / MultipleFacesError
```

## Modelos y datos

| Archivo | Origen | Licencia | SHA-256 |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` (227 KiB) | OpenCV Zoo, commit `48b10fa` | Apache 2.0 | `8f2383e4…2552fa4` |
| `face_recognition_sface_2021dec.onnx` (37 MiB) | OpenCV Zoo, commit `48b10fa` | Apache 2.0 | `0ba9fbfa…7c34e79` |
| `lfw.tgz` (172 MiB) | espejo de figshare que usa scikit-learn (archivo 5976018) | sin licencia explícita; uso de investigación | `055f7d9c…d8536c0` |
| `pairs.txt`, `pairsDevTrain.txt`, `pairsDevTest.txt` | figshare 5976006, 5976012, 5976009 | ídem | en [`lfw.py`](src/faces/lfw.py) |

Las sumas de los modelos son las de sus punteros de Git LFS en ese commit; las de LFW, las
que fija scikit-learn en `fetch_lfw_pairs`. Todo se baja con
[`appkit.download`](../kit/src/appkit/download.py): se reanuda si se corta y nunca da el
nombre final a un archivo con otra suma.

```bash
python -m faces models --salida data/models/faces   # o con FACES_MODELS_DIR definida
python -m faces lfw --salida data/datasets/lfw      # deja lfw/<Persona>/<Persona>_0001.jpg
```

`python -m faces models` es también lo que correrá la imagen del servicio al construirse
(`RUN python -m faces models --salida /opt/faces/models` con `FACES_MODELS_DIR` apuntando
ahí), de modo que el contenedor arranca con los modelos verificados y sin red.

LFW se extrae en una carpeta aparte que se mueve al final, con el filtro `data` de
`tarfile` (sin rutas absolutas, enlaces ni `..`), y se comprueba que hay 13 233 fotos de
5 749 personas y que existen todas las que nombran los tres archivos de pares.

## Tests

```bash
uv run --no-sync python -m pytest -q apps/faces                       # sin modelos: se saltan los que los usan
FACES_MODELS_DIR=data/models/faces uv run --no-sync python -m pytest -q apps/faces
```

Los que corren los modelos usan tres fotos de dominio público de la NASA
([`tests/fixtures/`](tests/fixtures/README.md)): un retrato, otro retrato de la misma
persona y una foto de grupo de siete. Cubren una foto sin rostro (gris y ruido), la de
varios (siete, ordenados por área), la girada por EXIF, en gris, con canal alfa, de 6 000 px
y de 96 px, además de bytes que no son imagen, un JPEG cortado, un formato no aceptado y
una cabecera que declara más píxeles que el tope. El embedding tiene norma 1; la misma
persona en dos fotos da un coseno de 0,74 a 0,83 y cualquier otra, menos de 0,18 (el umbral
que publica OpenCV para SFace es 0,363).

En la CI, `FACES_MODELS_DIR` está definida y los modelos se bajan con su suma antes de
pytest (y se guardan en la caché de Actions): un test que no los encuentre falla en vez de
saltarse.

## Rendimiento medido

Con [`benchmarks/faces_benchmark.py`](../../benchmarks/faces_benchmark.py), sobre las 13 233
fotos de LFW (250 x 250 px) y 500 de ellas ampliadas y vueltas a codificar en JPEG; un hilo
de OpenCV, Apple M4 Pro, OpenCV 5.0.0 y Pillow 13.0.0. Resultados crudos en
[`rostros.json`](../../benchmarks/results/rostros.json); análisis en
[`benchmarks/README.md`](../../benchmarks/README.md#7-reconocimiento-facial--latencia-del-pipeline-y-cobertura-en-lfw).

![Tiempo de cada etapa según el lado de la foto](../../benchmarks/figures/rostros-latencia.png)

| Lado mayor | Decodificar p50 / p95 | Detectar p50 / p95 | Alinear y embeber p50 / p95 | Total p50 / p95 |
|---:|---:|---:|---:|---:|
| 250 px (LFW) | 0,41 / 0,46 ms | 3,6 / 3,7 ms | 13,0 / 13,3 ms | 17,0 / 17,4 ms |
| 500 px | 1,2 / 1,3 ms | 9,1 / 9,3 ms | 13,1 / 13,5 ms | 23,4 / 23,9 ms |
| 1 000 px | 3,8 / 4,0 ms | 23,3 / 23,6 ms | 13,1 / 13,4 ms | 40,2 / 40,7 ms |
| 2 000 px | 14,4 / 14,9 ms | 21,9 / 22,2 ms | 13,2 / 13,5 ms | 49,4 / 50,2 ms |
| 4 000 px | 55,5 / 58,1 ms | 26,9 / 27,4 ms | 13,2 / 13,5 ms | 95,6 / 98,4 ms |

**Cobertura del detector en LFW:** YuNet encuentra al menos un rostro en **13 221 de las
13 233 fotos (99,91 %)**; en 12 no encuentra ninguno, y en 1 033 (7,8 %) encuentra más de
uno, porque muchas fotos de LFW son de prensa y tienen a otras personas detrás. Por eso la
evaluación de LFW (#92) debe tomar el rostro que corresponde al centro de la foto y no
exigir uno solo.

- El **embedding** cuesta lo mismo con cualquier foto (13 ms): SFace siempre ve 112 x 112.
- La **detección** deja de crecer pasados los 640 px: YuNet ve siempre la foto reducida a
  ese lado, y lo que queda es reducirla. Reducir en dos pasos —un factor entero primero, por
  el camino rápido de `INTER_AREA`— bajó la detección de una foto de 4 000 px de 93,0 a
  26,9 ms y la de 2 000 px de 39,2 a 21,9 ms; el total de 4 000 px, de 161,9 a 95,6 ms.
- Con fotos de celular de 12 MP domina **decodificar**: 55 ms de los 96. Una foto de 500 a
  1 000 px, del tamaño de un cuadro de cámara, tarda de 23 a 40 ms con su rostro.
