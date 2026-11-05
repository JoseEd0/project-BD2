# HNSW (Hierarchical Navigable Small World)

Un grafo por niveles. Cada vector es un nodo del nivel 0 y sube a cada nivel siguiente con
probabilidad decreciente, así que los de arriba son pocos y están lejos entre sí. Una
búsqueda entra por arriba, baja de nivel en nivel acercándose a la consulta y en el
nivel 0 explora con una cola de `ef` candidatos. La estructura es `faiss.IndexHNSWFlat`.

```
nivel 2   ●───────────────●                 pocos nodos, saltos largos
nivel 1   ●─────●────●────●────●
nivel 0   ●─●─●─●─●─●─●─●─●─●─●─●─●         todos los nodos, hasta 2M vecinos
```

## Parámetros

| Parámetro | En SQL | Por omisión | Qué cambia |
|---|---|---|---|
| `M` | `WITH (M = n)` | 16 | vecinos por nodo (2M en el nivel 0): más recall y más memoria |
| `ef_construction` | `WITH (EF_CONSTRUCTION = n)` | 64 | candidatos al insertar: mejor grafo, construcción más lenta |
| `ef_search` | por consulta | 40 | candidatos al buscar: más recall, consulta más lenta |

Los tres por omisión son los de pgvector (`m`, `ef_construction`, `hnsw.ef_search`).

## Borrar con lápidas

El grafo de FAISS no permite quitar un nodo: los caminos que pasan por él se romperían.
Un borrado deja una **lápida**, un bit por nodo. La búsqueda la excluye con un
`IDSelectorNot(IDSelectorBitmap)`: el nodo sigue sirviendo de paso, pero nunca sale como
resultado. El mapa de bits se rehace solo cuando cambian las lápidas.

Cuando las lápidas llegan al 30 % de los nodos (`vector_rebuild_fraction`, el mismo umbral
que el resto del motor), `needs_maintenance()` lo avisa y `maintain()` reconstruye el grafo
con los vivos.

Los nodos tienen identificadores internos consecutivos y el índice guarda aparte de qué
fila es cada uno. Una fila borrada que vuelve —el `ROLLBACK` de un `DELETE`— entra como un
nodo nuevo, sin chocar con su lápida.

## Producto punto

El producto interno no es una distancia (un vector no es necesariamente el más parecido a
sí mismo), y el grafo que se construye con él conecta peor. FAISS lo acepta y el índice lo
ofrece, pero el coseno —producto interno entre vectores normalizados— es la métrica natural
de los histogramas TF-IDF. El experimento de la Parte 4 mide la diferencia.

## Lo que muestra `describe()`

`M`, `ef_construction`, `ef_search`, nodos, lápidas, cuántos nodos llegan a cada nivel y
el grado medio del nivel 0.
