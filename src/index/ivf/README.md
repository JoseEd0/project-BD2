# IVF (Inverted File Index)

k-means reparte los vectores en **listas**, una por centroide. Una consulta se compara con
los centroides y recorre solo las `probes` listas más cercanas: en vez de N comparaciones,
`listas + N · probes / listas`. Es el mismo esquema que el índice invertido de texto, con
los centroides en el papel de los términos. La estructura es `faiss.IndexIVFFlat`, que
guarda los vectores completos en sus listas.

```
consulta ─► centroides (listas) ─► las `probes` más cercanas ─► vectores de esas listas ─► top-k
```

## Parámetros

| Parámetro | En SQL | Por omisión | Por qué |
|---|---|---|---|
| listas | `WITH (LISTS = n)` | `√N`, y al menos 39 vectores por lista | la regla de la wiki de FAISS hasta un millón de vectores; con menos de 39 por centroide FAISS avisa de que k-means no tiene con qué entrenar |
| probes | por consulta | `√listas` | el punto de partida que recomienda pgvector |

| Vectores | Listas | Probes |
|---:|---:|---:|
| 1 000 | 25 | 5 |
| 10 000 | 100 | 10 |
| 100 000 | 316 | 18 |

Con una sola lista no hay nada que agrupar: el centroide es la media y no se entrena.

## Mantenimiento

- **Insertar** asigna el vector a la lista de su centroide más cercano.
- **Borrar es real:** FAISS lo quita de su lista (`remove_ids`, con el mapa directo en una
  tabla hash para encontrarlo sin recorrer las listas).
- **Las listas envejecen.** Se entrenaron con los vectores de un momento. Si el número de
  vectores cambia un 30 % (`vector_rebuild_fraction`) desde entonces, en cualquiera de las
  dos direcciones, `needs_maintenance()` lo avisa y `maintain()` vuelve a entrenar con los
  vivos y recalcula las listas por omisión.

## Lo que muestra `describe()`

Listas, probes, vectores con los que se entrenó, el tamaño de cada lista y el factor de
desbalance de FAISS (1 si todas las listas tienen lo mismo; cuanto mayor, más cuesta la
lista más llena).
