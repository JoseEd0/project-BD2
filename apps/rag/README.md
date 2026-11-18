# RAG sobre papers: el corpus

Servicio de la opción A del Anexo A: un chat que responde con papers y cita la página de
donde sale cada afirmación. Este paquete (`rag`) es, por ahora, **el corpus**: baja los
papers, saca su texto página por página, lo limpia y lo corta en fragmentos con su paper y
su página. La ingesta en el gestor (BM25 y embeddings en HNSW, #85) y las respuestas (#86)
llegan después y leen lo que deja aquí.

```bash
uv sync --all-packages
uv run python -m rag.corpus build --data-dir data/datasets \
    --material Proyecto_Integrador_BD2.md --material docs/informe.pdf
```

Todo queda en `<data-dir>/rag_papers/`. La carpeta de datos sale de `--data-dir` o de la
variable `RAG_DATA_DIR`; nada de eso se versiona. Volver a correr el comando sigue desde
donde quedó y no duplica nada.

## Qué papers entran

**arXiv, categorías cs.DB y cs.IR**, solo con una licencia que deje servir el PDF:

| Licencia | ¿Entra? | Por qué |
|---|---|---|
| CC BY, CC BY-SA, CC0 (y la antigua «public domain» de CC) | sí | permiten copiar, redistribuir y adaptar |
| arXiv non-exclusive license (la que se elige por omisión) | no | solo autoriza a arXiv a distribuir el paper |
| CC BY-NC, BY-NC-SA, BY-ND, BY-NC-ND | no | los fragmentos son una adaptación (ND) y la aplicación no tiene por qué ser no comercial (NC) |

El API Atom de arXiv no dice la licencia; **OAI-PMH sí**: el formato `arXiv` trae
`<license>`, los autores, el resumen, la fecha de envío y las categorías (la primera es la
principal). Por eso la metadata se cosecha de `https://oaipmh.arxiv.org/oai` con
`ListRecords`, un conjunto por categoría (`cs:cs:DB`, `cs:cs:IR`), desde una fecha
(`--from-date`, por omisión 2024-01-01; filtra por la última vez que cambió el registro).

De lo cosechado entran los papers cuya **categoría principal** es cs.DB o cs.IR (un paper
de aprendizaje automático que solo se lista también en cs.IR no entra), con licencia
aceptada, **del más reciente al más antiguo y alternando entre las dos categorías** hasta
`--papers` (por omisión 1 500). El orden es determinista: la misma cosecha da los mismos
papers.

Se respetan los términos de uso de arXiv: una petición a la vez, al menos 3 s entre una y
otra (también para los PDF, que se piden a `export.arxiv.org`, el espejo para acceso
programático; `--request-delay` y `--pdf-delay`), `Retry-After` ante un 429 o un 503 y un
agente que describe el programa sin datos personales.

**Material del curso.** `--material` agrega documentos propios: el enunciado
(`Proyecto_Integrador_BD2.md`) y el informe de las Partes 1 y 2 (`docs/informe.pdf`), que
son del curso y del autor del proyecto. Se copian a `rag_papers/curso/` para que la
aplicación los sirva igual que un paper. En un Markdown, la «página» es la sección: se
corta en cada encabezado.

## Cómo se procesa

1. **Cosecha** (`arxiv.py`): todas las páginas de `ListRecords` con su `resumptionToken`,
   un registro por paper aunque esté en las dos categorías. Se guarda en `cosecha.json`
   junto con la consulta; si la consulta no cambia, la corrida siguiente no vuelve a pedirla
   (`--reharvest` la fuerza).
2. **Descarga** (`fetch.py`): un PDF que ya está en `pdf/` no se vuelve a pedir; se escribe
   en `<id>.pdf.parcial` y se renombra al terminar, así que una descarga cortada no queda
   como un PDF válido. Si la respuesta no empieza con `%PDF-` (una página de error), se
   registra como falla.
3. **Extracción** (`extraction.py`, `cleaning.py`), en paralelo por procesos (`--workers`):
   texto por página con pypdf; NFKC (ligaduras, letras matemáticas) y pares sustitutos
   unidos; se borran las líneas del borde de la página que se repiten en al menos la mitad
   de las páginas (encabezados, pies, números de página, comparados con los dígitos
   igualados) y el sello lateral de arXiv; las palabras partidas con guion al final de una
   línea se unen si la siguiente sigue en minúscula; los espacios se normalizan. **Un PDF
   dañado** (pypdf no puede leerlo) o **sin texto** (menos de 25 palabras por página: un
   escaneado) se registra en `fallas.jsonl` y se salta. El texto de cada documento se
   guarda en `paginas/<id>.json` con la huella del PDF y los parámetros de limpieza: solo se
   vuelve a extraer si alguno cambia.
4. **Fragmentos** (`chunking.py`): el documento se lee como una sola secuencia de
   oraciones, así que **ninguna oración se corta**, ni siquiera la que cruza un salto de
   página. Cada fragmento suma oraciones enteras hasta 220 palabras, sin bajar de 200 ni
   pasar de 500, y el siguiente repite las últimas oraciones del anterior (de 50 a 120
   palabras de solapamiento). Si el final del documento no llega a 200 palabras, el último
   fragmento toma oraciones del anterior. La única excepción a no cortar es una «oración»
   de más de 300 palabras (una tabla o una lista de referencias sin puntos), que se parte
   por palabras.
5. **Deduplicación** (`pipeline.py`): un PDF con la misma huella SHA-256 que otro ya
   aceptado no entra, y un fragmento cuyo texto ya está (mismo SHA-256) tampoco. Como la
   metadata y los fragmentos se reescriben enteros en cada corrida, volver a correr no
   duplica nada.

**Página de una cita.** Un fragmento guarda `page_start`, la página donde empieza su
primera oración, y `page_end`, la página donde termina la última. La cita abre el PDF en
`page_start`; el rango dice que el pasaje sigue en las páginas siguientes.

**Por qué 220 palabras.** El modelo de embeddings (multilingual-e5-small) trunca en 512
tokens. Medido con su tokenizador sobre 200 papers del corpus, el texto de estos papers
(fórmulas, tablas, siglas) da 1,9 tokens por palabra: con un objetivo de 300 palabras solo
el 50 % de los fragmentos entra entero en el modelo (mediana de 513 tokens); con 220, el
87 % (mediana de 394). El mínimo de 200 del enunciado deja poco margen hacia abajo, así
que 220 es el objetivo. El BM25 de la ingesta ve siempre el fragmento completo.

**Por qué 1 500 papers.** Con 220 palabras por fragmento, un paper de este corpus da una
mediana de 60 fragmentos (66 de media, por los apéndices largos): 1 500 papers son casi
100 000 fragmentos. multilingual-e5-small los calcula en CPU a 51 fragmentos por segundo
(medido sobre 3 000 fragmentos del corpus, Apple M4 Pro, 8 hilos de PyTorch), así que la
ingesta (#85) tarda unos 32 minutos; con 3 000 papers serían más de una hora y 3 h de
descarga al ritmo que pide arXiv. Cien mil filas es además la escala más grande que ya se
midió en el gestor para HNSW, IVF y BM25 (Partes 3 y 4). `--papers` la cambia.

## Qué queda en disco

```
rag_papers/
  cosecha.json        la cosecha de arXiv: consulta, fecha y todos los registros
  pdf/<id>.pdf        los PDF (los identificadores antiguos, cs/0101001, como cs_0101001)
  curso/              el material del curso, copiado
  paginas/<id>.json   el texto limpio de cada página, con la huella del archivo
  papers.jsonl        un documento por línea
  fragmentos.jsonl    un fragmento por línea
  fallas.jsonl        lo que no entró y por qué
  manifest.json       consulta, parámetros, conteos, bytes, distribución y tiempos
```

`papers.jsonl`:

| Campo | Contenido |
|---|---|
| `paper_id` | identificador de arXiv (`2501.01234`) o `curso:<nombre>` |
| `source` | `arxiv` o `curso` |
| `title`, `authors`, `year`, `abstract` | metadata de arXiv (en el material del curso, la del propio archivo) |
| `primary_category`, `categories` | `cs.DB`, `cs.IR`… (`curso` para el material del curso) |
| `license` | URL de la licencia (`null` en el material del curso) |
| `url` | la página del paper en arXiv |
| `file_path`, `media_type` | el archivo relativo a `rag_papers/` y su tipo (`application/pdf` o `text/markdown`) |
| `sha256`, `bytes` | huella y tamaño del archivo |
| `pages`, `empty_pages`, `words` | páginas (o secciones), cuántas quedaron sin texto y palabras extraídas |
| `chunks` | cuántos fragmentos dio |

`fragmentos.jsonl`:

| Campo | Contenido |
|---|---|
| `chunk_id` | `<paper_id>#<ordinal>`, estable entre corridas |
| `paper_id`, `ordinal` | el documento y el orden del fragmento dentro de él |
| `page_start`, `page_end` | página donde empieza y donde termina (desde 1) |
| `words`, `text` | palabras y texto del fragmento |
| `sha256` | huella del texto, con la que se deduplica |

`fallas.jsonl` trae `paper_id`, `stage` (`descarga`, `extracción` o `duplicado`) y
`reason`.

## La corrida

Con los valores por omisión y el material del curso, el 18 de noviembre de 2026. Las cifras
son las del manifiesto, versionado en
[`benchmarks/results/rag_corpus.json`](../../benchmarks/results/rag_corpus.json).

| Paso | Cuántos |
|---|---|
| registros cosechados (cs.DB y cs.IR desde 2024-01-01, 15 peticiones OAI-PMH) | 17 931 |
| con categoría principal cs.DB o cs.IR | 11 502 |
| de esos, con licencia que permite redistribuir | 5 461 (47 %) |
| elegidos (750 de cada categoría) | 1 500 |
| PDF bajados | 1 493 |
| fallas de descarga (HTTP 404: sin PDF en arXiv) | 7 |
| PDF dañados o sin texto | 0 y 0 |
| papers repetidos (mismo PDF) | 0 |
| documentos del curso | 2 |
| **documentos en el corpus** | **1 495** |
| páginas (y sin texto) | 26 365 (19) |
| palabras | 15 842 056 |
| **fragmentos** | **98 633** |
| fragmentos repetidos, descartados | 34 |
| «oraciones» de más de 300 palabras partidas | 1 452 |

Licencias de los 11 502 papers en las dos categorías: CC BY 5 044, arXiv no exclusiva
4 679, CC BY-NC-ND 782, CC BY-NC-SA 579, CC BY-SA 339, CC0 78 y uno sin licencia. Entre
los 1 493 elegidos quedan 1 401 CC BY, 81 CC BY-SA y 11 CC0. Como se eligen del más
reciente hacia atrás, los de cs.DB se enviaron entre octubre de 2025 y noviembre de 2026, y
los de cs.IR (más numerosos), entre julio y noviembre de 2026.

Un paper tiene una mediana de 14 páginas (el más largo, 256) y 60 fragmentos (el que más,
402). En disco: 2,8 GB de PDF, 168 MB de `fragmentos.jsonl` y 3 MB de `papers.jsonl`.

**Longitud de los fragmentos**, en palabras: mínimo 200, mediana 216, p95 267, máximo 499
y media 222. Todos están entre los límites; la cola larga son fragmentos que terminan en
una oración larga, típicamente una tabla.

![Histograma de la longitud de los fragmentos](../../benchmarks/figures/rag-fragmentos.png)

**Tiempos** (Apple M4 Pro, 12 núcleos): la cosecha, 1,5 min; la descarga, 90 min
(1 494 peticiones, 3,6 s cada una: los 3 s de pausa más la transferencia); la extracción,
70 s en 12 procesos; la fragmentación, 3 s. Una segunda corrida sobre el mismo directorio
tarda 26 s: no baja ni extrae nada (solo vuelve a pedir los 7 PDF que faltan) y deja
`papers.jsonl` y `fragmentos.jsonl` iguales.

Para redibujar la figura desde el manifiesto (necesita el extra `plots`):

```bash
uv run python -m rag.corpus figure --manifest benchmarks/results/rag_corpus.json \
    --output benchmarks/figures/rag-fragmentos.png
```

## Tests

`apps/rag/tests/` no usa la red: el OAI-PMH y el servidor de PDF son de mentira y los PDF
se escriben a mano en el test (`rag_pdf_factory.py`). Cubren el filtro de licencias, el
análisis de las respuestas OAI-PMH (registros borrados, `noRecordsMatch`, errores, token),
la pausa entre peticiones y los reintentos, los guiones de fin de línea, los encabezados
y pies repetidos, la página vacía, el PDF escaneado y el dañado, los límites de tamaño, el
solapamiento, que no se corte una oración, la página de cada fragmento, la deduplicación
al volver a correr y la reanudación después de una corrida cortada.

```bash
uv run pytest apps/rag -q
```
