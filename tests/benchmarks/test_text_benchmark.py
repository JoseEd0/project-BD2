"""El experimento de texto, en pequeño y sin PostgreSQL.

No mide nada: comprueba que el experimento sigue funcionando sobre un subconjunto escrito a
mano, que las cuentas de Precision@k y Recall@k son las del libro y que sus resultados se
guardan y se dibujan. Sin `BENCH_POSTGRES_DSN`, GIN se omite.
"""

import json
from pathlib import Path

import pytest
from benchmarks.esci.corpus import (
    JUDGMENT_HEADER,
    JUDGMENTS_CSV,
    PRODUCT_HEADER,
    PRODUCTS_CSV,
    QUERIES_CSV,
    write_rows,
)
from benchmarks.esci.subsets import SUBSET_QUERY_HEADER, SUBSETS_DIRECTORY, QueryBucket
from benchmarks.plots import text_search
from benchmarks.plots.drawing import draw
from benchmarks.text_benchmark import (
    ALL_QUERIES,
    BM25,
    BUCKET_OPERATIONS,
    BUILD,
    BUILD_MEMORY,
    GIN,
    INDEX_SPACE,
    INVERTED,
    QUERY_MEMORY,
    TF_IDF,
    MissingSubsetError,
    Settings,
    jaccard,
    load_workload,
    precision_at_k,
    recall_at_k,
    run,
    subset_directory,
)

SIZE = 12
K = 3
REPETITIONS = 2

PRODUCTS = [
    ("P01", "Cable USB tipo C de carga rápida, dos metros"),
    ("P02", "Cable USB tipo C trenzado para el móvil"),
    ("P03", "Cable HDMI de alta velocidad"),
    ("P04", "Funda roja para iPhone de silicona"),
    ("P05", "Funda transparente para iPhone con borde rojo"),
    ("P06", "Protector de pantalla para iPhone"),
    ("P07", "Lámpara de escritorio con luz cálida"),
    ("P08", "Taza de café de cerámica blanca"),
    ("P09", "Cargador USB de pared con dos puertos"),
    ("P10", "Auriculares inalámbricos con estuche de carga"),
    ("P11", "Mochila para portátil resistente al agua"),
    ("P12", "Libro de recetas de cocina peruana"),
]
QUERIES = [
    (1, "cable", 1, QueryBucket.ONE_WORD),
    (2, "funda iphone roja", 3, QueryBucket.THREE_WORDS),
    (3, "cable usb tipo c para carga", 6, QueryBucket.FIVE_OR_MORE_WORDS),
    (4, "lampara", 1, QueryBucket.ONE_WORD),
]
JUDGMENTS = [
    (1, "P01", "E"),
    (1, "P02", "E"),
    (1, "P03", "E"),
    (1, "P09", "S"),
    (2, "P04", "E"),
    (2, "P05", "E"),
    (2, "P06", "C"),
    (3, "P01", "E"),
    (3, "P02", "E"),
    (3, "P09", "I"),
    (4, "P07", "E"),
]


def write_subset(corpus: Path) -> None:
    directory = corpus / SUBSETS_DIRECTORY / str(SIZE)
    write_rows(
        directory / PRODUCTS_CSV,
        PRODUCT_HEADER,
        ((identifier, text.split(",")[0], "", text) for identifier, text in PRODUCTS),
    )
    write_rows(
        directory / QUERIES_CSV,
        SUBSET_QUERY_HEADER,
        ((identifier, text, words, bucket.value) for identifier, text, words, bucket in QUERIES),
    )
    write_rows(directory / JUDGMENTS_CSV, JUDGMENT_HEADER, JUDGMENTS)


def settings(queries_per_bucket: int | None = None) -> Settings:
    return Settings(
        k=K,
        repetitions=REPETITIONS,
        build_repetitions=REPETITIONS,
        queries_per_bucket=queries_per_bucket,
        k1=1.2,
        b=0.75,
        language="es",
        postgres_dsn=None,
        postgres_configuration="spanish",
        ts_rank_normalization=0,
    )


def test_precision_counts_the_relevant_among_the_first_k_over_k():
    relevant = frozenset({"a", "c", "x"})
    assert precision_at_k(["a", "b", "c", "d"], relevant, 4) == 0.5
    assert precision_at_k(["a", "b", "c", "d"], relevant, 2) == 0.5
    assert precision_at_k(["a"], relevant, 4) == 0.25


def test_recall_counts_the_relevant_among_the_first_k_over_all_relevant():
    relevant = frozenset({"a", "c", "x", "y"})
    assert recall_at_k(["a", "b", "c", "d"], relevant, 4) == 0.5
    assert recall_at_k(["a", "b", "c", "d"], relevant, 1) == 0.25
    assert recall_at_k([], relevant, 10) == 0.0


def test_recall_without_relevant_products_is_undefined():
    with pytest.raises(ValueError, match="relevantes"):
        recall_at_k(["a"], frozenset(), 10)


def test_jaccard_of_candidate_sets():
    assert jaccard(frozenset({1, 2, 3}), frozenset({2, 3, 4})) == 0.5
    assert jaccard(frozenset(), frozenset()) == 1.0
    assert jaccard(frozenset({1}), frozenset()) == 0.0


def test_a_missing_subset_says_how_to_build_it(tmp_path: Path):
    with pytest.raises(MissingSubsetError, match=r"benchmarks\.esci\.subsets"):
        load_workload(subset_directory(tmp_path, SIZE), None)


def test_the_workload_keeps_the_csv_order_and_the_buckets(tmp_path: Path):
    write_subset(tmp_path)
    workload = load_workload(subset_directory(tmp_path, SIZE), None)
    assert workload.product_ids == tuple(identifier for identifier, _ in PRODUCTS)
    assert [query.text for query in workload.queries[QueryBucket.ONE_WORD]] == ["cable", "lampara"]
    assert workload.relevant[2] == frozenset({"P04", "P05"})
    limited = load_workload(subset_directory(tmp_path, SIZE), 1)
    assert [query.text for query in limited.queries[QueryBucket.ONE_WORD]] == ["cable"]


def test_the_experiment_measures_both_rankings_and_skips_gin(tmp_path: Path):
    write_subset(tmp_path)
    report = run(tmp_path, [SIZE], settings(), tmp_path / "trabajo")
    measured = {(item.technique, item.operation): item for item in report.measurements}
    assert {(INVERTED, BUILD), (INVERTED, INDEX_SPACE), (INVERTED, BUILD_MEMORY)} <= measured.keys()
    for technique in (TF_IDF, BM25):
        for operation in (*BUCKET_OPERATIONS.values(), ALL_QUERIES):
            assert measured[(technique, operation)].milliseconds > 0
        assert measured[(technique, QUERY_MEMORY)].extra["kib"] > 0
    assert not any(item.technique == GIN for item in report.measurements)
    assert measured[(INVERTED, INDEX_SPACE)].extra["kib"] > 0
    assert len(measured[(INVERTED, BUILD)].extra["construcciones_ms"]) == REPETITIONS
    passes = measured[(BM25, BUCKET_OPERATIONS[QueryBucket.ONE_WORD])].extra["pasadas_ms"]
    assert len(passes) == REPETITIONS
    assert report.parameters["postgres"] is False


def test_the_relevance_is_the_mean_over_the_bucket(tmp_path: Path):
    """«cable» está en P01, P02 y P03, que son sus tres exactos: con k = 3 los dos
    rankings los devuelven todos. «lampara» solo está en P07, su único exacto."""
    write_subset(tmp_path)
    report = run(tmp_path, [SIZE], settings(), tmp_path / "trabajo")
    one_word = BUCKET_OPERATIONS[QueryBucket.ONE_WORD]
    for technique in (TF_IDF, BM25):
        bucket = next(
            item.extra
            for item in report.measurements
            if item.technique == technique and item.operation == one_word
        )
        assert bucket["consultas"] == 2
        assert bucket["recall_en_k"] == 1.0
        assert bucket["precision_en_k"] == pytest.approx((3 / 3 + 1 / 3) / 2)
        assert bucket["candidatos_promedio"] == 2.0
        assert [query["query_id"] for query in bucket["por_consulta"]] == [1, 4]
        assert [query["postings_leidos"] for query in bucket["por_consulta"]] == [3, 1]


def test_the_results_are_saved_and_drawn(tmp_path: Path):
    write_subset(tmp_path)
    report = run(tmp_path, [SIZE], settings(), tmp_path / "trabajo")
    saved = json.loads(report.save(tmp_path / "resultados").read_text(encoding="utf-8"))
    assert saved["name"] == "texto"
    assert len(saved["measurements"]) == len(report.measurements)
    assert "por_consulta" not in report.render()
    pytest.importorskip("matplotlib")
    for figure in text_search.FIGURES:
        assert draw(figure, saved, tmp_path / "figuras").stat().st_size > 0
