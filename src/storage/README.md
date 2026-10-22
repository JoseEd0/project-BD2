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
| `INT` | 8 | entero con signo de 64 bits; admite un real sin decimales (`10.0`) |
| `FLOAT` | 8 | doble precisión; solo valores finitos, y `-0.0` se guarda como `0.0` |
| `BOOL` | 1 | 0 / 1 |
| `DATE` | 4 | días desde 1970-01-01 (preserva el orden); se acepta como texto ISO `AAAA-MM-DD` |
| `STRING(n)` | n | UTF-8 rellenado con ceros; por eso no admite el carácter NUL |
| `BYTES(n)` | 4 + n | su longitud y los bytes, rellenados con ceros |
| `POINT` | 16 | dos dobles (lat, lon) |
| `VECTOR(n)` | 4n | n flotantes de precisión simple, finitos |

Un valor que no cabe se rechaza con un error del almacenamiento, nunca se trunca: un texto
más largo que el campo, un entero fuera de los 64 bits, un real con decimales en un `INT`.

Los reales tienen **un solo cero y ningún NaN**. NaN no es igual a sí mismo y `-0.0` tiene
otros bytes que `0.0` aunque valgan lo mismo: el índice hash y el hashing externo, que
reparten por bytes, responderían distinto que un recorrido. Unos `BYTES` pueden acabar en
ceros, así que llevan su longitud; una cadena no, porque el relleno se reconoce al leer.

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

1. guarda las páginas en el **buffer pool común** (`buffer_pool.py`): un solo LRU con
   `buffer_pool_pages` páginas (2 048 por omisión, 8 MiB) para **todos** los archivos de
   la configuración —tablas, índices y temporales de un ordenamiento—;
2. marca como *sucias* las que se modifican y solo las escribe al expulsarlas, al hacer
   `flush` (un COMMIT) o al cerrar. `discard()` cierra sin escribirlas, para un archivo
   que se va a reemplazar o borrar;
3. cuenta las lecturas y escrituras físicas (`reads`, `writes`): la diferencia entre
   páginas pedidas y páginas leídas de disco es lo que ahorra el buffer pool.

El pool es uno porque la memoria es una, como en cualquier gestor: un archivo muy
consultado puede ocupar casi todo el presupuesto mientras otro sin uso no ocupa nada. Con
una caché por archivo el gasto crecía con el número de archivos abiertos, y un `JOIN` con
dieciséis particiones abiertas reservaba dieciséis cachés.

Un candado protege el pool, porque varias sesiones leen a la vez. El `fsync` de un `flush`
va fuera de él, sobre una copia del descriptor: esperar al dispositivo no frena las lecturas
de las demás sesiones.

`read()` devuelve una **copia** de la página. Cuesta un `memcpy` por acceso y a cambio hace
imposible corromper el buffer pool por descuido: quien quiera cambiar algo escribe de
vuelta con `write()`.

Convención de todo el motor: **la página 0 de cada archivo es la cabecera** y pertenece a
la estructura que lo usa (cabeza de la lista de libres, raíz del árbol, profundidad global…).

### Piezas de formato compartidas (`layout.py`)

Las cinco estructuras de disco usan las mismas tres piezas, escritas una sola vez:

- **`FileHeader`**: la cabecera de la página 0 empieza por una firma de cuatro bytes y una
  versión del formato. Abrir un archivo de otra estructura, o de una versión anterior del
  formato, da un error que lo dice («`t.bpt` es un árbol B+ de la versión 1 del formato, y
  este motor lee la 2») en vez de interpretar mal sus páginas. La cabecera **se escribe al
  volcar o cerrar el archivo**, no en cada inserción: lo que guarda vive en memoria, y en
  disco solo hace falta cuando también están las páginas.
- **`FreePageList`**: el B+, el hash y el R-Tree reutilizan las páginas que liberan al
  fusionar o dividir. Cada página libre guarda el número de la siguiente; la cabeza va en la
  cabecera.
- **`chained_pages`**: el recorrido de una cadena de páginas (el desbordamiento del
  secuencial y de las cubetas del hash, y la lista de libres). Una cadena no puede tener más
  páginas que el archivo: si las tiene, una página corrupta apunta hacia atrás, y en vez de
  girar para siempre se lanza `CorruptFileError`.

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
