# Datasets de imágenes y audio

Los datos del experimento multimedia (Parte 4): fotos de producto de **Amazon Berkeley
Objects** (ABO) y clips de voz de **Speech Commands**, cada uno en subconjuntos anidados
de 1 000, 10 000 y 100 000 elementos más un conjunto de consultas que no está en la base.
El audio de la demo, del Free Music Archive, se baja con
[`demos/descargar_fma.py`](../../demos/README.md#6-música-para-la-demo--descargar_fmapy).

| Módulo | Qué hace |
|---|---|
| [`subsets.py`](subsets.py) | la partición que comparten los tres: consultas fuera de la base, clases en proporción, prefijos anidados |
| [`abo.py`](abo.py) | baja las fichas y las fotos de ABO, une cada foto principal con su producto y saca solo las fotos elegidas |
| [`speech_commands.py`](speech_commands.py) | baja Speech Commands, lo descomprime y aparta hablantes enteros para las consultas |
| [`../remote.py`](../remote.py) | descargas que se reanudan y lectura de archivos remotos por rangos HTTP |

```bash
.venv/bin/python -m benchmarks.multimedia.abo --salida data/datasets/abo
.venv/bin/python -m benchmarks.multimedia.speech_commands --salida data/datasets/speech_commands
```

No hace falta ningún extra: todo es de la biblioteca estándar. Con Docker, lo mismo en el
perfil `bench`, que monta `data/datasets/`
(`docker compose --profile bench run --rm bench python -m benchmarks.multimedia.abo --salida data/datasets/abo`).

| Argumento | Para qué | Por omisión |
|---|---|---|
| `--salida` | carpeta del dataset | — |
| `--sizes` | tamaños de los subconjuntos | `1000 10000 100000` |
| `--consultas` | tamaño del conjunto de consultas | 500 |
| `--seed` | semilla de la partición | 20261105 |
| `--url` | de dónde bajar (ABO: la carpeta de archivos; Speech Commands: el `.tar.gz`) | el sitio oficial |
| `--conservar-tar` / `--conservar-archivo` | no borrar el archivo grande al terminar | se borra |

## Qué deja en disco

```
data/datasets/abo/
  imagenes/<xx>/<id>.jpg                  100 500 fotos (las de la base y las consultas)
  subconjuntos/{1000,10000,100000}/imagenes.csv
  consultas.csv
  abo-listings.tar  images.csv.gz         fichas de producto e índice de las fotos
data/datasets/speech_commands/
  clips/<palabra>/<hablante>_nohash_<toma>.wav     los 105 829 clips
  subconjuntos/{1000,10000,100000}/clips.csv
  consultas.csv
```

Los CSV se cargan tal cual con `CREATE TABLE ... FROM FILE`:

| Dataset | Columnas |
|---|---|
| ABO | `id, ruta, titulo, categoria` |
| Speech Commands | `id, ruta, titulo, palabra, hablante` |

- **`id`** numera desde 1 en el orden de la base, así que la fila 7 es la misma en los tres
  tamaños. Las consultas se numeran aparte.
- **`ruta`** es la carpeta de `--salida` tal como se escribió, seguida de la ruta del
  archivo: con `--salida data/datasets/abo` queda `data/datasets/abo/imagenes/c8/c8fa4944.jpg`,
  relativa a la raíz del repositorio. Es la misma ruta desde el API, que corre en la raíz, y
  dentro del contenedor `bench`, que monta los datos en el mismo lugar. Además cae dentro de
  `data/`, el directorio de datos del gestor, que es de donde se le permite leer.

## La partición

- **Consultas fuera de la base.** Se apartan **grupos enteros** elegidos al azar hasta
  juntar cuatro veces las consultas pedidas, y entre ellos se eligen las consultas en
  proporción a las clases. En Speech Commands el grupo es el hablante: ningún hablante de
  las consultas está en la base, así que el Recall@10 no se infla con otra toma de la misma
  voz. En ABO el grupo es la propia foto. Apartar cuatro veces lo necesario evita que unos
  pocos hablantes muy prolíficos den todas las consultas: con 500 pedidas salen de 45
  hablantes y cubren las 35 palabras.
- **Clases en proporción y anidados.** Dentro de cada clase el orden es al azar con la
  semilla; entre clases, cada elemento se ubica según su lugar relativo en la suya. Así
  **cualquier prefijo** de la base lleva las clases en la proporción del total, y el
  subconjunto de 1 000 es el principio del de 10 000, que es el principio del de 100 000.
- **Determinista.** Misma semilla y mismos archivos, misma partición, sin importar el
  orden en que se lean. No se usan las listas `validation_list.txt` y `testing_list.txt`
  de Speech Commands: separan 20 986 clips para validación y prueba, y lo que queda
  (84 843) no llega a 100 000.

## Amazon Berkeley Objects

[ABO](https://amazon-berkeley-objects.s3.amazonaws.com/index.html) publica 147 702 fichas
de producto de Amazon en varias tiendas e idiomas y 398 212 fotos de catálogo reducidas a
256 px como máximo. Cada producto entra con su **foto principal** (`main_image_id`):

- **La clase** es el `product_type` de la ficha. **El título**, su `item_name` en español
  si lo tiene, si no en inglés, y si no el primero que traiga.
- **La unión.** `images.csv.gz` dice en qué archivo está cada foto (`image_id` →
  `c8/c8fa4944.jpg`). Una foto que comparten varias fichas —el mismo producto en varias
  tiendas— cuenta una vez, con la ficha de menor `(item_id, dominio)`. Quedan **123 511
  fotos principales**, de 567 tipos.
- **El archivo de fotos** (3,25 GB) se baja con reanudación, se recorre una vez para saber
  dónde empieza cada foto, se copian solo las 100 500 que se usan y se borra. Si se vuelve
  a ejecutar con las fotos ya en disco, no lo baja.

Las clases están muy desbalanceadas, y la partición lo respeta: más de la mitad son
fundas de celular.

| Conjunto | Fotos | Tipos | Fundas de celular | Zapatos | Abarrotes | Hogar |
|---|---:|---:|---:|---:|---:|---:|
| 1 000 | 1 000 | 143 | 540 | 47 | 42 | 27 |
| 10 000 | 10 000 | 377 | 5 233 | 455 | 402 | 259 |
| 100 000 | 100 000 | 566 | 52 153 | 4 536 | 4 007 | 2 585 |
| consultas | 500 | 97 | 256 | 22 | 25 | 14 |

**Licencia.** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Los datos y las
fotos son © Amazon.com; el dataset lo armaron Matthieu Guillaumin, Thomas Dideriksen, Kenan
Deng y Himanshu Arora (Amazon.com), y Jasmine Collins y Jitendra Malik (UC Berkeley).
Referencia: Collins et al., *ABO: Dataset and Benchmarks for Real-World 3D Object
Understanding* (CVPR 2022). Los subconjuntos son un extracto sin modificar las fotos.

## Speech Commands v0.02

[Speech Commands](https://www.tensorflow.org/datasets/catalog/speech_commands) son
105 829 clips WAV de un segundo, a 16 kHz y 16 bits, de **2 618 hablantes** diciendo una de
**35 palabras** en inglés. El nombre del archivo es `<hablante>_nohash_<toma>.wav`. La
carpeta `_background_noise_` (seis grabaciones largas de ruido) no son clips y no se
descomprime. Las palabras van de 4 052 clips («zero», «five») a 1 557 («forward»):

| Conjunto | Clips | Hablantes | Palabras | Mínimo por palabra | Máximo por palabra |
|---|---:|---:|---:|---:|---:|
| 1 000 | 1 000 | 725 | 35 | 15 | 38 |
| 10 000 | 10 000 | 2 213 | 35 | 147 | 383 |
| 100 000 | 100 000 | 2 571 | 35 | 1 475 | 3 826 |
| consultas | 500 | 45 | 35 | — | 22 |

**Licencia.** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Pete Warden
(Google), *Speech Commands: A Dataset for Limited-Vocabulary Speech Recognition*,
arXiv:1804.03209 (2018). Los clips se usan tal como se publican.

## Medido

En un MacBook con conexión de unos 13 MB/s a los servidores de los datasets:

| | ABO | Speech Commands |
|---|---:|---:|
| descarga | 3,25 GB en 189 s | 2,43 GB en 113 s |
| fichas de producto | 83 MB | — |
| recorrer y extraer | 43 s (índice del `.tar` y 100 500 fotos) | 34 s (105 829 clips) |
| volver a ejecutarlo con todo en disco | 7 s, sin red salvo un `HEAD` | 3 s, sin red |
| en disco al terminar | 0,97 GB (0,86 GB de fotos) | 3,2 GB |
| CSV de 1 000 / 10 000 / 100 000 | 0,16 / 1,6 / 16 MB | 0,11 / 1,1 / 11 MB |

El archivo grande solo está en disco mientras se extrae: ABO necesita unos 4,3 GB libres
en ese momento y Speech Commands unos 5,7 GB. Ningún dato se versiona (`data/` se
ignora): quien los quiera, los descarga.
