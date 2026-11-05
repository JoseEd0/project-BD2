# Comparación experimental

Todo lo que se reporta se **mide**. Cada script recibe sus parámetros por línea de comandos
—ningún tamaño ni número de repeticiones está escrito dentro del código—, imprime una tabla
Markdown y guarda los resultados crudos en [`results/`](results/). De esos JSON salen las
gráficas de [`figures/`](figures/) y todas las tablas de este documento.

```bash
.venv/bin/python -m benchmarks.storage_benchmark --sizes 1000 10000 100000 --queries 100
.venv/bin/python -m benchmarks.index_benchmark   --sizes 1000 10000 100000 --queries 100
.venv/bin/python -m benchmarks.spatial_benchmark --sizes 1000 10000 100000 --queries 100 \
    --points-file data/datasets/lugares_peru.csv
.venv/bin/python -m benchmarks.text_analyzer_benchmark --repetitions 5 \
    --input Proyecto_Integrador_BD2.md docs/latex/informe/*.tex
.venv/bin/python -m benchmarks.text_benchmark --corpus data/datasets/esci_es \
    --sizes 1000 10000 100000 --repetitions 5
.venv/bin/python -m benchmarks.plot              # redibuja todas las gráficas
```

### Con Docker

Los mismos comandos corren sin instalar Python ni PostgreSQL, en el perfil `bench` de
[`compose.yaml`](../compose.yaml):

- **`postgres`** es PostgreSQL 17 con PostGIS y pgvector ([`docker/postgres/Dockerfile`](../docker/postgres/Dockerfile)),
  para `linux/amd64` y `linux/arm64`. No publica ningún puerto.
- **`bench`** es la imagen del motor con los extras `postgres`, `plots`, `datasets` y `usearch`, y con
  `BENCH_POSTGRES_DSN` apuntando a `postgres`. Monta `benchmarks/` y `data/datasets/` desde
  el repositorio, así que los JSON, las gráficas y los datos descargados quedan en su sitio.

```bash
docker compose --profile bench run --rm bench python demos/descargar_lugares.py \
    --salida data/datasets/lugares_peru.csv
docker compose --profile bench run --rm bench python -m benchmarks.esci.download \
    --salida data/datasets/esci_es
docker compose --profile bench run --rm bench python -m benchmarks.esci.subsets \
    --corpus data/datasets/esci_es --sizes 1000 10000 100000 \
    --queries-per-bucket 50 --max-judged-share 0.5 --seed 20261104

docker compose --profile bench run --rm bench python -m benchmarks.spatial_benchmark \
    --sizes 1000 10000 100000 --queries 100 --points-file data/datasets/lugares_peru.csv
docker compose --profile bench run --rm bench python -m benchmarks.text_benchmark \
    --corpus data/datasets/esci_es --sizes 1000 10000 100000 --repetitions 5
docker compose --profile bench run --rm bench python -m benchmarks.plot

docker compose --profile bench down       # apaga PostgreSQL
```

- **Las cifras publicadas se midieron fuera de Docker**, con PostgreSQL 17.10 instalado en el
  equipo. El contenedor usa la configuración por omisión de PostgreSQL, igual que esa
  instalación: `shared_buffers` 128 MB, `work_mem` 4 MB y `maintenance_work_mem` 64 MB.
- **Los tiempos dentro de Docker no se comparan con los publicados:** en macOS y Windows, el
  contenedor corre en una máquina virtual. El JSON guarda la versión del servidor
  (`postgres_server_version`), para saber de dónde salió cada medida.
- **En Linux,** conviene agregar `--user "$(id -u):$(id -g)"` a `run`, para que los archivos
  que escribe el contenedor en `benchmarks/` y `data/` queden a nombre del usuario.
- **Si los datos están en otra carpeta,** `MINIGESTOR_DATASETS_DIR` cambia la que se monta
  como `data/datasets/`.

| Argumento | Qué controla |
|---|---|
| `--sizes` | tamaños del conjunto de datos |
| `--queries` | consultas o borrados por medida |
| `--seed` | semilla del generador; misma semilla, mismos datos |
| `--page-size` | tamaño de página del motor (almacenamiento e índices) |
| `--radii-km`, `--neighbours` | radios y valores de `k` del experimento espacial |
| `--points-file` | CSV con una columna `POINT` del que tomar puntos reales (experimento espacial) |
| `--postgres-dsn` | conexión al PostgreSQL contra el que se mide GiST o GIN |
| `--corpus` | carpeta del corpus ESCI con sus subconjuntos (experimento de texto) |
| `--k`, `--k1`, `--b` | el `k` del top-k, de Precision@k y de Recall@k, y los parámetros de BM25 |
| `--postgres-configuration`, `--ts-rank-normalization` | diccionario de PostgreSQL y normalización de `ts_rank_cd` |
| `--input`, `--repetitions`, `--language` | textos, pasadas calientes e idioma de la medida del analizador de texto ([resultado](../src/text/README.md#velocidad-medida)); `--repetitions` y `--language` también en el de texto |
| `--queries` | en el experimento de texto, consultas por cubeta (por omisión, todas las del subconjunto) |
| `--output` | dónde dejar el JSON |

| Experimento | Qué compara | Gráficas |
|---|---|---|
| [1. Gestión de archivos](#1-gestión-de-archivos--heap-vs-secuencial) | heap file vs archivo secuencial | [almacenamiento](figures/almacenamiento.png) |
| [2. Indexación](#2-indexación--b-agrupado-vs-b-no-agrupado-vs-hash-extendible) | B+ agrupado vs B+ no agrupado vs hash extendible | [índices](figures/indices.png) |
| [3. Datos espaciales](#3-datos-espaciales--búsqueda-secuencial-vs-r-tree-vs-gist) | búsqueda secuencial vs R-Tree vs GiST de PostgreSQL | [radio](figures/espacial-radio.png), [k-NN](figures/espacial-knn.png), [índice](figures/espacial-indice.png), [nodos](figures/espacial-nodos.png) |
| [4. Búsqueda de texto](#4-búsqueda-de-texto--tf-idf--coseno-vs-bm25-vs-gin) | TF-IDF + coseno vs BM25 vs GIN de PostgreSQL | [consultas](figures/texto-consultas.png), [relevancia](figures/texto-relevancia.png), [índice](figures/texto-indice.png) |

**Cómo leer las gráficas.** Cada una es una fila de paneles, uno por operación, con el
número de filas en horizontal y una línea por técnica. Los dos ejes son logarítmicos: los
tamaños van de 1 000 a 100 000 y los tiempos cubren cuatro órdenes de magnitud, así que en
escala lineal solo se vería la técnica más lenta. En un eje logarítmico **la pendiente dice
cómo escala** la técnica: una línea plana no depende del tamaño y una a 45° crece en
proporción a él. El número junto al último punto es el valor con 100 000 filas. La
precisión y el recall, que son fracciones de 0 a 1, van en barras sobre un eje lineal.

## 1. Gestión de archivos — heap vs secuencial

`storage_benchmark.py` mide inserción, búsqueda y borrado por clave primaria, espacio en
disco y reorganización. Cada búsqueda y cada borrado es una operación aparte, como la de
un `WHERE id = k`: el heap, sin índice, recorre el archivo hasta dar con la clave. La
reorganización no se fuerza: se sigue borrando hasta que el desperdicio pasa del umbral
del 30 % y se mide el borrado que la dispara.

### Resultados medidos

Ejecución con `--sizes 1000 10000 100000 --queries 100`, páginas de 4 KB
(macOS, Python 3.12). Las búsquedas y los borrados son el total de 100 operaciones.

![Heap file frente a archivo secuencial: inserción, búsqueda y borrado por clave, espacio y reorganización](figures/almacenamiento.png)

En los paneles de búsqueda y de borrado la línea del heap sube a 45° —crece en proporción
al tamaño— y la del secuencial es casi plana.

| Operación | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| Inserción (ms) | heap | **3.59** | **37.5** | **373** |
|  | secuencial | 23.9 | 273 | 3 182 |
| Búsqueda por clave (ms) | heap | 27.6 | 262 | 2 724 |
|  | secuencial | **4.32** | **4.73** | **5.63** |
| Borrado por clave (ms) | heap | 26.2 | 243 | 2 561 |
|  | secuencial | **4.79** | **5.02** | **5.48** |
| Espacio (KiB) | heap | **64.0** | **576** | **5 720** |
|  | secuencial | 104 | 960 | 9 528 |
| Reorganización (ms) | secuencial | 2.08 | 16.9 | 164 |

### Conclusiones

**Por clave, el secuencial gana en todos los tamaños.** El heap recorre de media la mitad
del archivo por cada clave, así que su tiempo crece lineal (27.6 → 262 → 2 724 ms
para 100 búsquedas), mientras que el secuencial hace una búsqueda binaria sobre sus páginas
y casi no se mueve (4.32 → 4.73 → 5.63 ms). Ya con 1 000 filas es
6.4 veces más rápido, y con 100 000 **busca 484 veces más
rápido**. Borrar cuesta casi lo mismo que buscar en las dos estructuras, porque es
encontrar la fila y marcarla: con 100 000 filas el secuencial borra 468 veces más
rápido.

El precio del orden se paga al escribir: insertar cuesta **8.5 veces más** en el
secuencial, porque cada inserción localiza la página y desplaza bytes dentro de ella, y cada
tanto se dispara una reorganización. También ocupa **1.7 veces más espacio**, y eso es
deliberado: el factor de llenado del 80 % deja hueco para que las inserciones futuras quepan
en su sitio en vez de irse al desbordamiento.

**La reorganización es lineal y poco frecuente.** Con 100 000 filas reescribir el archivo
tomó 164 ms, unos 2.2 µs por cada uno de los 75 055 registros que
quedaban, y solo ocurre cuando las lápidas y el desbordamiento pasan del 30 % de lo
guardado.

**Cuándo usar cada uno:** heap si se escribe mucho y se consulta siempre por índice;
secuencial si se consulta por clave o por rango y las escrituras son la minoría.

### Resumen: ventajas y desventajas

| | Heap file | Archivo secuencial |
|---|---|---|
| **Ventajas** | Inserción `O(1)`: va directo a la cabeza de la lista de libres · el menor espacio en disco · reutiliza los huecos de los borrados sin ningún trabajo diferido · sin coste oculto | Búsqueda y borrado por clave `O(log P)`: 484× más rápido con 100 000 filas · rangos y recorrido ordenado gratis, sin ordenar nada · el orden se mantiene siempre disponible |
| **Desventajas** | Búsqueda y borrado por valor `O(P)`: hay que recorrer el archivo · un rango o un `ORDER BY` obligan a ordenar aparte · inútil sin un índice encima | Inserción 8.5× más lenta: localiza la página y desplaza bytes · 1.7× más espacio por el factor de llenado del 80 % · reorganizaciones que congelan el archivo cuando el desperdicio pasa del 30 % · el desbordamiento degrada la búsqueda hasta que se reorganiza |
| **Se elige cuando** | Carga masiva, tablas que siempre se consultan por índice, orden de lectura irrelevante | Consultas por rango o por clave frecuentes, escrituras minoritarias, necesidad de recorrer en orden |

## 2. Indexación — B+ agrupado vs B+ no agrupado vs hash extendible

`index_benchmark.py` mide construcción, igualdad, rango, recorrido ordenado, espacio
adicional y un ciclo de borrados e inserciones sobre el 10 % de las filas. Las tres
técnicas hacen el mismo trabajo: guardar las filas y encontrarlas. El B+ agrupado las
guarda en sus hojas; el no agrupado y el hash guardan direcciones y tienen **su propio
heap**, así que su construcción y su ciclo de borrados e inserciones incluyen escribir y
borrar las filas en él. Sus búsquedas **incluyen el salto al heap**, y el JSON guarda
además la misma búsqueda sin él (`solo_indice_ms`) para separar los dos costes.

### Resultados medidos

Mismos parámetros. Las consultas son el total de 100.

![B+ agrupado, B+ no agrupado y hash extendible: construcción, igualdad, rango, recorrido ordenado, espacio e inserciones y borrados](figures/indices.png)

En los paneles de rango y de recorrido ordenado falta una línea: el hash no puede hacer
ninguna de las dos cosas.

| Operación | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| Construcción (ms) | B+ agrupado | 20.4 | 302 | 4 428 |
|  | B+ no agrupado | 179 | 2 094 | 25 920 |
|  | hash extendible | **11.1** | **135** | **1 317** |
| Igualdad (ms) | B+ agrupado | **1.20** | **3.04** | **4.06** |
|  | B+ no agrupado | 12.3 | 12.3 | 16.1 |
|  | hash extendible | 4.58 | 3.48 | 4.29 |
| Rango (ms) | B+ agrupado | **2.49** | **4.48** | **5.47** |
|  | B+ no agrupado | 20.9 | 21.7 | 27.0 |
| | hash extendible | — | — | **no soportado** |
| Recorrido ordenado (ms) | B+ agrupado | **0.25** | **2.50** | **31.8** |
|  | B+ no agrupado | 1.79 | 18.9 | 199 |
| | hash extendible | — | — | **no soportado** |
| Espacio (KiB) | B+ agrupado | 108 | 956 | 9 356 |
|  | B+ no agrupado | **24.0** | **220** | **2 040** |
|  | hash extendible | 28.0 | 264 | 2 056 |
| Inserción/borrado (ms) | B+ agrupado | **4.75** | 87.2 | 1 129 |
|  | B+ no agrupado | 44.4 | 478 | 5 047 |
|  | hash extendible | 5.32 | **45.0** | **531** |

Las celdas «no soportado» **no son un cero de rendimiento, son una limitación de la
técnica**: el hash destruye el orden, así que no hay forma de resolver un rango ni un
`ORDER BY` con él.

### Conclusiones

**En una búsqueda por igualdad cuesta el índice, no el salto al heap.** El agrupado
responde 100 igualdades en 4.06 ms con 100 000 filas y el hash en 4.29 ms; sin ir al heap, el hash
tarda 4.00 ms, lo mismo que el agrupado. El salto a la fila es como mucho un 7 % del tiempo en
los dos índices secundarios: una lectura de una página que casi siempre está en el buffer
pool. Lo que pesa es leer la cubeta o el nodo y decodificar sus entradas en Python. Por eso
el B+ no agrupado es el más lento (16.1 ms, 16.0 sin el heap): sus claves compuestas
`(valor, dirección)` llenan nodos de unas 300 entradas, y cada nodo que lee se decodifica
entero.

**El salto al heap sí pesa cuando hay muchos resultados.** En el recorrido ordenado de
100 000 filas el agrupado tarda 31.8 ms y el no agrupado 199 ms, 6.3 veces más. De esos,
79.0 ms son recorrer sus propias hojas y 120 ms, 100 000 lecturas sueltas del heap en el
orden de la clave y no en el físico.

**El espacio va al revés.** El agrupado ocupa 9 356 KiB porque guarda las filas; los otros
dos rondan 2 040 KiB porque solo guardan direcciones. Por eso solo puede haber un índice
agrupado por tabla y tantos secundarios como haga falta.

**El hash es el más rápido para escribir.** Construye en 1.3 s contando su heap, 20 veces menos
que el B+ no agrupado, porque insertar es calcular la cubeta y escribir en ella. En el ciclo
de borrados e inserciones también gana con 10 000 y 100 000 filas (0.5 s contra 1.1 s del
agrupado con 100 000). Con 1 000 filas el agrupado es algo más rápido (4.75 contra
5.32 ms): el hash paga al borrar lo que cuesta mantenerse compacto, porque funde cada
cubeta holgada con su gemela y reduce el directorio, y con pocas filas eso es casi todo.

**Cuándo usar cada uno:** agrupado para la clave primaria de la tabla; hash para columnas
que solo se consultan por igualdad; B+ no agrupado cuando además hacen falta rangos sobre
una columna que no es la de agrupamiento.

### Resumen: ventajas y desventajas

| | B+ agrupado | B+ no agrupado | Hash extendible |
|---|---|---|---|
| **Ventajas** | La fila está en la hoja: **sin salto al heap** · el mejor en igualdad (4.06 ms) y en rango (5.47 ms) · recorrido ordenado 6.3× más rápido que el no agrupado · un solo archivo que es índice y datos a la vez | Ocupa 4.6× menos que el agrupado porque solo guarda direcciones · **varios por tabla**, uno por columna que interese · admite rangos y orden · admite valores repetidos con clave compuesta `(valor, dirección)` | Igualdad en `O(1)`: dos accesos, uno al directorio y uno a la cubeta, sea cual sea el tamaño · **construcción 20× más rápida** que el B+ no agrupado (1.3 s vs 25.9 s) · crecer solo duplica el directorio, sin tocar las cubetas · al borrar funde cubetas y encoge el directorio |
| **Desventajas** | Ocupa 9 356 KiB porque guarda las filas · **solo puede haber uno por tabla**: los datos solo pueden estar ordenados de una forma · las hojas son grandes, así que caben menos entradas por página y el árbol crece en altura | Paga un **salto al heap por cada resultado**, que pesa cuando son muchos: con 100 000 filas el recorrido ordenado tarda 199 ms frente a 31.8 ms del agrupado · el más lento en construirse por el coste de decodificar claves compuestas | **No admite rangos ni `ORDER BY`**: el hash destruye el orden · con pocas filas, borrar es más caro que en el B+ porque funde cubetas y encoge el directorio · claves muy repetidas no se pueden repartir y degeneran en cadenas de desbordamiento |
| **Se elige cuando** | Es la clave primaria de la tabla y las consultas por ella dominan | Hace falta un índice secundario y las consultas incluyen rangos u orden | La columna solo se consulta por igualdad y se carga mucho de una vez |

## Cómo leer los tiempos de construcción

El B+ no agrupado tarda 25.9 s en construirse con 100 000 filas: 5.9 veces más que el
agrupado y 20 veces más que el hash, y los tres cuentan ya el escribir las filas: el
agrupado en sus hojas, los otros dos en su heap. El motivo está en la implementación, no en
la estructura. Su clave es compuesta `(valor, dirección)`, ocupa 14 bytes en vez de 8, y por
eso en cada nodo caben casi 300 entradas en vez de 59. Cada vez que se lee un nodo,
**deserializar decodifica esas 300 claves compuestas en Python**, aunque la búsqueda binaria
solo vaya a mirar ocho.

Es el precio de tener los nodos como objetos legibles en vez de operar directamente sobre
los bytes de la página. La alternativa —comparar claves serializadas sin decodificarlas—
sería más rápida y bastante menos clara. Merece la pena contarlo como lo que es: **una
consecuencia medida de una decisión de diseño**, no un defecto de la estructura.

## 3. Datos espaciales — búsqueda secuencial vs R-Tree vs GiST

`spatial_benchmark.py` responde las mismas consultas, sobre los mismos puntos, con tres
técnicas:

| Técnica | Qué es |
|---|---|
| **búsqueda secuencial** | el motor sin índice: recorre la tabla y mide la distancia a cada punto |
| **R-Tree** | el motor con el índice `RTREE` de [`src/index/rtree/`](../src/index/rtree/README.md) |
| **GiST (PostgreSQL)** | PostgreSQL 17 con PostGIS 3.6, columna `geography` e índice GiST |

Las consultas son las del enunciado: por radio de 1, 5 y 10 km, y los `k` vecinos más
cercanos con `k` = 10, 50 y 100. Las dos técnicas del motor se miden con el mismo SQL de
punta a punta (`SELECT id FROM puntos WHERE distancia(…) < r` y
`… ORDER BY distancia(…) LIMIT k`); GiST, con `ST_DWithin` y el operador `<->`.

**Los puntos son reales.** Con `--points-file` el experimento toma sus puntos de un CSV con
una columna `POINT`: aquí, los lugares con nombre del Perú en OpenStreetMap —colegios,
restaurantes, farmacias, bancos— que descarga
[`demos/descargar_lugares.py`](../demos/README.md). De ellos se sacan muestras de 1 000,
10 000 y 100 000 puntos distintos, y los centros de las consultas caen junto a puntos de la
muestra. Importa que sean reales: están repartidos como la gente, apretados en las ciudades
y con grandes zonas vacías, que es justo donde un índice espacial tiene algo que descartar.
Sin ese argumento se generan puntos sintéticos sobre Lima, que no necesitan descargar nada.

### Antes de medir: ¿responden lo mismo?

Un índice que acelera cambiando la respuesta no es una comparación. Por eso:

- el experimento **aborta** si el R-Tree devuelve filas distintas de las del recorrido;
- a PostGIS se le pide la distancia **sobre la esfera** (`ST_DWithin(…, false)`), el mismo
  modelo que Haversine, porque por omisión mide sobre el elipsoide y discrepa hasta un 0.5 %;
- cada medida de GiST anota cuántas de sus consultas coincidieron con el motor.

Resultado: **las 1 800 consultas coinciden, fila por fila** (100 de cada uno de los 6 tipos,
en los 3 tamaños), y PostgreSQL confirma con `EXPLAIN` que usa el índice en todas.

### Consultas por radio

![Consultas por radio: recorrido secuencial, R-Tree y GiST](figures/espacial-radio.png)

Tiempo medio por consulta, en milisegundos:

| Radio | Técnica | 1 000 | 10 000 | 100 000 | Filas devueltas (100 000) |
|---|---|---:|---:|---:|---:|
| 1 km | búsqueda secuencial | 3.52 | 33.8 | 338 | 64.1 |
|  | R-Tree | 0.27 | 0.42 | 0.96 | |
|  | GiST | **0.043** | **0.048** | **0.24** | |
| 5 km | búsqueda secuencial | 3.51 | 33.9 | 339 | 544 |
|  | R-Tree | 0.33 | 0.82 | 4.39 | |
|  | GiST | **0.042** | **0.063** | **0.48** | |
| 10 km | búsqueda secuencial | 3.55 | 34.3 | 339 | 1 577 |
|  | R-Tree | 0.42 | 1.40 | 11.1 | |
|  | GiST | **0.050** | **0.099** | **1.00** | |

### Vecinos más cercanos

![Vecinos más cercanos: recorrido secuencial, R-Tree y GiST](figures/espacial-knn.png)

Tiempo medio por consulta, en milisegundos:

| k | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| 10 | búsqueda secuencial | 5.82 | 70.2 | 1 171 |
|  | R-Tree | 0.33 | 0.44 | 0.57 |
|  | GiST | **0.059** | **0.058** | **0.12** |
| 50 | búsqueda secuencial | 5.85 | 71.1 | 1 173 |
|  | R-Tree | 0.54 | 0.70 | 0.86 |
|  | GiST | **0.095** | **0.097** | **0.19** |
| 100 | búsqueda secuencial | 5.94 | 73.7 | 1 170 |
|  | R-Tree | 0.79 | 1.14 | 1.31 |
|  | GiST | **0.12** | **0.14** | **0.26** |

### Por qué: los nodos que abre cada consulta

![Nodos del R-Tree que abre cada consulta, frente al total de nodos del árbol](figures/espacial-nodos.png)

El plan de ejecución dice cuántos nodos del R-Tree visitó cada búsqueda. Media sobre 100
consultas:

| Consulta | 1 000 puntos (7 nodos) | 10 000 (61 nodos) | 100 000 (606 nodos) |
|---|---:|---:|---:|
| radio 1 km | 2.00 | 2.20 | 4.30 |
| radio 5 km | 2.10 | 2.90 | 9.40 |
| radio 10 km | 2.30 | 3.80 | 16.9 |
| k-NN, k = 10 | 2.30 | 2.40 | 3.90 |
| k-NN, k = 50 | 2.90 | 3.20 | 4.80 |
| k-NN, k = 100 | 3.40 | 4.20 | 5.80 |

Esta es la gráfica que explica las dos anteriores. El árbol se multiplica por 87 (de 7 a
606 nodos) y el k-NN pasa de abrir 2.30 nodos a abrir 3.90.

### Construcción, espacio y memoria

![Tiempo de construcción, espacio del índice y memoria pico](figures/espacial-indice.png)

| Medida | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| Construcción (ms) | R-Tree, carga masiva (STR) | 3.46 | 40.6 | 544 |
|  | R-Tree, inserción uno a uno | 145 | 1 854 | 22 549 |
|  | GiST | **1.47** | **18.8** | **188** |
| Espacio del índice (KiB) | R-Tree, carga masiva | **32.0** | **248** | **2 428** |
|  | R-Tree, inserción uno a uno | 40.0 | 324 | 3 260 |
|  | GiST | 88.0 | 808 | 7 760 |
| Memoria pico al consultar (KiB) | búsqueda secuencial | 200 | 772 | 1 468 |
|  | R-Tree | **98.2** | **167** | **1 365** |

`CREATE INDEX … USING RTREE` usa la carga masiva; la inserción uno a uno es lo que cuesta
mantener el índice cuando las filas llegan después. La búsqueda secuencial no tiene índice
que construir ni que almacenar. La memoria es la de Python, medida con `tracemalloc`; la de
PostgreSQL no es comparable desde fuera y no se reporta.

### Conclusiones

**El recorrido secuencial crece con la tabla; el índice, con la respuesta.** La búsqueda
secuencial tarda lo mismo pida un radio de 1 km o de 10 (338 y 339 ms con 100 000 puntos),
porque siempre mide la distancia a todos. El R-Tree tarda 0.96 ms para 1 km y 11.1 ms para
10 km: lo que le cuesta son las 64.1 o las 1 577 filas que tiene que devolver.

**Cuanto más selectiva la consulta, más gana el índice.** Con 100 000 puntos, el R-Tree es
351 veces más rápido que el recorrido para 1 km, 77 veces para 5 km y 31 para 10 km,
donde cada consulta devuelve ya el 1.6 % de la tabla.

**En el k-NN la diferencia es de tres órdenes de magnitud.** Sin índice no hay forma de
saber cuáles son los 10 más cercanos sin medir los 100 000 y ordenarlos: 1 171 ms. El
R-Tree abre los nodos del más cercano al más lejano y se detiene al tener diez: 0.57 ms,
**2 042 veces menos**. Y casi no depende del tamaño: de 1 000 a 100 000 puntos el recorrido
se multiplica por 201 y el R-Tree por 1.7.

**GiST es entre 4.0 y 14 veces más rápido que el R-Tree, y escala igual.** La distancia entre
las dos líneas es constante en las gráficas: la misma pendiente, desplazada. GiST está
escrito en C y lleva décadas de optimización; este R-Tree es Python y deserializa cada nodo
que abre. Lo que el experimento compara es **el algoritmo**, y ahí las dos estructuras se
comportan igual y el recorrido no.

**La carga masiva es 41 veces más rápida que insertar y deja un árbol mejor.** 0.5 s
frente a 22.5 s con 100 000 puntos, 606 nodos en vez de 814 y un 26 % menos de espacio.
GiST construye en 0.2 s.

**El índice propio ocupa 3.2 veces menos que el de PostGIS:** 2 428 KiB frente a 7 760. Las
hojas de este R-Tree guardan el punto en 22 bytes; `geography` indexa cajas en tres
dimensiones.

**La memoria al consultar es parecida en las dos técnicas del motor** (1 468 KiB el recorrido y
1 365 KiB el R-Tree con 100 000 puntos). El R-Tree necesita tener a la vez las direcciones
que devuelve el índice, porque las ordena por página antes de ir al heap para leer cada
página una sola vez.

### Resumen: ventajas y desventajas

| | Búsqueda secuencial | R-Tree (propio) | GiST (PostgreSQL) |
|---|---|---|---|
| **Ventajas** | Sin índice que construir, almacenar ni mantener · coste predecible: siempre el mismo · suficiente en tablas pequeñas: 3.51 ms por radio con 1 000 puntos | k-NN en `O(log N + k)`: 0.57 ms con 100 000 puntos · por radio, hasta 351× más rápido que recorrer · el índice más compacto de los tres · una sola estructura para las dos métricas | El más rápido en todo, entre 4.0 y 14 veces · construye 100 000 puntos en 0.2 s · maduro, concurrente y con recuperación ante fallos |
| **Desventajas** | Crece en proporción a la tabla · el k-NN obliga a medir y ordenar todo: 1.2 s con 100 000 puntos · no aprovecha que la consulta sea selectiva | Hay que mantenerlo: insertar uno a uno cuesta 0.23 ms por punto · gana menos cuanto mayor es la parte de la tabla que devuelve la consulta | Es un sistema entero, no una estructura: hay que instalarlo, cargarlo y consultarlo por red · su índice ocupa 3.2× más |
| **Se elige cuando** | La tabla es pequeña (con 1 000 puntos responde en 3.51 a 5.94 ms) o la consulta devuelve una parte grande de las filas | Consultas selectivas —un barrio, los `k` más cercanos— sobre muchos puntos | Hay un PostgreSQL disponible y el rendimiento manda |

### Reproducirlo

```bash
.venv/bin/pip install -e ".[postgres,plots]"
.venv/bin/python demos/descargar_lugares.py --salida data/datasets/lugares_peru.csv
createdb minigestor_bench                       # el experimento crea ahí la extensión PostGIS
export BENCH_POSTGRES_DSN="dbname=minigestor_bench"
.venv/bin/python -m benchmarks.spatial_benchmark --sizes 1000 10000 100000 --queries 100 \
    --points-file data/datasets/lugares_peru.csv
```

Tarda unos 15 minutos, casi todos en el recorrido secuencial de 100 000 puntos. En
PostgreSQL solo usa tablas temporales, que desaparecen al cerrar la conexión. Sin
`BENCH_POSTGRES_DSN` mide las dos técnicas del motor y omite GiST; sin `--points-file`, usa
puntos sintéticos. El extracto de OpenStreetMap es el del día de la descarga, así que las
cifras de otra ejecución pueden diferir ligeramente de las publicadas.

El tiempo de GiST se mide desde el cliente e incluye el viaje por el socket local, que el
motor propio no paga porque corre en el mismo proceso. Aun así es el más rápido.

## 4. Búsqueda de texto — TF-IDF + coseno vs BM25 vs GIN

`text_benchmark.py` responde las mismas consultas, sobre los mismos productos, con tres
técnicas:

| Técnica | Qué es |
|---|---|
| **TF-IDF + coseno** | `Ranker.top_k` con SMART lnc.ltc sobre el índice invertido de [`src/index/inverted/`](../src/index/inverted/README.md) |
| **BM25** | el **mismo** índice y el mismo `Ranker`, con `k1` = 1.2 y `b` = 0.75 |
| **GIN (PostgreSQL)** | PostgreSQL 17.10: columna `tsvector` generada con `to_tsvector('spanish', texto)`, índice GIN sobre ella y orden con `ts_rank_cd` |

**El corpus es real.** Fichas de producto de Amazon en español, del Shopping Queries
Dataset (ESCI), con los subconjuntos de 1 000, 10 000 y 100 000 productos que describe
[`esci/README.md`](esci/README.md). Las consultas son búsquedas reales de la tienda, en
tres cubetas por su número de palabras (1, 3 y 5 o más): 8 por cubeta con 1 000 productos y
50 con 10 000 y 100 000. Un producto es **relevante** si un anotador lo calificó como exacto
(E); Precision@10 y Recall@10 se calculan contra esos juicios.

**Cómo se pregunta a cada uno.**

- El motor se mide a nivel del índice: `Ranker.top_k(consulta, método, 10)` sobre el índice
  abierto, sin pasar por el SQL. Un mismo índice sirve a los dos rankings, así que entre
  ellos solo cambia la fórmula.
- A PostgreSQL se le pregunta por SQL desde el cliente, con el viaje por el socket local
  incluido. La consulta es el **OR** de sus términos, como en el motor:
  `plainto_tsquery` analiza el texto igual que `to_tsvector` y une los lexemas con `&`, y
  el experimento cambia cada ` & ` por ` | ` (un lexema no contiene espacios, así que no
  se toca nada más). Se ordena con `ts_rank_cd(vector, consulta)` con la normalización por
  omisión (0), y a igual puntaje por el orden de carga, como el motor.
- PostgreSQL planifica cada consulta con su texto (`plan_cache_mode = force_custom_plan`):
  con un plan genérico la consulta deja de ser una constante y `ts_rank_cd` la vuelve a
  calcular por cada fila. Con `enable_seqscan = off` se mide el índice, y `EXPLAIN` confirma
  que se usa en las 324 consultas.
- Los dos tienen la misma caché propia: el buffer pool del motor son 2 048 páginas de
  4 KB (8 MiB) y la tabla temporal de PostgreSQL usa `temp_buffers` = 8 MB, el valor por
  omisión. Lo que no cabe, los dos lo leen de la caché de archivos del sistema.
- **Construir** empieza en los dos con los textos ya cargados: en el motor, una lista en
  memoria; en PostgreSQL, la tabla. Cuenta el análisis del texto y el índice: en el motor,
  `InvertedIndex.build`; en PostgreSQL, calcular la columna `tsvector` y `CREATE INDEX`.

Cada consulta se repite 5 veces y cada índice se construye 3; cuenta la vez más
rápida, porque el equipo era compartido y otro proceso solo puede sumar tiempo. Todas las
repeticiones están en el JSON. Apple M4 Pro, macOS, Python 3.12.

### Antes de medir: ¿qué candidatos ve cada uno?

Un candidato es un producto que contiene algún término de la consulta. Los dos analizadores
usan el stemmer Snowball del español, pero no cortan el texto igual, así que sus candidatos
no tienen por qué coincidir. El experimento compara los dos conjuntos de cada consulta:

| Productos | Candidatos idénticos | Términos idénticos | Jaccard medio | En los dos | Solo el motor | Solo GIN |
|---:|---:|---:|---:|---:|---:|---:|
| 1 000 | 15 / 24 | 24 / 24 | 0.98 | 1 914 | 148 | 35 |
| 10 000 | 48 / 150 | 135 / 150 | 0.90 | 111 866 | 29 408 | 4 568 |
| 100 000 | 37 / 150 | 135 / 150 | 0.89 | 1 103 340 | 294 767 | 45 072 |

Las raíces coinciden en 135 de las 150 consultas: lo que cambia es **dónde se corta una
palabra**. El motor corta entre letras y cifras y en los puntos; PostgreSQL deja «ps4»,
«5ghz» o «m.zuiko» como una sola palabra. Y las listas de stopwords no son iguales: «si» lo
es para el motor y no para PostgreSQL. Las consultas con más diferencia, con 100 000
productos:

| Consulta | Términos del motor | Términos de GIN | Solo el motor | Solo GIN |
|---|---|---|---:|---:|
| x10sdv | `10 sdv x` | `x10sdv` | 41 251 | 0 |
| nintengo 2ds new xl lapiz | `2 ds lapiz new ninteng xl` | `2ds lapiz new ninteng xl` | 33 334 | 7 |
| 200x50 rueda patinete sin cámara de aire | `200 50 air cam patinet rued x` | `200x50 air cam patinet rued` | 29 606 | 3 |
| ps4 terror vr | `4 ps terror vr` | `ps4 terror vr` | 23 678 | 0 |
| repetidor wifi 5ghz | `5 ghz repetidor wifi` | `5ghz repetidor wifi` | 20 126 | 0 |
| si tienes un papa mago | `mag pap` | `mag pap si` | 42 | 18 812 |

Cortar «ps4» en `ps` y `4` hace que la consulta encuentre todo producto con un 4. Por eso
el motor tiene más candidatos: con 100 000 productos, el 96 % de los de GIN están
también en el motor, y el 21 % de los del motor son solo suyos. La diferencia es del
analizador, no del índice.

### Tiempo de consulta

![Tiempo por consulta de TF-IDF, BM25 y GIN según el número de palabras](figures/texto-consultas.png)

Tiempo por consulta, en milisegundos:

| Palabras | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| 1 palabra | TF-IDF + coseno | 0.019 | **0.047** | 0.24 |
|  | BM25 | **0.019** | 0.049 | **0.23** |
|  | GIN | 0.082 | 0.096 | 0.64 |
| 3 palabras | TF-IDF + coseno | **0.062** | 0.17 | 1.24 |
|  | BM25 | 0.070 | **0.17** | **0.95** |
|  | GIN | 0.23 | 1.82 | 19.3 |
| 5 o más | TF-IDF + coseno | **0.087** | **0.36** | 2.99 |
|  | BM25 | 0.10 | 0.38 | **2.69** |
|  | GIN | 0.39 | 4.82 | 51.7 |

### Dónde se va el tiempo

El motor recorre las listas de postings de los términos y suma en un acumulador por
documento. PostgreSQL usa el GIN para saber qué filas coinciden, y luego lee el `tsvector`
de cada una y calcula `ts_rank_cd`. El `EXPLAIN ANALYZE` separa las dos partes:

| Palabras | Postings leídos (1 000) | (10 000) | (100 000) | Candidatos del motor (100 000) | Filas de GIN (100 000) | GIN: índice (ms) | GIN: ejecución completa (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 palabra | 9 | 176 | 1 620 | 1 464 | 235 | 0.009 | 0.39 |
| 3 palabras | 130 | 915 | 8 781 | 8 203 | 6 986 | 0.39 | 19.7 |
| 5 o más | 206 | 2 252 | 21 911 | 18 296 | 15 747 | 1.12 | 51.7 |

### Relevancia

![Precision@10 y Recall@10 de TF-IDF, BM25 y GIN por número de palabras](figures/texto-relevancia.png)

Precision@10, media por cubeta; en negrita, la mejor de cada tamaño:

| Productos | Técnica | 1 palabra | 3 palabras | 5 o más | Todas |
|---:|---|---:|---:|---:|---:|
| 1 000 | TF-IDF + coseno | **0.60** | 0.71 | **0.34** | 0.55 |
|  | BM25 | **0.60** | **0.76** | **0.34** | **0.57** |
|  | GIN | **0.60** | 0.62 | 0.21 | 0.48 |
| 10 000 | TF-IDF + coseno | **0.54** | 0.48 | 0.39 | 0.47 |
|  | BM25 | 0.53 | **0.57** | **0.44** | **0.52** |
|  | GIN | 0.49 | 0.30 | 0.15 | 0.32 |
| 100 000 | TF-IDF + coseno | 0.33 | 0.29 | 0.22 | 0.28 |
|  | BM25 | **0.33** | **0.43** | **0.29** | **0.35** |
|  | GIN | 0.32 | 0.10 | 0.03 | 0.15 |

Recall@10:

| Productos | Técnica | 1 palabra | 3 palabras | 5 o más | Todas |
|---:|---|---:|---:|---:|---:|
| 1 000 | TF-IDF + coseno | **0.46** | 0.52 | **0.67** | 0.55 |
|  | BM25 | **0.46** | **0.55** | 0.67 | **0.56** |
|  | GIN | **0.46** | 0.39 | 0.25 | 0.37 |
| 10 000 | TF-IDF + coseno | **0.42** | 0.39 | 0.45 | 0.42 |
|  | BM25 | 0.41 | **0.43** | **0.46** | **0.43** |
|  | GIN | 0.40 | 0.18 | 0.10 | 0.23 |
| 100 000 | TF-IDF + coseno | 0.26 | 0.24 | 0.30 | 0.27 |
|  | BM25 | **0.27** | **0.34** | **0.30** | **0.30** |
|  | GIN | 0.27 | 0.06 | 0.01 | 0.11 |

Con 100 000 productos cada consulta tiene en promedio 13.2 productos exactos, así que ni
un top 10 perfecto llega siempre a un Recall@10 de 1.

### Construcción, espacio y memoria

![Construcción, espacio en disco y memoria del índice invertido y de GIN](figures/texto-indice.png)

| Medida | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| Construcción (ms) | índice invertido (SPIMI) | 303 | 1 578 | 13 660 |
|  | GIN, total | **100** | **1 054** | **10 984** |
|  | GIN: columna `tsvector` | 79.0 | 816 | 8 402 |
|  | GIN: `CREATE INDEX` | 21.4 | 238 | 2 582 |
| Espacio del índice (KiB) | índice invertido (SPIMI) | 1 088 | 7 968 | 71 056 |
|  | GIN | **1 080** | **6 360** | **37 840** |
|  | GIN: columna `tsvector` | 1 136 | 11 641 | 117 420 |
| Memoria pico al construir (KiB) | índice invertido (SPIMI) | 3 976 | 17 978 | 24 934 |
| Memoria pico al consultar (KiB) | TF-IDF + coseno | 70.2 | 718 | 16 957 |
|  | BM25 | **46.1** | **503** | **10 754** |

El espacio del índice invertido son sus cuatro archivos: con 100 000 productos, léxico
4 624 KiB, términos 1 020, postings 61 696 y documentos 1 868. El de GIN es
`pg_relation_size` del índice; la columna `tsvector` va aparte porque está en la tabla,
pero el ranking la necesita. La memoria es la de Python, medida con `tracemalloc` en una
construcción aparte; la de PostgreSQL no se puede medir igual desde fuera y no se reporta.

### Conclusiones

**BM25 es el más relevante, en los tres tamaños.** Su Precision@10 media es 0.57, 0.52 y
0.35, frente a 0.55, 0.47 y 0.28 de TF-IDF. Con una palabra las tres técnicas casi
empatan (a lo sumo 0.040 de diferencia): con un solo término, ordenar es contar
cuántas veces aparece y en cuánto texto. La ventaja de BM25 está en las consultas largas:
con 100 000 productos gana por 0.14 con 3 palabras y por 0.07 con 5 o más. Satura la
frecuencia de cada término (`k1`), así que un producto no gana por repetir una palabra, y
pesa más el que contiene varios términos distintos.

**GIN no ordena: encuentra.** El índice dice qué filas contienen algún término; el orden lo
pone `ts_rank_cd`, que con la normalización por omisión no divide por la longitud del
documento, y un texto largo que repite los términos suma más. Con 10 000 productos su
Precision@10 con 3 palabras es 0.30 y con 100 000 baja a 0.10: 2.9 veces menos, cuando
BM25 baja 1.3 veces. Con 5 o más palabras queda en 0.03. Es la parte del
experimento que más depende de una opción: `--ts-rank-normalization` la cambia.

**El motor responde antes que PostgreSQL con 3 palabras o más**, y la tabla de arriba dice
por qué. Con 100 000 productos y 5 o más palabras, el GIN encuentra las
15 747 filas en 1.12 ms, el 2.2 % de la ejecución; el resto es leer el `tsvector` de cada fila
y puntuarla, unos 3.3 µs por fila. El motor tiene la frecuencia de cada término dentro
del posting y la longitud de cada documento en memoria: puntuar le cuesta unos
123 ns por posting con BM25 y 136 con TF-IDF. GIN es C y el motor Python, pero la
diferencia no está en el lenguaje sino en lo que cada uno guarda en el índice: GIN está
hecho para responder qué filas coinciden, no para ordenarlas. Con una palabra hay pocas filas
que leer y la distancia se acorta (2.8 veces con 100 000 productos, frente a
20 con 3 palabras).

**El tiempo del motor sigue a los postings que lee.** De 10 000 a 100 000 productos, una
consulta de 5 o más palabras lee 9.7 veces más postings y BM25 tarda 7.0 veces más: el
costo de una consulta es recorrer sus listas, y crece con ellas, no con el tamaño de la colección.

**GIN construye más rápido y ocupa menos.** Con 100 000 productos, PostgreSQL calcula la
columna y crea el índice en 11.0 s; el motor tarda 13.7 s, 1.2 veces más, la mayor parte
analizando el texto en Python (lo mide el [README del índice](../src/index/inverted/README.md#medido-con-esci)). El índice GIN ocupa 37.0 MiB y el invertido 69.4 MiB
(1.9 veces más): GIN comprime sus listas, y el motor guarda los postings sin
comprimir, 8 bytes cada uno, para decodificarlos en C; son el 87 % de sus archivos.
Pero el ranking de GIN necesita además la columna `tsvector`, que ocupa 115 MiB.

**La memoria de SPIMI está acotada.** Con 100 000 productos la construcción escribe
9 bloques y los mezcla en 2 pasadas; diez veces más documentos que con 10 000 cuestan
1.4 veces la memoria (17.6 MiB y 24.3 MiB).

### Resumen: ventajas y desventajas

| | TF-IDF + coseno | BM25 | GIN (PostgreSQL) |
|---|---|---|---|
| **Ventajas** | Sin parámetros que ajustar · la norma del documento se calcula al indexar y no cambia · empata con BM25 en consultas de una palabra | El más relevante: Precision@10 de 0.35 con 100 000 productos · satura la frecuencia y normaliza por longitud · el mismo índice y las mismas listas que TF-IDF | Construye 1.2 veces más rápido · índice 1.9 veces más chico · maduro, transaccional y concurrente |
| **Desventajas** | Pierde frente a BM25 en consultas largas: 0.22 contra 0.29 con 5 o más palabras · el idf solo pesa en la consulta | Dos parámetros (`k1`, `b`) que dependen del corpus · necesita la longitud de cada documento a mano en memoria | No ordena por sí mismo: `ts_rank_cd` relee el `tsvector` de cada fila, 19 veces más lento que BM25 con 5 o más palabras · con la normalización por omisión prefiere textos largos: Precision@10 de 0.15 · el ranking necesita además la columna `tsvector` (115 MiB) |
| **Se elige cuando** | Consultas cortas o un corpus donde no hay con qué ajustar `k1` y `b` | Búsqueda por relevancia en general, sobre todo con consultas de varias palabras | Hace falta filtrar por palabras dentro de un PostgreSQL y el orden importa poco |

### Reproducirlo

```bash
.venv/bin/pip install -e ".[postgres,plots,datasets]"
.venv/bin/python -m benchmarks.esci.download --salida data/datasets/esci_es
.venv/bin/python -m benchmarks.esci.subsets --corpus data/datasets/esci_es \
    --sizes 1000 10000 100000 --queries-per-bucket 50 --max-judged-share 0.5 --seed 20261104
export BENCH_POSTGRES_DSN="dbname=minigestor_bench"
.venv/bin/python -m benchmarks.text_benchmark --corpus data/datasets/esci_es \
    --sizes 1000 10000 100000 --repetitions 5 --build-repetitions 3
```

Tarda unos seis minutos, casi todo en construir el índice de 100 000 productos (tres veces,
y una cuarta con `tracemalloc`). El experimento no escribe los subconjuntos: si falta uno,
dice qué comando lo crea. En PostgreSQL solo usa tablas temporales. Sin
`BENCH_POSTGRES_DSN` mide TF-IDF y BM25 y omite GIN.

## Gráficas

`plot.py` lee los JSON de `results/` y redibuja todas las imágenes de `figures/`:

```bash
.venv/bin/pip install -e ".[plots]"
.venv/bin/python -m benchmarks.plot
```

Qué se dibuja está declarado en `FIGURES`, al principio del módulo: una figura por
comparación y un panel por operación. Los colores son tres tonos comprobados para que se
distingan también con daltonismo, y cada técnica lleva además su propio marcador (círculo,
cuadrado, triángulo), así que las gráficas se leen igual impresas en gris. Los valores de
todas las gráficas están en las tablas de este documento.

## Reproducibilidad

Misma semilla ⇒ mismos datos ⇒ resultados comparables entre ejecuciones. Los benchmarks
trabajan sobre un directorio temporal que se borra al terminar, así que no ensucian los
datos del gestor. Los JSON de `results/` y las imágenes de `figures/` se versionan junto al
código: son las cifras y las gráficas que cita este documento.
