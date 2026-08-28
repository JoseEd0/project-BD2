# Capa de almacenamiento

Esta capa responde a una sola pregunta: **¿cómo se convierte una fila de una tabla en bytes
dentro de un archivo, y cómo se recupera?** No sabe nada de SQL.

```
  fila (7, "ana", 4.5)
        │
        ▼   RecordSerializer          registro de tamaño fijo
   ┌──────────────────────────────────────────────┐
   │ mapa de nulos │ id │ nombre │ nota           │
   └──────────────────────────────────────────────┘
        │
        ▼   RecordPage                 muchas por página
   ┌──────────┬─────────┬─────────┬─────┬─────────┐
   │ cabecera │ ranura0 │ ranura1 │ ... │ ranuraN │
   └──────────┴─────────┴─────────┴─────┴─────────┘
        │
        ▼   Pager (+ buffer pool)      páginas ↔ disco
   archivo.dat
```

## 1. Tipos (`types.py`)

Cada tipo de dato sabe convertirse en bytes mediante un **códec**. Los tipos son de tamaño
fijo, sin excepción:

| Tipo | Bytes | Representación |
|---|---|---|
| `INT` | 8 | entero con signo |
| `FLOAT` | 8 | doble precisión |
| `BOOL` | 1 | 0 / 1 |
| `DATE` | 4 | días desde 1970-01-01 (preserva el orden) |
| `STRING(n)` | n | UTF-8 rellenado con ceros |
| `BYTES(n)` | n | crudo rellenado con ceros |
| `POINT` | 16 | dos dobles (lat, lon) |
| `VECTOR(n)` | 4n | n flotantes de precisión simple |

**¿Por qué tamaño fijo?** Porque hace que la posición de cualquier registro dentro de una
página sea una multiplicación, no una búsqueda. Sin eso, el archivo secuencial no podría
insertar en medio desplazando bytes, y el árbol B+ no podría calcular su orden de antemano.
El precio es que un `VARCHAR(50)` con la palabra "sí" gasta 50 bytes igual.

## 2. Registros (`record.py`)

Un registro es un **mapa de nulos** seguido de los campos empaquetados con un único
`struct`. El mapa ocupa `⌈n_campos / 8⌉` bytes y cada bit en 1 significa "este campo es
NULL". Los campos nulos igual escriben su valor neutro para que el registro no cambie de
tamaño.

```
esquema: (id INT NOT NULL, nombre VARCHAR(12), nota FLOAT)
tamaño:  1 (mapa) + 8 (id) + 12 (nombre) + 8 (nota) = 29 bytes

pack((7, None, 4.5))
  ┌────────┬──────────┬──────────────┬──────────┐
  │ 0b0010 │ 7        │ (ceros)      │ 4.5      │
  └────────┴──────────┴──────────────┴──────────┘
    bit 1 encendido → 'nombre' es NULL
```

`unpack_field(raw, i)` lee **un solo campo** sin reconstruir la fila entera. Es lo que usan
el archivo secuencial y los índices para leer una clave: deserializar 8 bytes en vez de 29.

## 3. Páginas (`page.py`)

La página es la unidad de intercambio con el disco. Su tamaño (`page_size`, por defecto
4096 bytes) fija la capacidad de todas las estructuras:

```
ranuras_por_página = (page_size − 8) ÷ (1 + tamaño_registro)
```

Cabecera de 8 bytes: cuántas ranuras están ocupadas, un puntero a otra página y unos bits
de banderas. El puntero significa cosas distintas según quién use la página — el heap file
encadena páginas con hueco, el secuencial encadena su zona de desbordamiento.

Cada ranura es **1 byte de estado + el registro**. El estado distingue tres situaciones:

| Estado | Significado | Quién lo usa |
|---|---|---|
| `EMPTY` | ranura reutilizable | heap file tras un borrado |
| `USED` | registro vigente | todos |
| `DELETED` | lápida: el registro sigue ahí pero está borrado | secuencial (borrado lazy) |

Dos disciplinas conviven sobre la misma página:

- **Con huecos** (heap): `insert` busca la primera ranura `EMPTY`; el orden da igual.
- **Contigua y ordenada** (secuencial): `insert_at(pos)` desplaza a la derecha para que las
  ranuras queden ordenadas por clave; `record_at` puede leer una lápida, porque su clave
  sigue marcando su posición en el orden.

## 4. Paginador y buffer pool (`pager.py`)

Ninguna estructura abre archivos por su cuenta. Todas piden páginas al `Pager`, que:

1. mantiene en memoria las últimas `buffer_pool_pages` páginas usadas (política **LRU**);
2. marca como *sucias* las que se modifican y solo las escribe al expulsarlas o al cerrar;
3. cuenta lecturas y escrituras físicas, que es lo que miden los benchmarks.

`read()` devuelve una **copia** de la página. Cuesta un `memcpy` por acceso y a cambio hace
imposible corromper el buffer pool por descuido: quien quiera cambiar algo escribe de
vuelta con `write()`.

Convención de todo el motor: **la página 0 de cada archivo es la cabecera** y pertenece a
la estructura que lo usa (cabeza de la lista de libres, raíz del árbol, profundidad global…).

## 5. Las dos organizaciones de archivo

| | [Heap file](heap/README.md) | [Archivo secuencial](sequential/README.md) |
|---|---|---|
| Orden de los registros | de llegada | por clave |
| Inserción | O(1) | O(log P) + desplazamiento |
| Búsqueda por clave | O(P) — recorrido completo | O(log P) |
| Recorrido ordenado | requiere ordenar | gratis |
| Borrado | libera la ranura | lápida + reorganización |

## Cómo se prueba

```bash
.venv/bin/python -m pytest tests/storage -q
```

Los tests usan páginas de 256 bytes a propósito: con páginas pequeñas cualquier prueba
cruza varias páginas y los errores de frontera aparecen enseguida.
