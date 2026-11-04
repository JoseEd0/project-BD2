"""Subconjuntos reproducibles del corpus ESCI en español para el experimento de texto.

Parten de los tres CSV que deja `benchmarks.esci.download` y producen, para cada tamaño, un
corpus más chico con el mismo formato, en el que las cuentas de Precision@10 y Recall@10
son exactas:

- **Cubetas de consultas.** Por el número de palabras de la consulta (`word_count`, la
  columna `palabras`). Entran las de exactamente 1 palabra,
  exactamente 3 y 5 o más; las de 2 y 4 no se usan.
- **Relevante = E.** Un producto es relevante para una consulta solo si el anotador lo
  calificó como exacto. Sustituto, complemento e irrelevante cuentan como no relevantes, y
  una consulta sin ningún exacto no entra: su Recall@10 no está definido.
- **Ningún juicio queda fuera.** Una consulta entra solo si caben en el subconjunto
  **todos** los productos que tiene juzgados, sea cual sea la etiqueta. Las consultas de
  cada cubeta se barajan con la semilla y se toman por turnos, una de cada cubeta, hasta
  llenar el cupo o el presupuesto de productos juzgados (`--max-judged-share` del tamaño).
- **Relleno.** El resto del subconjunto son productos del catálogo español elegidos al azar
  con la semilla entre los que **no** juzgó ninguna de las consultas elegidas.

Misma semilla, mismos subconjuntos. Como el orden de las consultas no depende del tamaño,
las consultas de un subconjunto chico suelen repetirse en los grandes.

    .venv/bin/python -m benchmarks.esci.subsets --corpus data/datasets/esci_es \\
        --sizes 1000 10000 100000 --queries-per-bucket 50

Cada subconjunto queda en `<output>/<tamaño>/`, por omisión `<corpus>/subconjuntos/`, con
`productos.csv`, `consultas.csv` (más la columna `cubeta`) y `juicios.csv`.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from benchmarks.common import QUERIES_STREAM, ROWS_STREAM, random_stream
from benchmarks.esci.corpus import (
    JUDGMENT_HEADER,
    JUDGMENTS_CSV,
    PRODUCT_HEADER,
    PRODUCTS_CSV,
    QUERIES_CSV,
    QUERY_HEADER,
    CorpusError,
    Judgment,
    Query,
    read_products,
    read_rows,
    write_rows,
)

SUBSETS_DIRECTORY = "subconjuntos"
SUBSET_QUERY_HEADER = (*QUERY_HEADER, "cubeta")

SHORT_QUERY_WORDS = 1
MEDIUM_QUERY_WORDS = 3
LONG_QUERY_MIN_WORDS = 5

DEFAULT_SIZES = (1000, 10_000, 100_000)
DEFAULT_QUERIES_PER_BUCKET = 50
# Con la mitad del subconjunto como tope para lo juzgado, al menos la otra mitad es
# catálogo que ninguna consulta elegida calificó: el índice tiene que separar los
# candidatos de ruido real, no solo de otros candidatos.
DEFAULT_MAX_JUDGED_SHARE = 0.5
DEFAULT_SEED = 20261104


class SubsetError(CorpusError):
    """Los parámetros piden un subconjunto que el corpus no puede dar."""


class QueryBucket(Enum):
    """Longitud de consulta que mide el experimento."""

    ONE_WORD = "1"
    THREE_WORDS = "3"
    FIVE_OR_MORE_WORDS = "5+"


def bucket_of(words: int) -> QueryBucket | None:
    """La cubeta de una consulta de `words` palabras; `None` si no entra en ninguna."""
    if words == SHORT_QUERY_WORDS:
        return QueryBucket.ONE_WORD
    if words == MEDIUM_QUERY_WORDS:
        return QueryBucket.THREE_WORDS
    if words >= LONG_QUERY_MIN_WORDS:
        return QueryBucket.FIVE_OR_MORE_WORDS
    return None


@dataclass(frozen=True)
class Corpus:
    """Identificadores de producto, consultas y juicios de un corpus o de un subconjunto.

    Los textos de los productos no se guardan aquí: con cientos de miles de productos se
    leen en flujo con `read_products` cuando hacen falta.
    """

    product_ids: tuple[str, ...]
    queries: tuple[Query, ...]
    judgments: Mapping[int, tuple[Judgment, ...]]

    def judged_products(self, query_id: int) -> frozenset[str]:
        """Todos los productos juzgados para la consulta, con cualquier etiqueta."""
        return frozenset(item.product_id for item in self.judgments.get(query_id, ()))

    def relevant_products(self, query_id: int) -> frozenset[str]:
        """Los productos que cuentan como relevantes (etiqueta E) para la consulta."""
        return frozenset(
            item.product_id for item in self.judgments.get(query_id, ()) if item.relevant
        )

    def queries_in(self, bucket: QueryBucket) -> tuple[Query, ...]:
        return tuple(query for query in self.queries if bucket_of(query.words) is bucket)


def load_corpus(directory: Path) -> Corpus:
    """Lee el corpus (o un subconjunto ya escrito) de los tres CSV de `directory`."""
    product_ids = tuple(row["id"] for row in read_rows(directory / PRODUCTS_CSV))
    queries = tuple(
        Query(int(row["query_id"]), row["consulta"], int(row["palabras"]))
        for row in read_rows(directory / QUERIES_CSV)
    )
    by_query: dict[int, list[Judgment]] = {}
    for row in read_rows(directory / JUDGMENTS_CSV):
        judgment = Judgment(int(row["query_id"]), row["product_id"], row["etiqueta"])
        by_query.setdefault(judgment.query_id, []).append(judgment)
    judgments = {query_id: tuple(items) for query_id, items in by_query.items()}
    return Corpus(product_ids, queries, judgments)


def qualifying_queries(corpus: Corpus, size: int) -> dict[QueryBucket, int]:
    """Cuántas consultas de cada cubeta podrían entrar solas en un subconjunto de `size`:
    tienen algún relevante y todos sus juzgados caben."""
    counts = dict.fromkeys(QueryBucket, 0)
    for query in _eligible_queries(corpus):
        if len(corpus.judged_products(query.identifier)) <= size:
            counts[_bucket(query)] += 1
    return counts


def select_subset(
    corpus: Corpus,
    size: int,
    queries_per_bucket: int,
    seed: int,
    max_judged_share: float = DEFAULT_MAX_JUDGED_SHARE,
) -> Corpus:
    """Un subconjunto de `size` productos con hasta `queries_per_bucket` consultas por
    cubeta, todos sus juicios dentro y el resto relleno al azar.

    Los productos salen en el orden del corpus y las consultas ordenadas por id.

    Raises:
        SubsetError: si un parámetro está fuera de rango o el corpus no tiene productos
            suficientes para rellenar.
    """
    _validate(size, queries_per_bucket, max_judged_share)
    budget = int(size * max_judged_share)
    chosen = _choose_queries(corpus, budget, queries_per_bucket, seed)
    return subset_with_queries(corpus, size, chosen, seed)


def subset_with_queries(
    corpus: Corpus, size: int, queries: Iterable[Query], seed: int
) -> Corpus:
    """Un subconjunto de `size` productos con todos los juzgados de `queries` y el resto
    relleno al azar con la semilla, entre los productos que ninguna de ellas juzgó.

    Los productos salen en el orden del corpus y las consultas ordenadas por id.

    Raises:
        SubsetError: si los productos juzgados no caben en `size` o el corpus no tiene
            productos suficientes para rellenar.
    """
    chosen = sorted(queries, key=lambda query: query.identifier)
    judged = _judged_by(corpus, chosen)
    if len(judged) > size:
        raise SubsetError(
            f"las consultas juzgan {len(judged)} productos y el subconjunto tiene {size}"
        )
    filler = _filler(corpus, judged, size - len(judged), seed)
    members = judged | filler
    return Corpus(
        product_ids=tuple(product for product in corpus.product_ids if product in members),
        queries=tuple(chosen),
        judgments={query.identifier: corpus.judgments[query.identifier] for query in chosen},
    )


def queries_named(corpus: Corpus, texts: Sequence[str]) -> tuple[Query, ...]:
    """Las consultas del corpus con exactamente esos textos, en el mismo orden.

    Raises:
        SubsetError: si alguno de los textos no es una consulta del corpus.
    """
    by_text = {query.text: query for query in corpus.queries}
    missing = [text for text in texts if text not in by_text]
    if missing:
        raise SubsetError(f"el corpus no tiene las consultas {missing}")
    return tuple(by_text[text] for text in texts)


def write_subset(subset: Corpus, corpus_directory: Path, destination: Path) -> None:
    """Escribe el subconjunto del experimento en `destination` con el formato del corpus.

    Las filas de producto se leen de `corpus_directory` en su orden; `consultas.csv` lleva
    además la columna `cubeta`.
    """
    _write_products_and_judgments(subset, corpus_directory, destination)
    write_rows(
        destination / QUERIES_CSV,
        SUBSET_QUERY_HEADER,
        (
            (query.identifier, query.text, query.words, _bucket(query).value)
            for query in subset.queries
        ),
    )


def write_as_corpus(subset: Corpus, corpus_directory: Path, destination: Path) -> None:
    """Escribe el subconjunto en `destination` con el formato exacto del corpus completo,
    sin la columna `cubeta`: admite consultas de cualquier número de palabras."""
    _write_products_and_judgments(subset, corpus_directory, destination)
    write_rows(
        destination / QUERIES_CSV,
        QUERY_HEADER,
        ((query.identifier, query.text, query.words) for query in subset.queries),
    )


def _write_products_and_judgments(
    subset: Corpus, corpus_directory: Path, destination: Path
) -> None:
    members = frozenset(subset.product_ids)
    products = (
        (product.identifier, product.title, product.brand, product.text)
        for product in read_products(corpus_directory / PRODUCTS_CSV)
        if product.identifier in members
    )
    write_rows(destination / PRODUCTS_CSV, PRODUCT_HEADER, products)
    write_rows(
        destination / JUDGMENTS_CSV,
        JUDGMENT_HEADER,
        (
            (item.query_id, item.product_id, item.label)
            for query in subset.queries
            for item in subset.judgments[query.identifier]
        ),
    )


def _validate(size: int, queries_per_bucket: int, max_judged_share: float) -> None:
    if size <= 0:
        raise SubsetError(f"el tamaño tiene que ser positivo y es {size}")
    if queries_per_bucket <= 0:
        raise SubsetError(f"hace falta al menos una consulta por cubeta, no {queries_per_bucket}")
    if not 0 < max_judged_share <= 1:
        raise SubsetError(f"la fracción juzgada va de 0 a 1 y es {max_judged_share}")


def _eligible_queries(corpus: Corpus) -> Iterator[Query]:
    """Consultas de alguna cubeta con al menos un producto relevante."""
    for query in corpus.queries:
        if bucket_of(query.words) is not None and corpus.relevant_products(query.identifier):
            yield query


def _choose_queries(corpus: Corpus, budget: int, quota: int, seed: int) -> list[Query]:
    """Consultas por turnos entre cubetas mientras quepan sus juzgados en `budget`.

    El orden de cada cubeta se baraja sobre todas sus consultas, no solo las que caben:
    así no depende del tamaño y los subconjuntos de una semilla comparten consultas.
    """
    generator = random_stream(seed, QUERIES_STREAM)
    eligible = list(_eligible_queries(corpus))
    pending: dict[QueryBucket, Iterator[Query]] = {}
    for bucket in QueryBucket:
        members = [query for query in eligible if bucket_of(query.words) is bucket]
        generator.shuffle(members)
        pending[bucket] = iter(members)
    chosen: list[Query] = []
    taken: Counter[QueryBucket] = Counter()
    judged: set[str] = set()
    while pending:
        for bucket in list(pending):
            query = _next_fitting(pending[bucket], corpus, judged, budget)
            if query is not None:
                chosen.append(query)
                judged |= corpus.judged_products(query.identifier)
                taken[bucket] += 1
            if query is None or taken[bucket] == quota:
                del pending[bucket]
    return chosen


def _next_fitting(
    candidates: Iterator[Query], corpus: Corpus, judged: set[str], budget: int
) -> Query | None:
    for query in candidates:
        if len(judged | corpus.judged_products(query.identifier)) <= budget:
            return query
    return None


def _judged_by(corpus: Corpus, queries: Iterable[Query]) -> frozenset[str]:
    judged: set[str] = set()
    for query in queries:
        judged |= corpus.judged_products(query.identifier)
    return frozenset(judged)


def _filler(corpus: Corpus, judged: frozenset[str], count: int, seed: int) -> frozenset[str]:
    population = [product for product in corpus.product_ids if product not in judged]
    if count > len(population):
        raise SubsetError(
            f"el corpus tiene {len(population)} productos de relleno y hacen falta {count}"
        )
    return frozenset(random_stream(seed, ROWS_STREAM).sample(population, count))


def _bucket(query: Query) -> QueryBucket:
    bucket = bucket_of(query.words)
    if bucket is None:
        raise SubsetError(f"la consulta {query.identifier} no está en ninguna cubeta")
    return bucket


def summary_table(corpus: Corpus, subsets: Mapping[int, Corpus]) -> str:
    """Tabla Markdown con una fila por tamaño: por cubeta, las consultas elegidas sobre las
    que cabrían solas; y los juicios E, los productos juzgados y los de relleno."""
    buckets = " | ".join(f"Cubeta {bucket.value} (elegidas / caben)" for bucket in QueryBucket)
    lines = [
        f"| Tamaño | {buckets} | Juicios E | Juzgados | Relleno |",
        "|---:|" + "---:|" * (len(QueryBucket) + 3),
    ]
    for size, subset in subsets.items():
        qualifying = qualifying_queries(corpus, size)
        chosen = " | ".join(
            f"{len(subset.queries_in(bucket))} / {qualifying[bucket]}" for bucket in QueryBucket
        )
        relevant = sum(len(subset.relevant_products(query.identifier)) for query in subset.queries)
        judged = len(_judged_by(subset, subset.queries))
        lines.append(
            f"| {size} | {chosen} | {relevant} | {judged} | {len(subset.product_ids) - judged} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True, help="carpeta de los CSV del corpus")
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--queries-per-bucket", type=int, default=DEFAULT_QUERIES_PER_BUCKET)
    parser.add_argument(
        "--max-judged-share",
        type=float,
        default=DEFAULT_MAX_JUDGED_SHARE,
        help="fracción del subconjunto que pueden ocupar los productos juzgados",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--output",
        type=Path,
        help=f"dónde escribir los subconjuntos; por omisión <corpus>/{SUBSETS_DIRECTORY}",
    )
    arguments = parser.parse_args()
    output = arguments.output or arguments.corpus / SUBSETS_DIRECTORY
    corpus = load_corpus(arguments.corpus)
    subsets: dict[int, Corpus] = {}
    for size in arguments.sizes:
        subsets[size] = select_subset(
            corpus, size, arguments.queries_per_bucket, arguments.seed, arguments.max_judged_share
        )
        write_subset(subsets[size], arguments.corpus, output / str(size))
    print(summary_table(corpus, subsets))
    print(f"Subconjuntos en {output}")


if __name__ == "__main__":
    main()
