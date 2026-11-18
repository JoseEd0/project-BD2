"""El experimento de las columnas VECTOR(n), en pequeño: no mide nada, comprueba que sigue
funcionando y que la mezcla de gaussianas es la que dice su documentación."""

from pathlib import Path

import numpy as np
import pytest
from benchmarks.vector_benchmark import FilteredTruth, Settings, run
from benchmarks.vectors.mixture import Mixture
from benchmarks.vectors.names import (
    BUILD,
    CURVE,
    FILTERED_QUERY,
    LOAD,
    QUERY,
    SPACE,
    SQL_EXACT,
    SQL_HNSW,
    SQL_IVF,
    TABLE_LOAD,
)

MIXTURE = Mixture(dimension=16, clusters=8, intrinsic_dimension=3, spread=1.0, noise=0.5, seed=3)
SIZES = (60, 120)


def test_the_mixture_is_seeded_normalized_and_nested():
    small, small_groups = MIXTURE.collection(50)
    large, large_groups = MIXTURE.collection(200)

    assert np.array_equal(small, large[:50])
    assert np.array_equal(small_groups, large_groups[:50])
    assert np.allclose(np.linalg.norm(large, axis=1), 1.0, atol=1e-6)
    assert large.dtype == np.float32


def test_the_queries_are_not_in_the_collection():
    vectors, _ = MIXTURE.collection(200)
    queries = MIXTURE.queries(10)

    assert not (queries @ vectors.T > 1 - 1e-6).any()


def test_rows_of_a_group_are_closer_than_rows_of_different_groups():
    vectors, groups = MIXTURE.collection(400)
    similarities = vectors @ vectors.T
    same = groups[:, None] == groups[None, :]
    np.fill_diagonal(same, False)
    different = groups[:, None] != groups[None, :]

    assert similarities[same].mean() > similarities[different].mean() + 0.3


def test_the_filtered_truth_only_counts_rows_of_the_part():
    vectors = np.eye(4, dtype=np.float32)[[0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3]]
    queries = np.array([[1, 0, 0, 0]], dtype=np.float32)
    truth = FilteredTruth(vectors, queries, k=1)

    # La consulta 0 filtra `id % 10 == 0`: solo la fila de id 10, en la posición 9.
    assert truth.recall(0, [9]) == 1.0


def test_a_small_run_measures_every_technique(tmp_path: Path):
    settings = Settings(
        sizes=SIZES,
        mixture=MIXTURE,
        queries=6,
        filter_queries=4,
        repetitions=1,
        k=3,
        threads=1,
        ef_search=(10, 40),
        probes=(1, 4),
    )

    report = run(settings, tmp_path)

    measured = {(item.technique, item.operation, item.dataset_size) for item in report.measurements}
    for size in SIZES:
        assert (TABLE_LOAD, LOAD, size) in measured
        for technique in (SQL_HNSW, SQL_IVF):
            for operation in (BUILD, SPACE, QUERY, FILTERED_QUERY):
                assert (technique, operation, size) in measured
        assert (SQL_EXACT, QUERY, size) in measured
    curves = [item for item in report.measurements if item.operation == CURVE]
    assert {item.dataset_size for item in curves} == {max(SIZES)}
    exact = [item for item in report.measurements if (item.technique, item.operation) == (SQL_EXACT, QUERY)]
    assert all(item.extra["recall_en_k"] == pytest.approx(1.0) for item in exact)
