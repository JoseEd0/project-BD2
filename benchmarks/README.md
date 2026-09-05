# Comparación experimental

Todo lo que se reporta se **mide**. Cada script recibe sus parámetros por línea de comandos
—ningún tamaño ni número de repeticiones está escrito dentro del código—, imprime una tabla
Markdown lista para el informe y guarda los resultados crudos en `results/`.

```bash
.venv/bin/python -m benchmarks.storage_benchmark --sizes 1000 10000 100000 --queries 100
.venv/bin/python -m benchmarks.index_benchmark   --sizes 1000 10000 100000 --queries 100
.venv/bin/python -m benchmarks.plot --report benchmarks/results/indices.json
```

| Argumento | Qué controla |
|---|---|
| `--sizes` | tamaños del conjunto de datos |
| `--queries` | consultas por medida (se promedia el lote) |
| `--seed` | semilla de las claves barajadas; misma semilla, mismos datos |
| `--page-size` | tamaño de página del motor |
| `--output` | dónde dejar el JSON |

## 1. Gestión de archivos — heap vs secuencial

`storage_benchmark.py` mide inserción, búsqueda por clave primaria, espacio en disco,
borrado y reorganización.

### Resultados medidos

Ejecución con `--sizes 1000 10000 100000 --queries 100`, páginas de 4 KB
(macOS, Python 3.12). Las búsquedas son el total de 100 consultas.

| Operación | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| Inserción (ms) | heap | **8.2** | **68.7** | **751** |
| | secuencial | 25.4 | 267.1 | 3 876 |
| Búsqueda por clave (ms) | heap | **1.0** | 10.1 | 95.4 |
| | secuencial | 4.1 | **4.2** | **5.4** |
| Espacio (KiB) | heap | **64** | **576** | **5 720** |
| | secuencial | 100 | 964 | 9 608 |
| Reorganización (ms) | secuencial | 2.1 | 18.5 | 185.6 |

### Conclusiones

**El cruce está entre 1 000 y 10 000 filas.** Con 1 000 registros el recorrido completo del
heap (1.0 ms) gana a la búsqueda binaria del secuencial (4.1 ms): recorrer 8 páginas
seguidas sale más barato que dar 10 saltos. A partir de ahí la asimetría se dispara —el
heap crece lineal (1 → 10 → 95 ms) y el secuencial casi no se mueve (4.1 → 4.2 → 5.4 ms)—
y con 100 000 filas **el secuencial busca 18 veces más rápido**.

El precio del orden se paga al escribir: insertar cuesta **5 veces más** en el secuencial,
porque cada inserción localiza la página y desplaza bytes dentro de ella, y cada tanto se
dispara una reorganización. También ocupa **1.7 veces más espacio**, y eso es deliberado:
el factor de llenado del 80 % deja hueco para que las inserciones futuras quepan en su
sitio en vez de irse al desbordamiento.

**Cuándo usar cada uno:** heap si se escribe mucho y se consulta siempre por índice;
secuencial si se consulta por clave o por rango y las escrituras son la minoría.

### Resumen: ventajas y desventajas

| | Heap file | Archivo secuencial |
|---|---|---|
| **Ventajas** | Inserción `O(1)`: va directo a la cabeza de la lista de libres · el menor espacio en disco · reutiliza los huecos de los borrados sin ningún trabajo diferido · sin coste oculto | Búsqueda por clave `O(log P)`: 18× más rápido con 100 000 filas · rangos y recorrido ordenado gratis, sin ordenar nada · el orden se mantiene siempre disponible |
| **Desventajas** | Búsqueda por valor `O(P)`: hay que recorrer el archivo entero · un rango o un `ORDER BY` obligan a ordenar aparte · inútil sin un índice encima | Inserción 5× más lenta: localiza la página y desplaza bytes · 1.7× más espacio por el factor de llenado del 80 % · reorganizaciones periódicas que congelan el archivo · el desbordamiento degrada la búsqueda hasta que se reorganiza |
| **Se elige cuando** | Carga masiva, tablas que siempre se consultan por índice, orden de lectura irrelevante | Consultas por rango o por clave frecuentes, escrituras minoritarias, necesidad de recorrer en orden |

## 2. Indexación — B+ agrupado vs B+ no agrupado vs hash extendible

`index_benchmark.py` mide construcción, igualdad, rango, recorrido ordenado, espacio
adicional y borrado. Las búsquedas de los índices no agrupados **incluyen el salto al
heap**, que es el coste real de usarlos.

### Resultados medidos

Mismos parámetros. Las consultas son el total de 100; las de los índices no agrupados
**incluyen el salto al heap**.

| Operación | Técnica | 1 000 | 10 000 | 100 000 |
|---|---|---:|---:|---:|
| Construcción (ms) | B+ agrupado | 32 | 545 | 8 840 |
| | B+ no agrupado | 306 | 3 630 | 47 623 |
| | hash extendible | **25** | **324** | **3 430** |
| Igualdad (ms) | B+ agrupado | **1.7** | **6.1** | **8.0** |
| | B+ no agrupado | 23.5 | 24.2 | 32.2 |
| | hash extendible | 11.6 | 8.6 | 11.4 |
| Rango (ms) | B+ agrupado | **3.2** | **8.1** | **10.6** |
| | B+ no agrupado | 33.1 | 35.2 | 52.2 |
| | hash extendible | — | — | **no soportado** |
| Recorrido ordenado (ms) | B+ agrupado | **0.3** | **3.4** | **35.2** |
| | B+ no agrupado | 2.0 | 22.0 | 361.9 |
| | hash extendible | — | — | **no soportado** |
| Espacio (KiB) | B+ agrupado | 100 | 924 | 9 312 |
| | B+ no agrupado | **24** | **220** | 2 060 |
| | hash extendible | 28 | 264 | **2 056** |
| Inserción/borrado (ms) | B+ agrupado | **6.5** | **167** | **2 175** |
| | B+ no agrupado | 76.0 | 848 | 9 202 |
| | hash extendible | 12.5 | 96 | 1 273 |

Las celdas «no soportado» **no son un cero de rendimiento, son una limitación de la
técnica**: el hash destruye el orden, así que no hay forma de resolver un rango ni un
`ORDER BY` con él.

### Conclusiones

**El B+ agrupado gana en consultas y el hash en escritura.** El agrupado responde una
igualdad en 8 ms con 100 000 filas porque la fila ya está en la hoja: no hay salto. El hash
tarda 11.4 ms pese a ser `O(1)`, y toda la diferencia es ese salto al heap. El B+ no
agrupado suma lo peor de ambos para esta carga: `O(log N)` **más** el salto.

**El salto al heap domina cuando hay muchos resultados.** En el recorrido ordenado de
100 000 filas el agrupado tarda 35 ms y el no agrupado 362 ms: diez veces más, y la
estructura es la misma. La diferencia son 100 000 accesos sueltos al heap.

**El espacio va al revés.** El agrupado ocupa 9 312 KiB porque guarda las filas; los otros
dos rondan 2 060 KiB porque solo guardan direcciones. Por eso solo puede haber un índice
agrupado por tabla y tantos secundarios como haga falta.

**Cuándo usar cada uno:** agrupado para la clave primaria de la tabla; hash para columnas
que solo se consultan por igualdad; B+ no agrupado cuando además hacen falta rangos sobre
una columna que no es la de agrupamiento.

### Resumen: ventajas y desventajas

| | B+ agrupado | B+ no agrupado | Hash extendible |
|---|---|---|---|
| **Ventajas** | La fila está en la hoja: **sin salto al heap** · el mejor en igualdad (8 ms) y en rango (10.6 ms) · recorrido ordenado 10× más rápido que el no agrupado · un solo archivo que es índice y datos a la vez | Ocupa 4.5× menos que el agrupado porque solo guarda direcciones · **varios por tabla**, uno por columna que interese · admite rangos y orden · admite valores repetidos con clave compuesta `(valor, dirección)` | Igualdad en `O(1)`: dos accesos, uno al directorio y uno a la cubeta, sea cual sea el tamaño · **construcción 14× más rápida** que el B+ no agrupado (3.4 s vs 47.6 s) · crecer solo duplica el directorio, sin tocar las cubetas · el más compacto |
| **Desventajas** | Ocupa 9 312 KiB porque guarda las filas · **solo puede haber uno por tabla**: los datos solo pueden estar ordenados de una forma · las hojas son grandes, así que caben menos entradas por página y el árbol crece en altura | Paga un **salto al heap por cada resultado**: con 100 000 filas el recorrido ordenado tarda 362 ms frente a 35 ms del agrupado · el más lento en construirse por el coste de decodificar claves compuestas | **No admite rangos ni `ORDER BY`**: el hash destruye el orden · claves muy repetidas no se pueden repartir y degeneran en cadenas de desbordamiento · esta implementación no fusiona cubetas al borrar |
| **Se elige cuando** | Es la clave primaria de la tabla y las consultas por ella dominan | Hace falta un índice secundario y las consultas incluyen rangos u orden | La columna solo se consulta por igualdad y las escrituras son frecuentes |

## Cómo leer los tiempos de construcción

El B+ no agrupado tarda 47.6 s en construirse con 100 000 filas: cinco veces más que el
agrupado y catorce veces más que el hash. El motivo está en la implementación, no en la
estructura. Su clave es compuesta `(valor, dirección)`, ocupa 14 bytes en vez de 8, y por
eso en cada nodo caben casi 300 entradas en vez de 59. Cada vez que se lee un nodo,
**deserializar decodifica esas 300 claves compuestas en Python**, aunque la búsqueda binaria
solo vaya a mirar ocho.

Es el precio de tener los nodos como objetos legibles en vez de operar directamente sobre
los bytes de la página. La alternativa —comparar claves serializadas sin decodificarlas—
sería más rápida y bastante menos clara. Merece la pena contarlo en el informe como lo que
es: **una consecuencia medida de una decisión de diseño**, no un defecto de la estructura.

## Gráficas

`plot.py` dibuja tiempo frente a número de filas, una imagen por operación:

```bash
.venv/bin/pip install -e ".[plots]"
.venv/bin/python -m benchmarks.plot --report benchmarks/results/almacenamiento.json
```

Las medidas de espacio se omiten de las gráficas de tiempo; están en la tabla y en el JSON.

## Reproducibilidad

Misma semilla ⇒ mismos datos ⇒ resultados comparables entre ejecuciones. Los benchmarks
trabajan sobre un directorio temporal que se borra al terminar, así que no ensucian los
datos del gestor. `results/` está en `.gitignore`: se versiona el script, no la salida.
