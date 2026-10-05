# Algoritmos externos

Un algoritmo es **externo** cuando trabaja con más datos de los que caben en memoria. La
regla que los define: en ningún momento hay más de unas pocas páginas cargadas, y todo lo
demás vive en archivos temporales.

| Carpeta | Algoritmo | Resuelve |
|---|---|---|
| [`sort/`](sort/README.md) | Ordenamiento externo por mezcla k-vías | `ORDER BY`, carga masiva del R-Tree |
| [`hash/`](hash/README.md) | Hashing externo (particionado) | `GROUP BY`, `DISTINCT`, `JOIN` por igualdad |
| [`loop/`](loop/README.md) | Bucles anidados en bloques | `JOIN` sin igualdad, y las claves que el hash no puede repartir |

## Lo que comparten

Todos generan archivos intermedios. `RunWriter` y `RunReader` (`runs.py`) los escriben y
leen **página a página**, así que la memoria que consume un archivo abierto es una página,
sin importar lo que ocupe.

También comparten **el presupuesto de memoria**: `buffered_records` dice cuántos registros
caben en `sort_buffer_pages` páginas, y esa cifra es a la vez el tamaño de un run del
ordenamiento, el de un bloque del bucle anidado y el máximo de grupos o de filas que el
hashing tiene en memoria antes de volver a repartir. Las dos formas de reunir comparten
además `joining.py`: qué es un par y qué filas sin pareja se conservan.

## Ordenar o hashear

| | Ordenamiento externo | Hashing externo |
|---|---|---|
| E/S | `O(N · log_k(N/B))` | `2N` por nivel; casi siempre uno |
| Resultado ordenado | **sí** | no |
| Claves muy repetidas | indiferente | al agrupar, indiferente; al reunir, esa clave va por bloques |
| Sirve para | `ORDER BY`, rangos | `GROUP BY`, `DISTINCT`, `JOIN` por igualdad |

Regla práctica: si el resultado tiene que salir ordenado, ordenamiento externo; si solo hay
que juntar cosas iguales, hashing, que hace **una sola pasada**.

## Tests

```bash
.venv/bin/python -m pytest tests/external -q
.venv/bin/python -m pytest tests/external/sort -q
.venv/bin/python -m pytest tests/external/hash -q
.venv/bin/python -m pytest tests/external/loop -q
```
