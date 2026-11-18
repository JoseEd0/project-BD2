# Tienda con recomendaciones (Parte 5, opción B)

Una tienda de instrumentos musicales que recomienda combinando las cuatro clases de datos
del gestor: compras conjuntas (relacional), fotos (vectorial), descripciones (texto) y
stock en tiendas cercanas (espacial). Como todo servicio de la Parte 5, usa el gestor solo
por su API REST ([`apps/README.md`](../README.md)).

Por ahora el paquete `shop` trae **el dataset** (#79): compras reales para que la
evaluación del recomendador (Hit@10, NDCG@10) sea honesta, fichas y fotos de los productos,
y tiendas reales de Lima. La ingesta en el gestor, el recomendador y su página llegan en
#80 a #83.

| Módulo | Qué hace |
|---|---|
| [`sources.py`](src/shop/dataset/sources.py) | URL, SHA-256 y tamaño de cada archivo publicado, en una revisión fija |
| [`transport.py`](src/shop/dataset/transport.py) | las peticiones de las fotos, con reintentos y espera creciente |
| [`catalog.py`](src/shop/dataset/catalog.py) | de una ficha JSON a un producto con columnas planas |
| [`interactions.py`](src/shop/dataset/interactions.py) | las compras, el 5-core y la partición por usuario |
| [`images.py`](src/shop/dataset/images.py) | fotos: bajar, reducir, recodificar, deduplicar y reanudar |
| [`stores.py`](src/shop/dataset/stores.py) | tiendas de Lima y su inventario sintético con semilla |
| [`prepare.py`](src/shop/dataset/prepare.py) | todo junto: tablas y manifiesto |

## Por qué Amazon Reviews 2023, instrumentos musicales

[Amazon Reviews 2023](https://amazon-reviews-2023.github.io/) (McAuley Lab, UCSD; Hou et
al., *Bridging Language and Items for Retrieval and Recommendation*, 2024) trae compras
verificadas con fecha y, para cada producto, título, precio, categoría, descripción y
fotos: todo lo que pide el enunciado, con usuarios de verdad. Su **5-core** (cada usuario y
cada producto con al menos 5 interacciones) es el conjunto estándar para evaluar
recomendadores. Tamaños medidos del 5-core de las categorías candidatas:

| Categoría | Interacciones | Usuarios | Productos |
|---|---:|---:|---:|
| **Musical_Instruments** | 511 836 | 57 439 | 24 587 |
| Industrial_and_Scientific | 412 947 | 50 985 | 25 848 |
| Video_Games | 814 586 | 94 762 | 25 612 |
| Baby_Products | 1 241 083 | 150 777 | 36 013 |

Las cuatro caen en el rango de 20 000 a 60 000 productos. Instrumentos musicales tiene
grupos visuales claros (guitarras, baterías, pedales, cables), lo que hace medible la señal
visual; su tamaño permite bajar todas las fotos, y una tienda de instrumentos en Lima es
verosímil.

## Preparar el dataset

Un comando baja y prepara todo. Necesita antes los lugares de Lima de la Parte 2
(`lugares_lima.csv`, ver el [README raíz](../../README.md#35-cargar-datos-desde-la-interfaz)):

```bash
uv sync --all-packages
.venv/bin/python -m demos.descargar_lugares --salida data/datasets/lugares_lima.csv \
    --recuadro -12.52 -77.20 -11.57 -76.62
uv run python -m shop.dataset prepare --data-dir data/datasets
```

| Argumento | Para qué | Por omisión |
|---|---|---|
| `--data-dir` | carpeta de datasets; el dataset queda en `<data-dir>/amazon_musical_instruments/` | `$SHOP_DATA_DIR` |
| `--places` | CSV de lugares de la Parte 2 | `<data-dir>/lugares_lima.csv` |
| `--max-side` | lado mayor de las fotos, en píxeles | 512 |
| `--jpeg-quality` | calidad JPEG de las fotos recodificadas | 85 |
| `--workers` | descargas de fotos simultáneas | 8 |
| `--seed` | semilla del inventario | 2023 |
| `--products-per-store` | productos distintos en stock por tienda | 1 000 |
| `--max-quantity` | unidades máximas de un producto en una tienda | 12 |

**Es reanudable.** Los archivos publicados se bajan con `appkit.download.fetch_verified`
([`apps/kit`](../README.md#descargas-de-modelos-y-datasets)): a un `.parcial` que sigue
desde su último byte si se corta, y solo toman su nombre final cuando miden y hashean lo
que publica el Hub. Cada foto se anota en `images.jsonl` al terminar; al volver a ejecutar, no se vuelve
a pedir ninguna que ya esté guardada y verifique su SHA-256, ni ninguna que falló sin
remedio (un 404, un archivo que no es imagen). Las que fallaron por la red, un 429 o un 5xx
se reintentan. Todo lo demás es determinista: una segunda ejecución no va a la red y deja
los mismos bytes (lo comprueba `test_shop_prepare.py`).

## Fuentes

Las URL apuntan a la revisión `2b6d039` del repositorio
[`McAuley-Lab/Amazon-Reviews-2023`](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023)
del Hugging Face Hub, con el SHA-256 y el tamaño que el Hub publica para cada archivo
(los `RemoteFile` de [`sources.py`](src/shop/dataset/sources.py)):

| Archivo | Ruta en el repositorio | Tamaño |
|---|---|---:|
| fichas | `raw/meta_categories/meta_Musical_Instruments.jsonl` | 632 MB |
| entrenamiento | `benchmark/5core/last_out/Musical_Instruments.train.csv` | 23,0 MB |
| validación | `benchmark/5core/last_out/Musical_Instruments.valid.csv` | 3,3 MB |
| prueba | `benchmark/5core/last_out/Musical_Instruments.test.csv` | 3,3 MB |

### La partición: `last_out`, no `timestamp`

El benchmark publica dos particiones del mismo 5-core. Se midieron las dos: tienen
exactamente las mismas 511 836 interacciones, pero reparten distinto.

| Partición | Entrenamiento | Validación | Prueba | Usuarios en prueba |
|---|---:|---:|---:|---:|
| `last_out` | 396 958 | 57 439 | 57 439 | 57 439 (todos) |
| `timestamp` | 427 957 | 41 284 | 42 595 | 13 198 |

`timestamp` corta en dos fechas globales (entrenamiento hasta el 11-08-2021, validación
hasta el 16-07-2022, prueba después), así que tres de cada cuatro usuarios no tienen
ninguna compra en la prueba. La evaluación que pide #79 deja fuera **la última compra de
cada usuario** como prueba y la anterior como validación: eso es `last_out`. Al preparar se
comprueba que cada usuario tenga exactamente una compra en validación y una en prueba, que
ninguna compra de entrenamiento sea posterior a la de validación ni esta a la de prueba, y
que todo usuario y todo producto siga teniendo al menos 5 interacciones; si algo no se
cumple, el comando falla en vez de escribir un dataset distinto del publicado.

## Qué deja

```
<data-dir>/amazon_musical_instruments/
  raw/                 los cuatro archivos publicados, tal cual
  images/<xx>/<sha256>.jpg
  images.jsonl         qué pasó con cada URL de foto (para reanudar)
  products.csv
  users.csv
  interactions.csv
  stores.csv
  inventory.csv
  manifest.json        fuentes, parámetros, conteos, y filas, bytes y SHA-256 de cada CSV
```

Los CSV tienen cabecera, van en UTF-8 y un valor que falta queda vacío, que es como el
gestor los carga con `CREATE TABLE ... FROM FILE`.

| Archivo | Columnas |
|---|---|
| `products.csv` | `product_id` (el `parent_asin`), `title`, `brand`, `department`, `category`, `category_path`, `price`, `average_rating`, `rating_count`, `features`, `description`, `image` (ruta relativa a la carpeta del dataset), `image_url` |
| `users.csv` | `user_id`, `interactions` |
| `interactions.csv` | `user_id`, `product_id`, `rating`, `timestamp_ms`, `purchased_at` (ISO 8601 en UTC), `split` (`train`, `valid` o `test`) |
| `stores.csv` | `store_id` (id de OpenStreetMap), `name`, `kind`, `location` (`POINT(lat, lon)`) |
| `inventory.csv` | `store_id`, `product_id`, `quantity` |

`interactions.csv` va ordenado por fecha (y, en un empate, por usuario y producto); los
demás, por su clave.

### Fichas

Las fichas traen de todo, y se aplanan sin inventar nada:

- **Precio**: un número positivo, o vacío. Un texto que es solo un número (`"$1,299.00"`)
  se convierte; uno como `"from 23.99"` es una cota, no un precio, y queda vacío.
- **Categorías**: `category_path` es el árbol completo (`Musical Instruments > Guitars >
  Electric Guitars`); `department`, el primer nivel bajo la raíz; `category`, la hoja.
  Algunas fichas empiezan sin la raíz (`Instrument Accessories > …`), otras cuelgan
  promociones del árbol («Prime Card Bonus») y otras no traen categoría: se conservan tal
  cual, con los campos vacíos cuando no hay nada.
- **Textos**: `features` son las viñetas separadas por ` | `; `description`, los párrafos
  de la descripción. Los espacios se normalizan y un párrafo repetido se queda una vez.
- **Marca**: el campo `store` de la ficha, o `details.Brand` si no viene.

### Fotos

Una por producto: la variante `MAIN` (o la primera, si ninguna lo es). Amazon publica cada
foto en `large` (unos 500 px de lado mayor y 28 kB) y en `hi_res` (unos 1 500 px y
170 kB, seis veces más); se baja **la menor que cubre `--max-side`**, así que con los
512 px por omisión, `large`. Cada foto se gira según su EXIF, se aplana sobre blanco si
tiene transparencia, se reduce (nunca se agranda) a 512 px de lado mayor y se recodifica
como JPEG RGB de calidad 85, sin metadatos. 512 px sobran para SIFT y para los 224 px que
ve CLIP (#80).

El archivo se llama como el SHA-256 de sus bytes: dos productos con la misma foto
comparten archivo (**deduplicación por contenido**), y comprobar que un archivo está sano
es volver a hashearlo. Solo se aceptan URL `http(s)`. Las descargas son `--workers` a la
vez (8), y cada foto se reintenta hasta 4 veces ante un corte, un 429 o un 5xx, esperando
2, 4 y 8 s. Una foto que no se pudo bajar **no detiene nada**: queda anotada con su motivo
y el producto queda sin foto (`image` vacía).

### Tiendas e inventario

Las tiendas son lugares **reales** de OpenStreetMap en Lima, los de la Parte 2. El extracto
de Geofabrik no distingue una tienda de música, así que se toman, con nombre, los rubros
donde en Lima se venden instrumentos, audio y accesorios: tiendas por departamentos,
centros comerciales, tiendas de cómputo, de video y tiendas generales.

**El inventario es lo único sintético del dataset.** Cada tienda tiene en stock 1 000
productos distintos, muestreados sin reemplazo con probabilidad proporcional a sus compras
de entrenamiento más uno (Efraimidis y Spirakis), con entre 1 y 12 unidades. Los pesos
salen solo del entrenamiento, para que el inventario no le cuente al recomendador nada de
la validación ni de la prueba. Cada tienda usa su propio generador, sembrado con la
semilla y su id: la misma semilla da el mismo inventario, y quitar una tienda no cambia el
de las demás.

## Conteos medidos

Medido el 18-11-2026 con los parámetros por omisión, en un MacBook con Python 3.12 y una
conexión doméstica. Las cifras salen de `manifest.json`, que el comando escribe en cada
ejecución.

| | Filas |
|---|---:|
| productos | 24 587 |
| usuarios | 57 439 |
| compras de entrenamiento | 396 958 |
| compras de validación | 57 439 |
| compras de prueba | 57 439 |
| compras en total | 511 836 |
| tiendas | 209 |
| filas de inventario | 209 000 |

Todos los productos del 5-core tienen ficha (ninguno queda fuera). De los 24 587:

- **fotos**: 24 580 tienen URL (7 no traen ninguna imagen) y las 24 580 se bajaron, **sin
  ningún fallo**. Se pidieron 24 309 URL distintas (271 productos repiten la URL de otro) y
  quedaron **24 308 archivos**: 272 productos comparten foto con otro (deduplicados), 271
  por URL y uno por contenido. Las fotos ocupan 734 MB, unos 30 kB cada una;
- **precio**: 16 482 lo traen; 8 105 no (8 103 nulos, un `0.0` y un `"from 23.99"`);
- **categoría**: 779 sin ninguna; 6 941 sin descripción, 91 sin marca y 3 sin título;
- **departamentos** con más productos: Instrument Accessories (11 519), Live Sound & Stage
  (2 710), Microphones & Accessories (2 284), Amplifiers & Effects (1 745), Studio
  Recording Equipment (1 528), Guitars (1 066) y Drums & Percussion (834).

Las 209 tiendas son 72 centros comerciales, 63 tiendas por departamentos, 62 tiendas de
cómputo, 6 tiendas de video y 6 tiendas generales. Su inventario reúne 1 359 506 unidades
de 23 910 productos distintos. Los 677 que no están en ninguna tienen en promedio 3,7
compras de entrenamiento (como mucho 17); los que sí, 16,5.

| En disco | Bytes |
|---|---:|
| `raw/` (los cuatro archivos publicados) | 661,6 MB |
| `images/` | 734,1 MB |
| los cinco CSV | 89,6 MB |
| `images.jsonl` | 4,9 MB |
| **total** | **1,49 GB** |

| CSV | Bytes |
|---|---:|
| `products.csv` | 34,6 MB |
| `users.csv` | 1,8 MB |
| `interactions.csv` | 48,1 MB |
| `stores.csv` | 16,5 kB |
| `inventory.csv` | 5,1 MB |

**Tiempo.** La primera ejecución tardó **959 s** (16 min), casi todo en las fotos (unas 30
por segundo con 8 descargas a la vez); las fichas ya estaban bajadas de una prueba
anterior, así que esa cifra no incluye sus 632 MB. Su memoria máxima fue de 414 MB. La
segunda ejecución tardó **10,6 s**: no pidió nada a la red, no añadió ninguna línea a
`images.jsonl` y dejó los cinco CSV y el manifiesto idénticos byte a byte.

## Licencias

- Amazon Reviews 2023: publicado por el McAuley Lab (UCSD) para investigación; las fichas
  y las fotos son de Amazon.com.
- Lugares de OpenStreetMap: ODbL, © colaboradores de OpenStreetMap.
