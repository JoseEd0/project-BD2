# Minigestor de Base de Datos Multimodal

Gestor de base de datos construido desde cero para el curso Base de Datos 2 (UTEC): sin ORM,
sin motor externo y sin librerías que resuelvan las estructuras. La especificación completa
está en [`Proyecto_Integrador_BD2.md`](./Proyecto_Integrador_BD2.md).

**Parte 1 (relacional) completa**: almacenamiento paginado, cuatro estructuras de indexación,
algoritmos externos, parser y ejecutor SQL, transacciones con detección de interbloqueos,
API REST y una interfaz tipo IDE de base de datos.

## Arquitectura

```
              frontend/ (React + TypeScript)
                      │  HTTP
              src/api/ (FastAPI)
                      │
              src/txn/         sesiones, bloqueos, transacciones
                      │
              src/query/       catálogo · planificador · ejecutor Volcano
                 ┌────┴────┐
          src/sql/     src/index/      B+ agrupado, B+ no agrupado, hash extendible
                            │
                       src/external/   ordenamiento y hashing externos
                            │
                       src/storage/    registros · páginas · buffer pool
                                       heap file · archivo secuencial
```

Cada capa solo conoce a la de abajo: el motor no sabe qué es una transacción, y ninguna
capa del motor sabe que existe un API.

## Organización del código

| Carpeta | Contenido | Documentación |
|---|---|---|
| [`src/sql/`](src/sql/) | lexer, AST y parser | [gramática](docs/sql-grammar.md) |
| [`src/storage/`](src/storage/) | tipos, registros, páginas, paginador | [README](src/storage/README.md) |
| [`src/storage/heap/`](src/storage/heap/) | heap file | [README](src/storage/heap/README.md) |
| [`src/storage/sequential/`](src/storage/sequential/) | archivo secuencial paginado | [README](src/storage/sequential/README.md) |
| [`src/index/bplustree/`](src/index/bplustree/) | árbol B+ agrupado y no agrupado | [README](src/index/bplustree/README.md) |
| [`src/index/hash/`](src/index/hash/) | hash extendible | [README](src/index/hash/README.md) |
| [`src/external/sort/`](src/external/sort/) | ordenamiento externo (k-way merge) | [README](src/external/sort/README.md) |
| [`src/external/hash/`](src/external/hash/) | hashing externo (GROUP BY, JOIN) | [README](src/external/hash/README.md) |
| [`src/query/`](src/query/) | catálogo, planificador, ejecutor | [README](src/query/README.md) |
| [`src/txn/`](src/txn/) | transacciones y concurrencia | [README](src/txn/README.md) |
| [`src/api/`](src/api/) | API REST | [README](src/api/README.md) |
| [`frontend/`](frontend/) | interfaz de 4 paneles | [README](frontend/README.md) |
| [`benchmarks/`](benchmarks/) | comparación experimental | [README](benchmarks/README.md) |
| [`demos/`](demos/) | dataset de demostración y simulación de concurrencia | [README](demos/README.md) |

Cada estructura de datos tiene su carpeta y su `.md` explicando cómo funciona, con qué
complejidad y cómo se prueba.

## Instalación

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cd frontend && npm install && cp .env.example .env
```

## Arrancar

```bash
# 1. poblar con el dataset de demostración (e-commerce, 5 tablas relacionadas)
.venv/bin/python demos/poblar_ecommerce.py --data-dir ./data

# 2. API
MINIGESTOR_DATA_DIR=./data .venv/bin/uvicorn api.main:app --reload --port 8000

# 3. interfaz
npm --prefix frontend run dev        # http://localhost:5173
```

El dataset de demostración carga unas 27 000 filas repartidas en cinco tablas, **cada una
con una organización física distinta**, para que las diferencias entre estructuras se vean
en la interfaz. Detalles en [`demos/README.md`](demos/README.md).

### Sin la terminal: cargar los CSV desde la interfaz

Los cinco archivos del dataset están versionados en [`demos/samples/`](demos/samples/). Con
la base vacía, el botón **Cargar CSV** los sube uno a uno eligiendo su organización:

| Archivo | Organización | Columna clave |
|---|---|---|
| `categorias.csv` | Heap file | `id` |
| `clientes.csv` | Heap file | `id` |
| `productos.csv` | B+ agrupado | `id` |
| `pedidos.csv` | Archivo secuencial | `id` |
| `detalle_pedidos.csv` | Heap file | `id` |

Después, el atajo **Carga → Índices tras cargar CSV** crea los dos índices secundarios del
dataset (hash sobre `clientes.ciudad` y B+ sobre `detalle_pedidos.pedido_id`), y todos los
demás atajos del editor quedan listos para usarse.

## Uso desde Python

```python
from config import EngineConfig
from query.engine import Engine

with Engine(EngineConfig()) as engine:
    engine.execute("CREATE TABLE alumnos (id INT PRIMARY KEY, ciudad VARCHAR(20) INDEX HASH)")
    engine.execute("INSERT INTO alumnos VALUES (1, 'lima')")
    result = engine.execute("SELECT * FROM alumnos WHERE ciudad = 'lima'")
    print(result.rows)
    print(result.plan.render())
```

## SQL soportado

```sql
CREATE TABLE t (id INT PRIMARY KEY INDEX BTREE, nombre VARCHAR(30), ciudad VARCHAR(20) INDEX HASH);
CREATE TABLE t FROM FILE "datos.csv" USING INDEX BTREE("id");
CREATE INDEX idx ON t USING HASH (ciudad);
INSERT INTO t VALUES (1, 'ana', 'lima'), (2, 'luis', 'cusco');
SELECT ciudad, COUNT(*) AS total FROM t WHERE id BETWEEN 1 AND 9 GROUP BY ciudad ORDER BY total DESC LIMIT 10;
SELECT a.nombre, b.v FROM t AS a JOIN otra AS b ON a.id = b.id;
UPDATE t SET nombre = 'x' WHERE id = 1;
DELETE FROM t WHERE ciudad = 'lima';
BEGIN TRANSACTION;  ...  END TRANSACTION;   -- o COMMIT / ROLLBACK
```

La gramática completa, incluida la sintaxis espacial, de texto y multimedia que usarán las
partes 2 a 4, está en [`docs/sql-grammar.md`](docs/sql-grammar.md).

## Verificación

```bash
.venv/bin/ruff check                          # lint
.venv/bin/mypy                                # tipos (strict)
.venv/bin/python -m pytest -q                 # 388 tests
npm --prefix frontend run build               # tipos + bundle del frontend
```

Los tests están segmentados por módulo, para no tener que correrlos todos:

```bash
.venv/bin/python -m pytest tests/storage -q
.venv/bin/python -m pytest tests/index/bplustree -q
.venv/bin/python -m pytest tests/external/sort -q
.venv/bin/python -m pytest tests/query -q
.venv/bin/python -m pytest tests/txn -q
```

## Documentación

Cada estructura tiene su propio README junto al código, con cómo funciona, su API, sus
complejidades y sus tests:

| Módulo | Qué documenta |
|---|---|
| [`src/storage/`](src/storage/README.md) | páginas, registros y buffer pool |
| [`src/storage/heap/`](src/storage/heap/README.md) | heap file y reutilización de espacio |
| [`src/storage/sequential/`](src/storage/sequential/README.md) | archivo secuencial y reorganización |
| [`src/index/bplustree/`](src/index/bplustree/README.md) | árbol B+ agrupado y no agrupado |
| [`src/index/hash/`](src/index/hash/README.md) | hash extendible |
| [`src/external/`](src/external/README.md) | ordenamiento y hashing externos |
| [`src/query/`](src/query/README.md) | catálogo, planificador y ejecutor |
| [`src/txn/`](src/txn/README.md) | transacciones y control de concurrencia |
| [`src/api/`](src/api/README.md) | API REST |
| [`frontend/`](frontend/README.md) | interfaz |
| [`benchmarks/`](benchmarks/README.md) | comparación experimental medida |

## Experimentos y demos

```bash
.venv/bin/python -m benchmarks.storage_benchmark --sizes 1000 10000 100000
.venv/bin/python -m benchmarks.index_benchmark   --sizes 1000 10000 100000
.venv/bin/python demos/concurrencia.py
.venv/bin/python demos/poblar_ecommerce.py --data-dir ./data --escala 3 --reiniciar
```

## Autor

**José Huamaní** — Ciencias de la Computación, UTEC
