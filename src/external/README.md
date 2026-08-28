# Algoritmos externos

Un algoritmo es **externo** cuando trabaja con más datos de los que caben en memoria. La
regla que los define: en ningún momento hay más de unas pocas páginas cargadas, y todo lo
demás vive en archivos temporales.

| Carpeta | Algoritmo | Resuelve |
|---|---|---|
| [`sort/`](sort/README.md) | Ordenamiento externo por mezcla k-vías | `ORDER BY`, `DISTINCT`, agrupación ordenada |
| [`hash/`](hash/README.md) | Hashing externo (particionado) | `GROUP BY`, `JOIN` |

## Lo que comparten (`runs.py`)

Ambos generan montones de archivos intermedios. `RunWriter` y `RunReader` los escriben y
leen **página a página**, así que la memoria que consume un archivo abierto es una página,
sin importar lo que ocupe.

## Ordenar o hashear

| | Ordenamiento externo | Hashing externo |
|---|---|---|
| E/S | `O(N · log_k(N/B))` | `2N` — una escritura y una lectura |
| Resultado ordenado | **sí** | no |
| Sensible a claves muy repetidas | no | sí: una partición puede desbalancearse |
| Sirve para | `ORDER BY`, rangos, agrupación ordenada | `GROUP BY`, `JOIN` por igualdad |

Regla práctica: si el resultado tiene que salir ordenado, ordenamiento externo; si solo hay
que juntar cosas iguales, hashing, que hace **una sola pasada**.

## Tests

```bash
.venv/bin/python -m pytest tests/external -q
.venv/bin/python -m pytest tests/external/sort -q
.venv/bin/python -m pytest tests/external/hash -q
```
