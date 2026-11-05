"""Lo que cumplen los tres índices de vectores —exacto, IVF y HNSW— con las tres métricas."""

from collections.abc import Callable

import numpy as np
import pytest

from index.hnsw import HNSWIndex
from index.ivf import IVFIndex
from index.vector import (
    DuplicateVectorIdError,
    FlatIndex,
    InvalidVectorError,
    VectorIndex,
    VectorIndexFileError,
    VectorMetric,
)

DIMENSION = 16
ROWS = 600
K = 10
QUERIES = 20
MIN_RECALL = 0.9
ID_STRIDE = 7

IndexFactory = Callable[..., VectorIndex]
FACTORIES: dict[str, IndexFactory] = {"flat": FlatIndex, "ivf": IVFIndex, "hnsw": HNSWIndex}


@pytest.fixture(params=sorted(FACTORIES))
def factory(request) -> IndexFactory:
    return FACTORIES[request.param]


@pytest.fixture(params=list(VectorMetric), ids=lambda metric: metric.value)
def metric(request) -> VectorMetric:
    return request.param


@pytest.fixture
def rows(clustered_vectors) -> tuple[np.ndarray, np.ndarray]:
    return np.arange(ROWS) * ID_STRIDE, clustered_vectors(ROWS, DIMENSION, 1)


@pytest.fixture
def filled(factory, metric, rows) -> VectorIndex:
    index = factory(DIMENSION, metric)
    index.add(*rows)
    return index


def exact_top(metric, rows, query, k=K, allowed=None):
    ids, vectors = rows
    if allowed is not None:
        keep = np.isin(ids, list(allowed))
        ids, vectors = ids[keep], vectors[keep]
    scores = metric.similarity(query, vectors)
    order = np.lexsort((ids, -scores))[:k]
    return ids[order].tolist()


def found(result):
    return [hit.vector_id for hit in result.hits]


def test_an_empty_index_finds_nothing(factory, metric):
    index = factory(DIMENSION, metric)

    assert index.count == 0
    assert index.search(np.ones(DIMENSION), K).hits == ()


def test_a_single_vector_is_its_own_nearest(factory, metric):
    index = factory(DIMENSION, metric)
    vector = np.arange(1, DIMENSION + 1, dtype=np.float32)
    index.add([42], [vector])

    hits = index.search(vector, K).hits

    assert [hit.vector_id for hit in hits] == [42]
    assert hits[0].score == pytest.approx(metric.similarity(vector, [vector])[0], rel=1e-5)


def test_asking_for_more_than_there_is_returns_everything(factory, metric, rows):
    index = factory(DIMENSION, metric)
    index.add(rows[0][:3], rows[1][:3])

    assert sorted(found(index.search(rows[1][0], K))) == sorted(rows[0][:3].tolist())


def test_results_agree_with_the_exact_search(filled, metric, rows, clustered_vectors):
    queries = clustered_vectors(QUERIES, DIMENSION, 2)

    recalls = [
        len(set(found(filled.search(query, K))) & set(exact_top(metric, rows, query))) / K
        for query in queries
    ]

    assert np.mean(recalls) >= MIN_RECALL


def test_scores_are_the_metric_and_come_sorted(filled, metric, rows, clustered_vectors):
    query = clustered_vectors(1, DIMENSION, 3)[0]
    vector_of = dict(zip(rows[0].tolist(), rows[1], strict=True))

    hits = filled.search(query, K).hits

    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    expected = metric.similarity(query, [vector_of[hit.vector_id] for hit in hits])
    assert scores == pytest.approx(expected.tolist(), rel=1e-4, abs=1e-5)


def test_the_flat_index_is_the_exact_search(metric, rows, clustered_vectors):
    index = FlatIndex(DIMENSION, metric)
    index.add(*rows)

    for query in clustered_vectors(QUERIES, DIMENSION, 4):
        assert found(index.search(query, K)) == exact_top(metric, rows, query)


def test_removed_vectors_never_come_back(filled, rows):
    removed = rows[0][:ROWS // 2].tolist()

    assert filled.remove(removed) == len(removed)
    assert filled.count == ROWS - len(removed)
    for vector in rows[1][: ROWS // 2 : 25]:
        assert not set(found(filled.search(vector, K))) & set(removed)


def test_removing_what_is_not_there_removes_nothing(filled):
    assert filled.remove([1, 2, 3]) == 0
    assert filled.count == ROWS


def test_a_removed_row_can_come_back(filled, rows):
    row, vector = int(rows[0][5]), rows[1][5]
    filled.remove([row])

    filled.add([row], [vector])

    assert filled.contains(row)
    assert found(filled.search(vector, 1, allowed=[row])) == [row]


def test_an_identifier_enters_once(filled, rows):
    with pytest.raises(DuplicateVectorIdError):
        filled.add(rows[0][:1], rows[1][:1])
    with pytest.raises(DuplicateVectorIdError):
        filled.add([1, 1], rows[1][:2])


def test_vectors_of_another_dimension_are_rejected(filled):
    with pytest.raises(InvalidVectorError):
        filled.add([1], [np.ones(DIMENSION + 1)])
    with pytest.raises(InvalidVectorError):
        filled.search(np.ones(DIMENSION - 1), K)


def test_a_small_filter_is_answered_exactly(filled, metric, rows, clustered_vectors):
    allowed = rows[0][::40].tolist()
    query = clustered_vectors(1, DIMENSION, 5)[0]

    result = filled.search(query, K, allowed=allowed)

    assert result.stats.exact
    assert found(result) == exact_top(metric, rows, query, allowed=allowed)


def test_a_filtered_search_through_the_index_still_returns_k(factory, metric, rows):
    index = factory(DIMENSION, metric, exact_filter_rows=0)
    index.add(*rows)
    allowed = set(rows[0][::50].tolist())

    hits = found(index.search(rows[1][3], K, allowed=allowed))

    assert len(hits) == K
    assert set(hits) <= allowed


def test_a_filter_ignores_rows_that_are_not_in_the_index(filled, rows):
    hits = found(filled.search(rows[1][0], K, allowed=[int(rows[0][0]), 1, 2]))

    assert hits == [int(rows[0][0])]


def test_a_saved_index_answers_the_same(filled, rows, tmp_path, clustered_vectors):
    filled.remove(rows[0][:50].tolist())
    path = tmp_path / "vectores.idx"
    queries = clustered_vectors(5, DIMENSION, 6)
    before = [found(filled.search(query, K)) for query in queries]

    filled.save(path)
    reopened = type(filled).load(path)

    assert reopened.count == filled.count
    assert reopened.describe() == filled.describe()
    assert [found(reopened.search(query, K)) for query in queries] == before
    assert list(tmp_path.iterdir()) == [path]


def test_an_empty_index_can_be_saved(factory, metric, tmp_path):
    path = tmp_path / "vacio.idx"
    factory(DIMENSION, metric).save(path)

    assert factory(DIMENSION, metric).load(path).count == 0


def test_a_missing_or_damaged_file_is_reported(factory, tmp_path):
    damaged = tmp_path / "danado.idx"
    damaged.write_bytes(b"no es un indice")

    for path in (tmp_path / "no-existe.idx", damaged):
        with pytest.raises(VectorIndexFileError):
            factory.load(path)


def test_a_file_of_another_index_is_rejected(metric, rows, tmp_path):
    path = tmp_path / "hnsw.idx"
    index = HNSWIndex(DIMENSION, metric)
    index.add(*rows)
    index.save(path)

    with pytest.raises(VectorIndexFileError, match="ivf"):
        IVFIndex.load(path)


def test_describe_reports_the_common_shape(filled, metric):
    shape = filled.describe()

    assert shape["kind"] == filled.kind
    assert shape["metric"] == metric.value
    assert shape["dimension"] == DIMENSION
    assert shape["vectors"] == ROWS
