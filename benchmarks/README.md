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
.venv/bin/python -m benchmarks.plot              # redibuja todas las gráficas
```

| Argumento | Qué controla |
|---|---|
| `--sizes` | tamaños del conjunto de datos |
| `--queries` | consultas o borrados por medida |
| `--seed` | semilla del generador; misma semilla, mismos datos |
| `--page-size` | tamaño de página del motor (almacenamiento e índices) |
| `--radii-km`, `--neighbours` | radios y valores de `k` del experimento espacial |
| `--points-file` | CSV con una columna `POINT` del que tomar puntos reales (experimento espacial) |
| `--postgres-dsn` | conexión al PostgreSQL contra el que se mide GiST |
| `--output` | dónde dejar el JSON |

| Experimento | Qué compara | Gráficas |
|---|---|---|
| [1. Gestión de archivos](#1-gestión-de-archivos--heap-vs-secuencial) | heap file vs archivo secuencial | [almacenamiento](figures/almacenamiento.png) |
| [2. Indexación](#2-indexación--b-agrupado-vs-b-no-agrupado-vs-hash-extendible) | B+ agrupado vs B+ no agrupado vs hash extendible | [índices](figures/indices.png) |
| [3. Datos espaciales](#3-datos-espaciales--búsqueda-secuencial-vs-r-tree-vs-gist) | búsqueda secuencial vs R-Tree vs GiST de PostgreSQL | [radio](figures/espacial-radio.png), [k-NN](figures/espacial-knn.png), [índice](figures/espacial-indice.png), [nodos](figures/espacial-nodos.png) |

**Cómo leer las gráficas.** Cada una es una fila de paneles, uno por operación, con el
número de filas en horizontal y una línea por técnica. Los dos ejes son logarítmicos: los
tamaños van de 1 000 a 100 000 y los tiempos cubren cuatro órdenes de magnitud, así que en
escala lineal solo se vería la técnica más lenta. En un eje logarítmico **la pendiente dice
cómo escala** la técnica: una línea plana no depende del tamaño y una a 45° crece en
proporción a él. El número junto al último punto es el valor con 100 000 filas.

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
