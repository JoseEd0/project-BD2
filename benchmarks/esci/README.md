# Corpus de texto ESCI en español

Los tres módulos comparten un solo formato en disco:

| Módulo | Qué hace |
|---|---|
| [`corpus.py`](corpus.py) | nombres y columnas de los CSV, conteo de palabras, qué etiqueta es relevante |
| [`download.py`](download.py) | baja los parquet oficiales y escribe el corpus en español |
| [`subsets.py`](subsets.py) | saca los subconjuntos reproducibles del experimento |


El experimento de texto mide Precision@10 y Recall@10, y para eso hace falta saber qué
productos **debió** devolver cada consulta. `download.py` descarga el **Shopping Queries
Dataset** (ESCI) de Amazon: consultas reales de la tienda y, para cada una, productos que un
anotador calificó como **E**xacto, **S**ustituto, **C**omplemento o **I**rrelevante. Se
queda solo con la tienda de España.

```bash
.venv/bin/pip install -e ".[datasets]"     # pyarrow, solo para leer los parquet
.venv/bin/python -m benchmarks.esci.download --salida data/datasets/esci_es
```

| Argumento | Para qué |
|---|---|
| `--salida` | carpeta donde quedan los tres CSV |
| `--parquet` | dónde guardar los parquet originales; por omisión `<salida>/parquet/` |
| `--url` | carpeta remota de los parquet; por omisión, la del repositorio oficial |

Baja dos parquet (1,1 GB el de productos y 49 MB el de juicios, con los tres países) y los
deja en `--parquet`: si se vuelve a ejecutar y ya están completos, no los descarga otra vez.
Tarda un par de minutos. Escribe tres CSV con el formato que carga el gestor:

| Archivo | Columnas | Filas | Tamaño |
|---|---|---:|---:|
| `productos.csv` | `id, titulo, marca, texto` | 260 011 | 339 MB |
| `consultas.csv` | `query_id, consulta, palabras` | 15 180 | 451 KB |
| `juicios.csv` | `query_id, product_id, etiqueta` | 356 410 | 6,8 MB |

- **El documento** es el título, las viñetas, la descripción y la marca, en ese orden, sin
  etiquetas HTML, con las entidades (`&amp;`, `&nbsp;`) convertidas y un solo espacio entre
  palabras. Tiene en promedio **194 palabras** (mediana 150, máximo 1 055); el más largo
  ocupa 8,6 KB. 12 020 productos no traen marca.
- **Las palabras de una consulta** son los trozos que separan los espacios
  (`str.split()`): «funda iphone-12» son dos. La columna `palabras` es esa cuenta.
- **Las etiquetas**: 202 066 E, 89 059 S, 20 503 C y 44 782 I. Todas las consultas tienen
  al menos un exacto; en promedio, 23 productos juzgados y 13 exactos.

| Palabras | 1 | 2 | 3 | 4 | 5 o más |
|---|---:|---:|---:|---:|---:|
| consultas | 1 104 | 3 771 | 5 038 | 2 988 | 2 279 |

## Subconjuntos de 1 000, 10 000 y 100 000 productos

`benchmarks.esci.subsets` saca del corpus los subconjuntos del experimento, con el mismo
formato y la columna `cubeta` añadida en `consultas.csv`. No necesita `pyarrow`.

```bash
.venv/bin/python -m benchmarks.esci.subsets --corpus data/datasets/esci_es \
    --sizes 1000 10000 100000 --queries-per-bucket 50 --max-judged-share 0.5 --seed 20261104
```

Quedan en `data/datasets/esci_es/subconjuntos/<tamaño>/`. Las reglas:

- **Cubetas.** Consultas de exactamente 1 palabra, exactamente 3 y 5 o más. Las de 2 y 4
  no se usan.
- **Relevante = E.** Para Precision@10 y Recall@10 solo cuenta como relevante un producto
  calificado como exacto; sustituto, complemento e irrelevante son no relevantes. Una
  consulta sin ningún exacto no entra (su Recall@10 no estaría definido). Como cada
  consulta tiene unos 13 exactos, un top 10 perfecto da Recall@10 por debajo de 1.
- **Todos los juicios dentro.** Una consulta entra solo si caben en el subconjunto todos
  sus productos juzgados, con cualquier etiqueta. Así ningún juicio se refiere a un
  producto que no está, y un no relevante juzgado sigue ahí como distractor.
- **Elección.** Las consultas de cada cubeta se barajan con la semilla y se toman por
  turnos, una de cada cubeta, hasta llegar a `--queries-per-bucket` o hasta que los
  juzgados ocupan `--max-judged-share` del subconjunto (la mitad, por omisión).
- **Relleno.** El resto son productos del catálogo español elegidos con la semilla entre
  los que ninguna consulta elegida juzgó. Puede haber entre ellos algún relevante que nadie
  calificó: el juicio es el de Amazon, no uno exhaustivo.

Misma semilla, mismos subconjuntos. Con los valores por omisión:

| Productos | 1 palabra | 3 palabras | 5 o más | Juicios E | Juzgados | Relleno | `productos.csv` |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 000 | 8 | 8 | 8 | 290 | 500 | 500 | 1,3 MB |
| 10 000 | 50 | 50 | 50 | 1 979 | 3 427 | 6 573 | 13 MB |
| 100 000 | 50 | 50 | 50 | 1 979 | 3 427 | 96 573 | 130 MB |

Cualquier consulta de las tres cubetas cabe sola incluso en 1 000 productos (la que más
tiene juzga 67), pero no caben juntas: con unos 23 juzgados cada una, en 500 plazas entran 24. Por eso el de
1 000 tiene menos consultas, y las que tiene son de las que juzgan menos productos. Las
150 de 10 000 y 100 000 son las mismas, y las 24 de 1 000 están entre ellas.

**Licencia.** El Shopping Queries Dataset es de Amazon y se distribuye bajo
[Apache-2.0](https://github.com/amazon-science/esci-data/blob/main/LICENSE):
© Amazon.com, Inc. Referencia: Reddy et al., *Shopping Queries Dataset: A Large-Scale ESCI
Benchmark for Improving Product Search* (2022). Los CSV no están en el repositorio
(`data/` se ignora): quien los quiera, los descarga.
