# Heap file

## Qué es

La organización más simple posible: **los registros se guardan donde haya sitio, sin ningún
orden**. Es lo que usa una tabla que se llena rápido y se consulta por índice, no por
recorrido ordenado.

## Cómo funciona

El archivo es una secuencia de páginas. La página 0 guarda la cabecera; las demás guardan
registros. La única estructura que el heap mantiene es una **lista enlazada de páginas con
espacio libre**:

```
cabecera (página 0)
  free_head ──┐
              ▼
        ┌──────────┐  next   ┌──────────┐  next
        │ página 3 │────────►│ página 1 │────────► −1
        │ ▣ ▣ □ ▣  │         │ □ ▣ □ □  │
        └──────────┘         └──────────┘
        ▣ ocupada   □ libre

        ┌──────────┐
        │ página 2 │  llena → fuera de la lista
        │ ▣ ▣ ▣ ▣  │
        └──────────┘
```

**Invariante:** una página tiene ranuras libres **si y solo si** está en la lista. Eso hace
que insertar sea O(1): se va directo a `free_head`, no se busca hueco por todo el archivo.

### Insertar

1. Si la lista está vacía, se añade una página nueva al final del archivo.
2. Se escribe el registro en la primera ranura libre de la página que encabeza la lista.
3. Si la página quedó llena, se saca de la lista (`free_head = página.next`).
4. Devuelve un `RecordId(página, ranura)`, que es la dirección física del registro.

### Borrar — la reutilización de espacio

1. La ranura pasa a `EMPTY`.
2. **Si la página estaba llena**, vuelve a entrar en la lista de libres.

Ese paso 2 es toda la "estrategia de reutilización de espacios libres" que pide el
enunciado: sin él, un archivo que borra e inserta en igual medida crecería para siempre.

```
inserto 20 registros  → 4 páginas
borro los registros 3 y 19
inserto 2 registros   → siguen siendo 4 páginas, se reusaron los huecos
```

### Buscar

El heap **no sabe buscar por valor**. Solo sabe:

- `read(record_id)` — ir a una dirección conocida, en O(1);
- `scan()` — recorrer todas las páginas en orden físico, en O(P).

Buscar por un campo requiere un índice encima (B+ o hash), que es exactamente para lo que
sirve el `RecordId`: las hojas del índice guardan direcciones que apuntan aquí.

## Complejidad

| Operación | Accesos a página | Nota |
|---|---|---|
| `insert` | O(1) | va directo a la cabeza de la lista de libres |
| `read(rid)` | O(1) | la dirección ya dice dónde mirar |
| `update(rid)` | O(1) | in situ; el registro no cambia de dirección |
| `delete(rid)` | O(1) | |
| `scan()` | O(P) | P = páginas del archivo |
| búsqueda por valor | O(P) | no hay atajo sin índice |

Espacio: `P = ⌈N / ranuras_por_página⌉` páginas, más una de cabecera.

## Cuándo conviene

**Sí:** cargas masivas, tablas que se consultan siempre por índice, tablas donde el orden
de lectura no importa.
**No:** consultas por rango o recorridos ordenados frecuentes — ahí gana el archivo
secuencial o un B+ agrupado.

## Uso

```python
from config import EngineConfig
from storage.heap import HeapFile
from storage.record import RecordSerializer

config = EngineConfig()
serializer = RecordSerializer(schema)

with HeapFile(config.data_directory / "alumnos.heap", serializer.size, config) as heap:
    record_id = heap.insert(serializer.pack((1, "ana", 4.5)))
    fila = serializer.unpack(heap.read(record_id))
    heap.delete(record_id)
```

## Tests

```bash
.venv/bin/python -m pytest tests/storage/heap -q
```

Cubren: archivo vacío, un solo registro, varias páginas, borrado, doble borrado,
**reutilización de huecos sin hacer crecer el archivo**, actualización in situ y
persistencia al reabrir.
