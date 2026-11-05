# Índices de vectores: lo común

Los índices de la Parte 4 guardan vectores de dimensión fija —los histogramas TF-IDF de
cada imagen o audio— y responden a una sola pregunta: **los k vectores más parecidos a una
consulta**. Hay tres, con la misma interfaz (`VectorIndex`):

| Índice | Carpeta | Estructura de FAISS | Búsqueda |
|---|---|---|---|
| Exacto | aquí, [`flat.py`](flat.py) | `IndexIDMap2(IndexFlat)` | compara contra todos |
| IVF | [`../ivf/`](../ivf/README.md) | `IndexIVFFlat` | solo las listas más cercanas |
| HNSW | [`../hnsw/`](../hnsw/README.md) | `IndexHNSWFlat` | recorre un grafo por niveles |

La estructura de cada índice la pone FAISS (el profesor permitió librerías para IVF y HNSW
en esta parte). Lo de esta carpeta es lo que el gestor necesita alrededor: identificadores
que son direcciones de filas, filtros, una búsqueda que no se queda corta, borrado,
persistencia y la forma física para inspeccionarla.

## Métricas ([`metrics.py`](metrics.py))

| Métrica | En SQL | Puntaje (`SIMILARITY_SCORE()`) | En FAISS |
|---|---|---|---|
| Euclidiana | `euclidean`, `euclidiana` | `1 / (1 + ‖q - v‖)` | `METRIC_L2` (distancia al cuadrado) |
| Producto punto | `dot`, `producto_punto` | `q · v` | `METRIC_INNER_PRODUCT` |
| Coseno | `cosine`, `coseno` | `q · v / (‖q‖ ‖v‖)` | producto interno entre vectores normalizados |

Mayor es siempre más parecido, así que `ORDER BY score DESC` vale para las tres. El coseno
normaliza los vectores al guardarlos y la consulta al buscar; un vector cero se queda en
cero y su coseno con cualquiera es 0. `VectorMetric.similarity` es la definición escrita
con numpy: los tests comparan contra ella lo que devuelve cada índice.

## Identificadores ([`ids.py`](ids.py))

FAISS identifica cada vector con un entero de 64 bits, y la dirección de una fila
(página de 32 bits, ranura de 16) cabe entera: `vector_id_of(RecordId)` y `record_id_of`.
El índice devuelve directamente dónde está la fila.

## La búsqueda ([`base.py`](base.py))

`search(consulta, k, allowed=None, effort=None)` devuelve hasta k `VectorHit` (identificador
y puntaje) de mayor a menor, y un `VectorSearchStats` para el plan: distancias calculadas,
esfuerzo final (listas sondeadas o `ef`), pasadas y si fue exacta.

- **No se queda corta.** IVF solo mira las listas que sondea, y HNSW con un filtro recorre
  nodos que el filtro descarta: las dos pueden devolver menos de k aunque existan. Si
  faltan, la búsqueda se repite con el doble de esfuerzo hasta encontrarlas o agotar el
  índice (todas las listas, o `ef` igual al número de nodos), como los *iterative scans*
  de pgvector 0.8. Si ni así, compara contra todos los candidatos.
- **Filtros.** `allowed` son las filas que deja pasar el `WHERE`. FAISS las recibe como un
  `IDSelector` y filtra durante la búsqueda, no después: el resultado son k filas que
  cumplen el filtro, no las k más cercanas menos las que no lo cumplen.
- **Filtro con pocas filas → exacto.** Si el filtro deja `vector_exact_filter_rows` filas o
  menos (1 000 por omisión), se comparan todas: cuesta lo mismo que las ~1 000 distancias
  que calcula un recorrido de HNSW, y es exacto. Qdrant y Weaviate hacen lo mismo con su
  umbral de búsqueda completa.

## Persistencia

`save(ruta)` escribe un archivo `.npz` sin pickle y lo reemplaza de una vez (temporal,
`fsync` y renombrado), así que un corte a mitad deja el índice anterior entero:

| Arreglo | Contenido |
|---|---|
| `metadata` | JSON: versión del formato, tipo de índice, métrica, dimensión y parámetros |
| `faiss` | la estructura de FAISS (`serialize_index`) |
| `node_rows`, `tombstones` | solo HNSW: la fila de cada nodo y su lápida |

`Clase.load(ruta)` lo abre y lanza `VectorIndexFileError` si falta, está dañado o es de
otro tipo de índice. Quien lo usa puede entonces reconstruirlo desde los vectores, que son
la fuente de verdad.

## Errores

| Error | Cuándo |
|---|---|
| `InvalidVectorError` | un vector o la consulta no tiene la dimensión del índice, o trae NaN o infinito |
| `UnknownVectorMetricError` | `METRIC = ...` no es ninguna de las tres |
| `VectorIndexFileError` | el archivo del índice falta, está dañado o es de otro índice |
| `DuplicateVectorIdError` | se añade una fila que ya está (un fallo de quien llama, no del usuario) |
