"""IVF: cuántas listas, cuántas se sondean, borrado real y reentrenamiento."""

import numpy as np
import pytest

from index.ivf import IVFIndex, default_list_count, default_probe_count
from index.vector import FlatIndex, VectorMetric

DIMENSION = 8
ROWS = 2_000
K = 10


@pytest.fixture
def vectors(clustered_vectors) -> np.ndarray:
    return clustered_vectors(ROWS, DIMENSION, 1)


@pytest.fixture
def index(vectors) -> IVFIndex:
    built = IVFIndex(DIMENSION, VectorMetric.EUCLIDEAN)
    built.add(np.arange(ROWS), vectors)
    return built


@pytest.mark.parametrize(
    ("vectors", "lists"), [(0, 1), (1, 1), (38, 1), (1_000, 25), (10_000, 100), (100_000, 316)]
)
def test_lists_are_the_square_root_without_starving_k_means(vectors, lists):
    assert default_list_count(vectors) == lists


@pytest.mark.parametrize(("lists", "probes"), [(1, 1), (25, 5), (316, 18)])
def test_probes_are_the_square_root_of_the_lists(lists, probes):
    assert default_probe_count(lists) == probes


def test_every_vector_lands_in_one_list(index):
    shape = index.describe()

    assert shape["lists"] == default_list_count(ROWS)
    assert shape["probes"] == default_probe_count(shape["lists"])
    assert sum(shape["list_sizes"]) == ROWS
    assert shape["imbalance"] >= 1.0


def test_requested_lists_and_probes_are_kept_within_bounds(vectors):
    index = IVFIndex(DIMENSION, VectorMetric.EUCLIDEAN, lists=5_000, probes=50)
    index.add(np.arange(100), vectors[:100])

    assert index.lists == 100
    assert index.probes == 50


def test_probing_every_list_is_the_exact_search(index, vectors, clustered_vectors):
    exact = FlatIndex(DIMENSION, VectorMetric.EUCLIDEAN)
    exact.add(np.arange(ROWS), vectors)

    for query in clustered_vectors(10, DIMENSION, 2):
        result = index.search(query, K, effort=index.lists)
        assert [hit.vector_id for hit in result.hits] == [
            hit.vector_id for hit in exact.search(query, K).hits
        ]
        assert result.stats.effort == index.lists


def test_removing_takes_the_vector_out_of_its_list(index):
    index.remove(range(500))

    assert sum(index.describe()["list_sizes"]) == ROWS - 500


def test_a_single_vector_needs_no_training(capfd, vectors):
    index = IVFIndex(DIMENSION, VectorMetric.COSINE)
    index.add([7], vectors[:1])

    assert index.lists == 1
    assert [hit.vector_id for hit in index.search(vectors[0], K).hits] == [7]
    assert "WARNING" not in capfd.readouterr().err


def test_lists_are_retrained_when_the_collection_changes(vectors):
    index = IVFIndex(DIMENSION, VectorMetric.EUCLIDEAN)
    index.add(np.arange(100), vectors[:100])
    index.add(np.arange(100, 129), vectors[100:129])
    assert not index.needs_maintenance()

    index.add(np.arange(129, ROWS), vectors[129:])
    assert index.needs_maintenance()
    index.maintain()

    assert not index.needs_maintenance()
    assert index.describe()["trained_on"] == ROWS
    assert index.lists == default_list_count(ROWS)
    assert index.count == ROWS


def test_shrinking_also_asks_for_retraining(index):
    index.remove(range(int(ROWS * 0.7), ROWS))

    assert index.needs_maintenance()


def test_lists_and_probes_must_be_positive():
    with pytest.raises(ValueError, match="listas"):
        IVFIndex(DIMENSION, VectorMetric.EUCLIDEAN, lists=0)
    with pytest.raises(ValueError, match="probes"):
        IVFIndex(DIMENSION, VectorMetric.EUCLIDEAN, probes=0)
