# Demostraciones

Dos guiones pensados para la exposición del proyecto.

## 1. Poblar la base de datos — `poblar_ecommerce.py`

Genera y carga un **e-commerce con cinco tablas relacionadas**, cada una con una
organización física distinta, para que en la misma demo se vean las tres funcionando:

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

```bash
.venv/bin/python demos/poblar_ecommerce.py --data-dir ./data
.venv/bin/python demos/poblar_ecommerce.py --data-dir ./data --escala 3 --reiniciar
```

| Argumento | Para qué |
|---|---|
| `--data-dir` | dónde vive la base de datos (el mismo que usa el API) |
| `--escala` | multiplica el número de filas; `3` da unas 80 000 |
| `--seed` | semilla del generador; misma semilla, mismos datos |
| `--reiniciar` | borra el directorio antes de cargar |
| `--csv-dir` | dónde dejar los CSV; por defecto `<data-dir>/csv/` |

Deja además los CSV, que sirven para probar **Cargar CSV** desde la interfaz.

### `samples/`: los CSV versionados

[`samples/`](samples/) guarda los cinco CSV de la escala por defecto, para que quien clone
el repositorio pueda probar la carga de archivos sin ejecutar nada. Salen de este mismo
script, con la semilla por defecto, así que se regeneran idénticos byte a byte:

```bash
.venv/bin/python demos/poblar_ecommerce.py --data-dir /tmp/semilla --reiniciar --csv-dir demos/samples
```

Al terminar imprime el tiempo de carga por tabla —que ya es una comparación entre
organizaciones— y una lista de consultas para la demo.

### Los datos son sintéticos, y a propósito

Nombres, ciudades, productos y fechas se generan con un generador determinista en vez de
descargar un dataset público. Así el dataset **se reproduce con un comando**, no depende de
la red ni de una licencia, y su tamaño se ajusta con `--escala` para que las diferencias
entre estructuras se noten en la interfaz.

### Qué enseñar con esto

| Consulta | Qué se ve en el plan |
|---|---|
| `SELECT * FROM clientes WHERE ciudad = 'Cusco'` | `IndexLookup` por hash — **~4 ms** |
| `SELECT * FROM clientes WHERE nombre = '…'` | `SequentialScan` sobre 2 000 filas — **~16 ms** |
| `SELECT * FROM productos WHERE id BETWEEN 100 AND 140` | `PrimaryKeyRange` en el B+ agrupado |
| `SELECT * FROM pedidos WHERE id BETWEEN 500 AND 560` | `PrimaryKeyRange` en el secuencial |
| `SELECT estado, COUNT(*) … GROUP BY estado` | `HashAggregate` (hashing externo) |
| `JOIN` de tres tablas con `WHERE c.ciudad = …` | `HashJoin` encadenado y el filtro bajando al índice |

## 2. Concurrencia — `concurrencia.py`

```bash
.venv/bin/python demos/concurrencia.py
```

Tres escenas: una **race condition** real (con 4 hilos el saldo esperado es 920 y sin
control sale 980), la misma carga hecha correcta dentro de transacciones, y un
**interbloqueo** que el gestor detecta y resuelve abortando exactamente una de las dos
transacciones. Ver [`src/txn/README.md`](../src/txn/README.md).
