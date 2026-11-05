"""HNSW: lápidas, reconstrucción al 30 %, niveles del grafo y parámetros."""

import numpy as np
import pytest

from config import DEFAULT_HNSW_M, DEFAULT_VECTOR_REBUILD_FRACTION
from index.hnsw import HNSWIndex
from index.vector import VectorMetric

DIMENSION = 8
ROWS = 1_000
K = 10


@pytest.fixture
def vectors(clustered_vectors) -> np.ndarray:
    return clustered_vectors(ROWS, DIMENSION, 1)


@pytest.fixture
def index(vectors) -> HNSWIndex:
    built = HNSWIndex(DIMENSION, VectorMetric.EUCLIDEAN, exact_filter_rows=0)
    built.add(np.arange(ROWS), vectors)
    return built


def test_a_tombstone_is_never_a_result_even_when_it_is_the_nearest(index, vectors):
    index.remove([3])

    hits = [hit.vector_id for hit in index.search(vectors[3], K).hits]

    assert 3 not in hits
    assert len(hits) == K


def test_removing_leaves_a_tombstone_in_the_graph(index):
    index.remove(range(100))

    assert index.count == ROWS - 100
    assert index.nodes == ROWS
    assert index.tombstones == 100
    assert index.describe()["tombstones"] == 100


def test_the_graph_is_rebuilt_once_tombstones_reach_the_threshold(index, vectors):
    below = int(ROWS * DEFAULT_VECTOR_REBUILD_FRACTION) - 1
    index.remove(range(below))
    assert not index.needs_maintenance()

    index.remove([below])
    assert index.needs_maintenance()
    before = [hit.vector_id for hit in index.search(vectors[-1], K).hits]
    index.maintain()

    assert index.tombstones == 0
    assert index.nodes == index.count == ROWS - below - 1
    assert [hit.vector_id for hit in index.search(vectors[-1], K).hits] == before


def test_a_row_that_comes_back_is_a_new_node(index, vectors):
    index.remove([5])
    index.add([5], [vectors[5] + 1])

    assert index.nodes == ROWS + 1
    assert index.tombstones == 1
    assert [hit.vector_id for hit in index.search(vectors[5] + 1, 1).hits] == [5]


def test_tombstones_survive_saving(index, vectors, tmp_path):
    index.remove(range(50))
    path = tmp_path / "grafo.idx"
    index.save(path)

    reopened = HNSWIndex.load(path, exact_filter_rows=0)

    assert reopened.tombstones == 50
    assert {hit.vector_id for hit in reopened.search(vectors[10], K).hits}.isdisjoint(range(50))


def test_every_node_is_on_the_base_level_and_fewer_go_up(index):
    levels = index.describe()["nodes_per_level"]

    assert levels[0] == ROWS
    assert levels == sorted(levels, reverse=True)
    assert len(levels) > 1


def test_base_level_nodes_have_up_to_twice_m_neighbors(index):
    degree = index.describe()["mean_base_degree"]

    assert 0 < degree <= 2 * DEFAULT_HNSW_M


def test_a_larger_ef_is_reported_in_the_stats(index, vectors):
    result = index.search(vectors[0], K, effort=200)

    assert result.stats.effort == 200
    assert result.stats.distance_computations > 0
    assert not result.stats.exact


def test_parameters_are_kept(vectors):
    index = HNSWIndex(DIMENSION, VectorMetric.COSINE, m=8, ef_construction=32, ef_search=16)

    shape = index.describe()

    assert (shape["m"], shape["ef_construction"], shape["ef_search"]) == (8, 32, 16)


@pytest.mark.parametrize(
    ("parameter", "name"), [("m", "M"), ("ef_construction", "ef_construction"), ("ef_search", "ef_search")]
)
def test_parameters_must_be_positive(parameter, name):
    with pytest.raises(ValueError, match=name):
        HNSWIndex(DIMENSION, VectorMetric.COSINE, **{parameter: 0})
