"""Descarga el catálogo de Amazon en español con consultas reales y juicios de relevancia.

Los datos son el **Shopping Queries Dataset** (ESCI) que publica Amazon: consultas que la
gente escribió en la tienda y, para cada una, productos que un anotador calificó como
**E**xacto, **S**ustituto, **C**omplemento o **I**rrelevante. Del corpus se conserva solo
la tienda de España (`product_locale = 'es'`). Es lo que hace falta para medir
Precision@10 y Recall@10 en la búsqueda de texto: qué productos debió devolver cada
consulta.

    .venv/bin/pip install -e ".[datasets]"
    .venv/bin/python -m benchmarks.esci.download --salida data/datasets/esci_es

Deja en la carpeta de salida tres CSV con el formato que carga el gestor:

- `productos.csv` (`id, titulo, marca, texto`): el texto del documento es el título, las
  viñetas, la descripción sin HTML y la marca, con los espacios normalizados;
- `consultas.csv` (`query_id, consulta, palabras`): `palabras` cuenta los trozos que deja
  `str.split()`, es decir, lo que separan los espacios;
- `juicios.csv` (`query_id, product_id, etiqueta`), con la etiqueta E, S, C o I.

Los parquet originales (unos 1,1 GB entre los dos, con todos los países) quedan en
`--parquet`, por omisión `<salida>/parquet/`, y no se vuelven a bajar si ya están completos.

Licencia de los datos: Apache-2.0, © Amazon.com, Inc. (github.com/amazon-science/esci-data).
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from benchmarks.esci.corpus import (
    JUDGMENT_HEADER,
    JUDGMENTS_CSV,
    LABELS,
    PRODUCT_HEADER,
    PRODUCTS_CSV,
    QUERIES_CSV,
    QUERY_HEADER,
    CorpusError,
    Judgment,
    Product,
    Query,
    word_count,
    write_rows,
)
from benchmarks.remote import download

DEFAULT_BASE_URL = (
    "https://media.githubusercontent.com/media/amazon-science/esci-data/main/"
    "shopping_queries_dataset"
)
PRODUCTS_PARQUET = "shopping_queries_dataset_products.parquet"
EXAMPLES_PARQUET = "shopping_queries_dataset_examples.parquet"
PARQUET_DIRECTORY = "parquet"
SPANISH_LOCALE = "es"
PARQUET_BATCH_ROWS = 65_536


LOCALE_COLUMN = "product_locale"
PRODUCT_COLUMNS = (
    "product_id",
    "product_title",
    "product_bullet_point",
    "product_description",
    "product_brand",
    LOCALE_COLUMN,
)
EXAMPLE_COLUMNS = ("query_id", "query", "product_id", "esci_label", LOCALE_COLUMN)


class CorpusDownloadError(CorpusError):
    """El corpus descargado no tiene la forma que espera este guion."""


@dataclass(frozen=True, slots=True)
class CorpusSummary:
    """Cuántas filas escribió cada CSV y cuántos juicios se descartaron."""

    products: int
    queries: int
    judgments: int
    discarded_judgments: int


class _TextCollector(HTMLParser):
    """Junta el texto de un fragmento HTML; cada etiqueta separa palabras."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pieces: list[str] = []

    def handle_data(self, data: str) -> None:
        self.pieces.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.pieces.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.pieces.append(" ")


def plain_text(fragment: str | None) -> str:
    """El texto de un fragmento HTML, sin etiquetas, sin entidades y con un espacio entre
    palabras. `None` y el texto vacío dan `""`."""
    if not fragment:
        return ""
    collector = _TextCollector()
    collector.feed(fragment)
    collector.close()
    return " ".join("".join(collector.pieces).split())


def product_of(row: dict[str, Any]) -> Product:
    """El producto de una fila del parquet de productos.

    El documento junta, en este orden, título, viñetas, descripción y marca.
    """
    title = plain_text(row["product_title"])
    brand = plain_text(row["product_brand"])
    parts = (
        title,
        plain_text(row["product_bullet_point"]),
        plain_text(row["product_description"]),
        brand,
    )
    text = " ".join(part for part in parts if part)
    return Product(identifier=row["product_id"], title=title, brand=brand, text=text)


def read_parquet_products(parquet: Path, locale: str) -> Iterator[Product]:
    """Los productos de un país, en el orden del parquet."""
    for row in _parquet_rows(parquet, PRODUCT_COLUMNS, locale):
        yield product_of(row)


def read_examples(
    parquet: Path, locale: str, known_products: frozenset[str]
) -> tuple[list[Query], list[Judgment], int]:
    """Consultas y juicios de un país, solo sobre productos conocidos.

    Devuelve las consultas con al menos un juicio ordenadas por id, los juicios ordenados
    por consulta y producto, y cuántos juicios se descartaron: los de productos que no
    están en el catálogo y los repetidos (se queda el primero).

    Raises:
        CorpusDownloadError: si un juicio trae una etiqueta fuera de E, S, C o I.
    """
    queries: dict[int, Query] = {}
    judgments: dict[tuple[int, str], Judgment] = {}
    discarded = 0
    for row in _parquet_rows(parquet, EXAMPLE_COLUMNS, locale):
        judgment = Judgment(row["query_id"], row["product_id"], row["esci_label"])
        if judgment.label not in LABELS:
            raise CorpusDownloadError(f"etiqueta desconocida {judgment.label!r}")
        key = (judgment.query_id, judgment.product_id)
        if judgment.product_id not in known_products or key in judgments:
            discarded += 1
            continue
        judgments[key] = judgment
        text = _normalized(row["query"])
        queries.setdefault(judgment.query_id, Query(judgment.query_id, text, word_count(text)))
    ordered_queries = [queries[identifier] for identifier in sorted(queries)]
    ordered_judgments = [judgments[key] for key in sorted(judgments)]
    return ordered_queries, ordered_judgments, discarded


def build_corpus(products_parquet: Path, examples_parquet: Path, output: Path) -> CorpusSummary:
    """Escribe los tres CSV del corpus en español a partir de los parquet originales."""
    output.mkdir(parents=True, exist_ok=True)
    known: set[str] = set()

    def remembered(products: Iterable[Product]) -> Iterator[Sequence[object]]:
        for product in products:
            known.add(product.identifier)
            yield (product.identifier, product.title, product.brand, product.text)

    products = write_rows(
        output / PRODUCTS_CSV,
        PRODUCT_HEADER,
        remembered(read_parquet_products(products_parquet, SPANISH_LOCALE)),
    )
    queries, judgments, discarded = read_examples(
        examples_parquet, SPANISH_LOCALE, frozenset(known)
    )
    write_rows(
        output / QUERIES_CSV,
        QUERY_HEADER,
        ((query.identifier, query.text, query.words) for query in queries),
    )
    write_rows(
        output / JUDGMENTS_CSV,
        JUDGMENT_HEADER,
        ((item.query_id, item.product_id, item.label) for item in judgments),
    )
    return CorpusSummary(products, len(queries), len(judgments), discarded)


def _parquet_rows(path: Path, columns: Sequence[str], locale: str) -> Iterator[dict[str, Any]]:
    """Filas de un país, por lotes para no tener el parquet entero en memoria.

    `pyarrow` es un extra opcional (`.[datasets]`): se importa solo al leer un parquet.
    """
    import pyarrow.compute as compute
    import pyarrow.parquet as parquet

    source = parquet.ParquetFile(path)
    for batch in source.iter_batches(batch_size=PARQUET_BATCH_ROWS, columns=list(columns)):
        local = batch.filter(compute.equal(batch.column(LOCALE_COLUMN), locale))
        yield from local.to_pylist()


def _normalized(text: str) -> str:
    return " ".join(text.split())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--salida", type=Path, required=True, help="carpeta de los CSV")
    parser.add_argument(
        "--parquet",
        type=Path,
        help=f"dónde guardar los parquet originales; por omisión <salida>/{PARQUET_DIRECTORY}",
    )
    parser.add_argument(
        "--url", default=DEFAULT_BASE_URL, help="carpeta remota de los parquet del dataset"
    )
    arguments = parser.parse_args()
    cache = arguments.parquet or arguments.salida / PARQUET_DIRECTORY
    products = download(f"{arguments.url}/{PRODUCTS_PARQUET}", cache / PRODUCTS_PARQUET)
    examples = download(f"{arguments.url}/{EXAMPLES_PARQUET}", cache / EXAMPLES_PARQUET)
    summary = build_corpus(products, examples, arguments.salida)
    print(f"{summary.products} productos en {arguments.salida / PRODUCTS_CSV}")
    print(f"{summary.queries} consultas en {arguments.salida / QUERIES_CSV}")
    print(
        f"{summary.judgments} juicios en {arguments.salida / JUDGMENTS_CSV} "
        f"({summary.discarded_judgments} descartados)"
    )
    print("Shopping Queries Dataset, licencia Apache-2.0: © Amazon.com, Inc.")


if __name__ == "__main__":
    main()
