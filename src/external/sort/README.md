# Ordenamiento externo (mezcla k-vías)

## El problema

`ORDER BY` sobre un millón de filas no cabe en memoria. Un `sort()` normal fallaría. El
ordenamiento externo lo resuelve en dos fases con memoria acotada.

## Fase 1 — generación de runs

Se leen tantos registros como quepan en el buffer (`sort_buffer_pages` páginas), se ordenan
**en memoria** y se vuelcan a un archivo temporal. Cada archivo es un *run*: un trozo ya
ordenado.

```
entrada desordenada  [9 3 7 | 5 1 8 | 6 2 4]     buffer = 3 registros
                        ↓        ↓        ↓
runs en disco        [3 7 9] [1 5 8] [2 4 6]
```

Con `N` registros y buffer de `B`, salen `⌈N/B⌉` runs.

## Fase 2 — mezcla k-vías

Se abren `k` runs a la vez (`merge_fan_in`) y se saca siempre el menor de sus cabezas. Un
**montículo** con `k` elementos dice cuál es el menor en `O(log k)`:

```
run A: [3 7 9]   ┐
run B: [1 5 8]   ├──►  montículo {3,1,2} ──► saca 1, mete el 5 de B ──► 1
run C: [2 4 6]   ┘                       ──► saca 2, mete el 4 de C ──► 1 2
                                         ──► ...                    ──► 1 2 3 4 5 6 7 8 9
```

De cada run solo hay **una página** en memoria a la vez, así que la fase 2 gasta `k`
páginas y nada más.

Si hay más de `k` runs, se hacen varias pasadas: cada una divide el número de runs entre
`k`, hasta que queda uno solo.

```
84 runs, k = 3  →  28 runs  →  10 runs  →  4 runs  →  2 runs  →  salida
                   pasada 1   pasada 2   pasada 3   pasada 4   pasada 5
```

## Complejidad

Con `N` registros, buffer de `B` registros y abanico `k`:

| Concepto | Coste |
|---|---|
| Runs iniciales | `⌈N/B⌉` |
| Pasadas de mezcla | `⌈log_k(N/B)⌉` |
| E/S total | `O(N · log_k(N/B))` lecturas + escrituras |
| Memoria fase 1 | `B` registros |
| Memoria fase 2 | `k` páginas |
| Comparaciones | `O(N log N)` |

**Lo que hay que entender:** el número de pasadas crece con el *logaritmo* del número de
runs. Duplicar el buffer o el abanico casi siempre elimina una pasada entera, y cada pasada
cuesta leer y escribir el archivo completo. Por eso los gestores reales dedican tanta
memoria a ordenar.

## Detalle de implementación

Los archivos temporales **nunca reutilizan un nombre**. Reciclarlo pisaría un run que la
pasada actual todavía está leyendo — fue exactamente el error que apareció al probar con
84 runs y abanico 3.

## Uso

```python
from external.sort import ExternalSorter

with ExternalSorter(directorio, serializer.size, key_of, config) as sorter:
    for record in sorter.sort(registros):     # devuelve un iterador perezoso
        ...
    print(sorter.run_count, sorter.merge_passes)
```

## Tests

```bash
.venv/bin/python -m pytest tests/external/sort -q
```

Cubren: entrada vacía, un registro, entrada que cabe entera en el buffer (un run, una
pasada), entrada ya ordenada, entrada invertida, entrada aleatoria con **varias pasadas de
mezcla**, claves repetidas, orden por una columna de texto y limpieza de los temporales.
