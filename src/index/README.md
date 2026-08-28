# Índices

Un índice responde "¿dónde están las filas que cumplen esto?" sin recorrer la tabla entera.
Cada estructura vive en su carpeta con su propia explicación:

| Carpeta | Estructura | Sirve para |
|---|---|---|
| [`bplustree/`](bplustree/README.md) | Árbol B+ (agrupado y no agrupado) | igualdad, rango y orden |
| [`hash/`](hash/README.md) | Hash extendible | igualdad, nada más |

## Claves (`keys.py`)

Todas las estructuras comparten la forma de tratar una clave: se **guarda** como bytes de
tamaño fijo y se **compara** como valor de Python.

- `ScalarKeyCodec` — una columna.
- `CompositeKeyCodec` — varias columnas; la clave es una tupla y Python ya las compara en
  orden lexicográfico, que es exactamente el orden que necesita un índice.
- `RecordIdKeyCodec` — la dirección física, usada como componente de desempate.

## Cuál elegir

| Consulta | B+ agrupado | B+ no agrupado | Hash extendible |
|---|---|---|---|
| `WHERE id = 5` | `O(log N)`, sin saltos | `O(log N)` + 1 salto al heap | **`O(1)`** |
| `WHERE id BETWEEN 10 AND 90` | `O(log N + k)` | `O(log N + k)` + k saltos | ✗ imposible |
| `ORDER BY id` | gratis | gratis (con saltos) | ✗ hay que ordenar |
| Muchas filas por valor | — | sí | sí |
| Índices por tabla | uno solo | los que hagan falta | los que hagan falta |

Regla práctica: **hash** si solo se busca por igualdad, **B+** en cuanto aparezca un rango o
un `ORDER BY`, y **agrupado** para la clave primaria de la tabla.

## Tests

```bash
.venv/bin/python -m pytest tests/index -q            # todos
.venv/bin/python -m pytest tests/index/bplustree -q  # solo el B+
.venv/bin/python -m pytest tests/index/hash -q       # solo el hash
```
