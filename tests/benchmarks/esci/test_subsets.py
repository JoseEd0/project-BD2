"""Los subconjuntos del corpus ESCI, sobre un corpus sintético escrito en CSV.

No hace falta `pyarrow`: los subconjuntos se arman desde los CSV que deja el descargador.
"""

import csv
import random
from pathlib import Path

import pytest
from benchmarks.esci.corpus import read_products
from benchmarks.esci.subsets import (
    Corpus,
    QueryBucket,
    SubsetError,
    bucket_of,
    load_corpus,
    qualifying_queries,
    select_subset,
    summary_table,
    write_subset,
)

SEED = 11
PRODUCTS = 400
# Palabras de cada consulta sintética: varias de cada cubeta y algunas de 2 y 4 palabras,
# que no entran en ninguna.
QUERY_WORDS = (1, 1, 1, 1, 2, 2, 3, 3, 3, 3, 4, 5, 5, 6, 7, 8)
JUDGED_PER_QUERY = (4, 9)
WITHOUT_RELEVANT = 900
OVERSIZED = 901
OVERSIZED_JUDGMENTS = 120
LABELS = ("E", "S", "C", "I")


def write_csv(path: Path, header: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_corpus(directory: Path) -> Path:
    """Productos con comas y comillas en el texto, consultas de todas las longitudes, una
    sin ningún exacto y una con más juzgados de los que caben en un subconjunto chico."""
    generator = random.Random(SEED)
    product_ids = [f"P{number:04d}" for number in range(PRODUCTS)]
    products = [
        (pid, f'Título "{pid}", rojo', "Marca" if number % 3 else "", f"texto de {pid}, con coma")
        for number, pid in enumerate(product_ids)
    ]
    queries = [
        (number, " ".join(["palabra"] * words), words) for number, words in enumerate(QUERY_WORDS)
    ]
    judgments: list[tuple[object, ...]] = []
    for number, _, _ in queries:
        judged = generator.sample(product_ids, generator.randint(*JUDGED_PER_QUERY))
        judgments += [
            (number, pid, "E" if position == 0 else generator.choice(LABELS))
            for position, pid in enumerate(judged)
        ]
    queries.append((WITHOUT_RELEVANT, "sin ningún exacto", 3))
    judgments += [(WITHOUT_RELEVANT, pid, "S") for pid in product_ids[:3]]
    queries.append((OVERSIZED, "una sola", 1))
    judgments += [(OVERSIZED, pid, "E") for pid in product_ids[-OVERSIZED_JUDGMENTS:]]
    directory.mkdir(parents=True, exist_ok=True)
    write_csv(directory / "productos.csv", ("id", "titulo", "marca", "texto"), products)
    write_csv(directory / "consultas.csv", ("query_id", "consulta", "palabras"), queries)
    write_csv(directory / "juicios.csv", ("query_id", "product_id", "etiqueta"), judgments)
    return directory


@pytest.fixture
def corpus_directory(tmp_path: Path) -> Path:
    return write_corpus(tmp_path / "corpus")


@pytest.fixture
def corpus(corpus_directory: Path) -> Corpus:
    return load_corpus(corpus_directory)


def judged_union(subset: Corpus) -> set[str]:
    return {pid for query in subset.queries for pid in subset.judged_products(query.identifier)}


@pytest.mark.parametrize(
    ("words", "bucket"),
    [
        (0, None),
        (1, QueryBucket.ONE_WORD),
        (2, None),
        (3, QueryBucket.THREE_WORDS),
        (4, None),
        (5, QueryBucket.FIVE_OR_MORE_WORDS),
        (12, QueryBucket.FIVE_OR_MORE_WORDS),
    ],
)
def test_the_bucket_depends_only_on_the_word_count(words: int, bucket: QueryBucket | None):
    assert bucket_of(words) is bucket


def test_the_corpus_is_read_from_its_three_csvs(corpus: Corpus):
    assert len(corpus.product_ids) == PRODUCTS
    assert len(corpus.queries) == len(QUERY_WORDS) + 2
    assert corpus.relevant_products(WITHOUT_RELEVANT) == frozenset()
    assert len(corpus.judged_products(WITHOUT_RELEVANT)) == 3
    assert len(corpus.relevant_products(OVERSIZED)) == OVERSIZED_JUDGMENTS
    assert corpus.judged_products(-1) == frozenset()


def test_only_exact_judgments_are_relevant(corpus: Corpus):
    for query in corpus.queries:
        exact = {
            item.product_id for item in corpus.judgments[query.identifier] if item.label == "E"
        }
        assert corpus.relevant_products(query.identifier) == exact


def test_the_same_seed_gives_the_same_subset(corpus: Corpus):
    first = select_subset(corpus, 100, 3, SEED)
    assert select_subset(corpus, 100, 3, SEED) == first
    assert select_subset(corpus, 100, 3, SEED + 1).product_ids != first.product_ids


def test_every_judged_product_of_a_chosen_query_is_inside(corpus: Corpus):
    subset = select_subset(corpus, 100, 3, SEED)
    members = set(subset.product_ids)
    assert len(members) == len(subset.product_ids) == 100
    for query in subset.queries:
        assert subset.judged_products(query.identifier) <= members
        assert subset.judgments[query.identifier] == corpus.judgments[query.identifier]


def test_judged_products_stay_within_their_share_and_the_rest_is_filler(corpus: Corpus):
    subset = select_subset(corpus, 100, 3, SEED, max_judged_share=0.3)
    assert 0 < len(judged_union(subset)) <= 30
    assert subset.queries


def test_products_keep_the_corpus_order(corpus: Corpus):
    subset = select_subset(corpus, 100, 3, SEED)
    positions = [corpus.product_ids.index(pid) for pid in subset.product_ids]
    assert positions == sorted(positions)


def test_each_bucket_gets_its_quota_and_other_lengths_stay_out(corpus: Corpus):
    subset = select_subset(corpus, 300, 2, SEED, max_judged_share=1.0)
    assert {bucket: len(subset.queries_in(bucket)) for bucket in QueryBucket} == dict.fromkeys(
        QueryBucket, 2
    )
    assert all(bucket_of(query.words) is not None for query in subset.queries)
    assert [query.identifier for query in subset.queries] == sorted(
        query.identifier for query in subset.queries
    )


def test_a_query_without_an_exact_product_never_enters(corpus: Corpus):
    subset = select_subset(corpus, PRODUCTS, len(QUERY_WORDS), SEED, max_judged_share=1.0)
    assert WITHOUT_RELEVANT not in {query.identifier for query in subset.queries}


def test_a_query_enters_only_if_all_its_judged_products_fit(corpus: Corpus):
    small = select_subset(
        corpus, OVERSIZED_JUDGMENTS - 1, len(QUERY_WORDS), SEED, max_judged_share=1.0
    )
    assert OVERSIZED not in {query.identifier for query in small.queries}
    assert qualifying_queries(corpus, OVERSIZED_JUDGMENTS - 1)[QueryBucket.ONE_WORD] == 4
    assert qualifying_queries(corpus, OVERSIZED_JUDGMENTS)[QueryBucket.ONE_WORD] == 5
    whole = select_subset(corpus, PRODUCTS, len(QUERY_WORDS), SEED, max_judged_share=1.0)
    assert OVERSIZED in {query.identifier for query in whole.queries}


def test_a_corpus_without_usable_queries_gives_only_filler(tmp_path: Path):
    directory = tmp_path / "vacío"
    directory.mkdir()
    write_csv(
        directory / "productos.csv",
        ("id", "titulo", "marca", "texto"),
        [(f"P{n}", "t", "m", "x") for n in range(10)],
    )
    write_csv(
        directory / "consultas.csv", ("query_id", "consulta", "palabras"), [(1, "dos palabras", 2)]
    )
    write_csv(directory / "juicios.csv", ("query_id", "product_id", "etiqueta"), [(1, "P0", "E")])
    subset = select_subset(load_corpus(directory), 4, 5, SEED)
    assert subset.queries == ()
    assert len(subset.product_ids) == 4
    assert dict(subset.judgments) == {}


def test_a_single_product_subset_is_only_filler_when_nothing_fits(corpus: Corpus):
    subset = select_subset(corpus, 1, 3, SEED)
    assert subset.queries == ()
    assert len(subset.product_ids) == 1


@pytest.mark.parametrize(
    ("size", "quota", "share"),
    [(0, 3, 0.5), (100, 0, 0.5), (100, 3, 0.0), (100, 3, 1.5), (PRODUCTS + 1, 3, 0.5)],
)
def test_impossible_parameters_are_rejected(corpus: Corpus, size: int, quota: int, share: float):
    with pytest.raises(SubsetError):
        select_subset(corpus, size, quota, SEED, max_judged_share=share)


def test_a_written_subset_reads_back_the_same(
    corpus: Corpus, corpus_directory: Path, tmp_path: Path
):
    subset = select_subset(corpus, 100, 3, SEED)
    destination = tmp_path / "subconjuntos" / "100"
    write_subset(subset, corpus_directory, destination)
    assert load_corpus(destination) == subset
    original = {
        product.identifier: product for product in read_products(corpus_directory / "productos.csv")
    }
    assert list(read_products(destination / "productos.csv")) == [
        original[pid] for pid in subset.product_ids
    ]


def test_the_written_queries_carry_their_bucket(
    corpus: Corpus, corpus_directory: Path, tmp_path: Path
):
    subset = select_subset(corpus, 300, 2, SEED, max_judged_share=1.0)
    write_subset(subset, corpus_directory, tmp_path / "300")
    with (tmp_path / "300" / "consultas.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["query_id", "consulta", "palabras", "cubeta"]
    assert sorted(row["cubeta"] for row in rows) == ["1", "1", "3", "3", "5+", "5+"]
    assert all(bucket_of(int(row["palabras"])) is QueryBucket(row["cubeta"]) for row in rows)


def test_the_summary_has_one_row_per_size(corpus: Corpus):
    subsets = {
        size: select_subset(corpus, size, 2, SEED, max_judged_share=1.0) for size in (100, 300)
    }
    lines = summary_table(corpus, subsets).splitlines()
    assert len(lines) == 2 + 2
    assert lines[3].startswith("| 300 | 2 / 5 | 2 / 4 | 2 / 5 |")
