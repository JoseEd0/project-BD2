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

Qué esperar y por qué:

| | Heap file | Archivo secuencial |
|---|---|---|
| Inserción | **más rápida**: va a la primera ranura libre | más lenta: localiza la página y desplaza |
| Búsqueda por clave | recorrido completo, `O(P)` | búsqueda binaria, `O(log P)` |
| Espacio | el mínimo | algo más: el factor de llenado deja hueco a propósito |
| Coste oculto | ninguno | reorganizaciones periódicas |

El cruce importa: con pocas filas el recorrido del heap gana porque leer 20 páginas
seguidas es más barato que 5 saltos; a partir de unos miles de filas la búsqueda binaria se
impone. **Ese cruce es la conclusión del experimento**, y por eso se mide con varios
tamaños en vez de con uno.

## 2. Indexación — B+ agrupado vs B+ no agrupado vs hash extendible

`index_benchmark.py` mide construcción, igualdad, rango, recorrido ordenado, espacio
adicional y borrado. Las búsquedas de los índices no agrupados **incluyen el salto al
heap**, que es el coste real de usarlos.

Qué esperar y por qué:

| | B+ agrupado | B+ no agrupado | Hash extendible |
|---|---|---|---|
| Igualdad | `O(log N)`, sin saltos | `O(log N)` + 1 salto | **`O(1)`** + 1 salto |
| Rango | **el mejor**: filas contiguas | `O(log N + k)` + k saltos | **no soportado** |
| Orden | gratis | gratis, con un salto por fila | no soportado |
| Espacio | grande: guarda las filas | pequeño: solo direcciones | pequeño + directorio |
| Por tabla | uno | varios | varios |

Las celdas "no soportado" salen con tiempo 0 y una nota: **no son un cero de rendimiento,
son una limitación de la técnica**, y es justamente la conclusión que el experimento tiene
que dejar clara.

## Cómo leer los tiempos de construcción

El B+ no agrupado tarda más en construirse de lo que su tamaño haría pensar. El motivo está
en la implementación, no en la estructura: su clave es compuesta `(valor, dirección)` y en
cada nodo caben muchas más entradas, de modo que **deserializar un nodo decodifica cientos
de claves en Python**. Es el precio de tener nodos como objetos legibles en vez de operar
sobre los bytes de la página. Merece la pena señalarlo en el informe como lo que es: una
consecuencia medida de una decisión de diseño.

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
