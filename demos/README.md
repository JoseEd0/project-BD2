# Demostraciones

Tres guiones pensados para la exposición del proyecto: un dataset relacional que se genera
solo, uno espacial **real** que se descarga, y una simulación de concurrencia.

## 1. Poblar la base de datos — `poblar_ecommerce.py`

Genera y carga un **e-commerce con cinco tablas relacionadas**, cada una con una
organización física distinta, para que en la misma demo se vean las tres funcionando, y
una sexta con datos espaciales:

```
categorias ──< productos ──┐
                           ├──< detalle_pedidos >── pedidos >── clientes
```

| Tabla | Filas (escala 1) | Organización | Índices |
|---|---:|---|---|
| `categorias` | 12 | heap file | clave primaria |
| `clientes` | 2 000 | heap file | clave primaria + **hash en `ciudad`** |
| `productos` | 800 | **B+ agrupado** | las filas viven en el árbol |
| `pedidos` | 6 000 | **archivo secuencial** | ordenado por `id` |
| `detalle_pedidos` | 18 000 | heap file | clave primaria + **B+ en `pedido_id`** |
| `tiendas` | 3 000 | heap file | clave primaria + **R-Tree en `ubicacion`** |

`tiendas` es la tabla de la Parte 2: cada local tiene un rubro, un distrito de Lima y su
ubicación como `POINT(latitud, longitud)`. Los puntos se generan dentro del contorno de
doce distritos, de modo que «tiendas dentro de Miraflores» tiene una respuesta
comprobable: las que llevan ese distrito. Los contornos son **polígonos simplificados a
mano**, no límites oficiales.

```bash
.venv/bin/python demos/poblar_ecommerce.py --data-dir ./data
.venv/bin/python demos/poblar_ecommerce.py --data-dir ./data --escala 3 --reiniciar
```

| Argumento | Para qué |
|---|---|
| `--data-dir` | dónde vive la base de datos (el mismo que usa el API) |
| `--escala` | multiplica el número de filas; `3` da unas 90 000 |
| `--seed` | semilla del generador; misma semilla, mismos datos |
| `--reiniciar` | borra el directorio antes de cargar |
| `--csv-dir` | dónde dejar los CSV; por defecto `<data-dir>/csv/` |

Deja además los CSV, que sirven para probar **Cargar CSV** desde la interfaz.

### `samples/`: los CSV versionados

[`samples/`](samples/) guarda los seis CSV de la escala por defecto, para que quien clone
el repositorio pueda probar la carga de archivos sin ejecutar nada. Salen de este mismo
script, con la semilla por defecto, así que se regeneran idénticos byte a byte (hay un
test que lo comprueba, `tests/demos/test_dataset.py`):

```bash
.venv/bin/python demos/poblar_ecommerce.py --data-dir /tmp/semilla --reiniciar --csv-dir demos/samples
```

Al terminar imprime el tiempo de carga por tabla —que ya es una comparación entre
organizaciones— y una lista de consultas para la demo.

### Estos datos son sintéticos, y a propósito

Nombres, ciudades, productos y fechas se generan con un generador determinista en vez de
descargar un dataset público. Así el dataset **se reproduce con un comando**, no depende de
la red ni de una licencia, y su tamaño se ajusta con `--escala` para que las diferencias
entre estructuras se noten en la interfaz. Para lo espacial, donde importa que los puntos
estén repartidos como en el mundo y no como los reparte un generador, están los datos
reales de la sección siguiente.

### Qué enseñar con esto

| Consulta | Qué se ve en el plan |
|---|---|
| `SELECT * FROM clientes WHERE ciudad = 'Cusco'` | `IndexLookup` por hash — **~1 ms** |
| `SELECT * FROM clientes WHERE nombre = '…'` | `SequentialScan` sobre 2 000 filas — **~6 ms** |
| `SELECT * FROM productos WHERE id BETWEEN 100 AND 140` | `PrimaryKeyRange` en el B+ agrupado |
| `SELECT * FROM pedidos WHERE id BETWEEN 500 AND 560` | `PrimaryKeyRange` en el secuencial |
| `SELECT estado, COUNT(*) … GROUP BY estado` | `HashAggregate` (hashing externo) |
| `JOIN` de tres tablas con `WHERE c.ciudad = …` | `HashJoin` encadenado y el filtro bajando al índice |
| `… WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000` | `SpatialRangeScan` sobre el R-Tree y los nodos que abrió |
| `… ORDER BY distancia(ubicacion, POINT(…)) LIMIT 10` | `SpatialNearestScan`: k-NN sin ordenar nada |
| `… WHERE intersecta(ubicacion, POLYGON(…))` | `SpatialPolygonScan` |

## 2. Lugares reales — `descargar_lugares.py`

Descarga los **puntos de interés de OpenStreetMap** de un país y los deja en un CSV que el
gestor carga tal cual: restaurantes, farmacias, colegios, bancos, gasolineras… cada uno con
su nombre, su rubro en castellano y su ubicación.

```bash
# todo el Perú: unos 128 000 lugares
.venv/bin/python demos/descargar_lugares.py --salida data/datasets/lugares_peru.csv

# solo Lima y Callao: unos 26 600
.venv/bin/python demos/descargar_lugares.py --salida data/datasets/lugares_lima.csv \
    --recuadro -12.52 -77.20 -11.57 -76.62
```

| Argumento | Para qué |
|---|---|
| `--salida` | CSV que se escribe |
| `--recuadro SUR OESTE NORTE ESTE` | solo los lugares dentro de ese recuadro de coordenadas |
| `--url` | shapefile de Geofabrik de otro país; por omisión, el del Perú |

El CSV tiene cuatro columnas:

```
id,nombre,rubro,ubicacion
4332685891,Botica Alcimar,farmacia,"POINT(-12.045938, -77.038442)"
```

| Dato (extracto del 4 de octubre de 2026) | Perú | Lima y Callao |
|---|---:|---:|
| lugares | 128 011 | 26 606 |
| rubros distintos | 128 | 116 |
| colegios | 40 075 | 4 079 |
| restaurantes | 9 958 | 3 509 |
| farmacias | 3 510 | 1 880 |
| bancos | 1 754 | 707 |
| gasolineras | 1 405 | 125 |

Las cifras cambian de un día a otro: el extracto es el del día de la descarga.

**Cómo funciona.** Geofabrik publica cada país como un `.zip` de cientos de MB con una capa
shapefile por tipo de objeto. El guion no lo descarga entero: abre el `.zip` por HTTP
pidiendo solo los bytes que lee (unos 5 MB, las dos capas de puntos) y decodifica los
`.shp` y `.dbf` con un lector propio, sin dependencias. Solo conserva los puntos **con
nombre** —OpenStreetMap también cataloga bancas, papeleras y cámaras— y de la capa de
tráfico solo las gasolineras.

**Cargarlo.** Desde la interfaz, **Cargar CSV** con la organización «Sin índice» deja la
tabla como un heap sin nada, que es el punto de partida para ver qué aporta el R-Tree; o
por SQL, ya con el índice:

```sql
CREATE TABLE lugares FROM FILE 'data/datasets/lugares_lima.csv' USING INDEX RTREE("ubicacion");

-- farmacias a menos de 1 km de la Plaza de Armas
SELECT nombre FROM lugares
 WHERE rubro = 'farmacia' AND distancia(ubicacion, POINT(-12.0464, -77.0428)) < 1000;

-- los 10 restaurantes más cercanos al Parque Kennedy
SELECT nombre, distancia(ubicacion, POINT(-12.1211, -77.0297)) AS metros
  FROM lugares WHERE rubro = 'restaurante'
 ORDER BY distancia(ubicacion, POINT(-12.1211, -77.0297)) LIMIT 10;
```

Con los 26 606 lugares de Lima, la primera consulta pasa de recorrer la tabla entera a abrir
un puñado de nodos del árbol; el plan de ejecución dice cuántos.

Este es también el dataset del **experimento espacial**
([`benchmarks/README.md`](../benchmarks/README.md)), que toma de él muestras de 1 000,
10 000 y 100 000 puntos.

**Licencia.** Los datos son de OpenStreetMap y se distribuyen bajo la
[ODbL](https://opendatacommons.org/licenses/odbl/): © colaboradores de OpenStreetMap. Por
eso el CSV no está en el repositorio (`data/` se ignora): quien lo quiera, lo descarga.

## 3. Concurrencia — `concurrencia.py`

```bash
.venv/bin/python demos/concurrencia.py
```

Tres escenas: una **race condition** real (4 hilos restan 25 veces cada uno de un saldo
de 1 000: debería quedar en 900 y sin control queda en 975), la misma carga hecha
correcta dentro de transacciones, y un
**interbloqueo** que el gestor detecta y resuelve abortando exactamente una de las dos
transacciones. Ver [`src/txn/README.md`](../src/txn/README.md).
