"""Las tres métricas de vectores y su puntaje, contra la definición escrita a mano."""

import math

import numpy as np
import pytest

from index.vector import (
    InvalidVectorError,
    UnknownVectorMetricError,
    VectorMetric,
    vector_metric_named,
)

QUERY = [1.0, 2.0, 2.0]
OTHERS = [[1.0, 2.0, 2.0], [0.0, 0.0, 3.0], [-2.0, 0.0, 0.0]]


def test_euclidean_score_is_one_over_one_plus_distance():
    scores = VectorMetric.EUCLIDEAN.similarity(QUERY, OTHERS)

    expected = [1 / (1 + math.dist(QUERY, other)) for other in OTHERS]
    assert scores == pytest.approx(expected)
    assert scores[0] == 1.0


def test_dot_score_is_the_inner_product():
    scores = VectorMetric.DOT.similarity(QUERY, OTHERS)

    assert scores == pytest.approx([9.0, 6.0, -2.0])


def test_cosine_score_ignores_the_length():
    scores = VectorMetric.COSINE.similarity(QUERY, [[2.0, 4.0, 4.0], *OTHERS[1:]])

    assert scores == pytest.approx([1.0, 6 / 9, -2 / 6])


def test_cosine_of_a_zero_vector_is_zero():
    scores = VectorMetric.COSINE.similarity(QUERY, [[0.0, 0.0, 0.0]])

    assert scores == pytest.approx([0.0])


def test_faiss_squared_distance_becomes_the_euclidean_score():
    raw = np.array([0.0, 4.0], dtype=np.float32)

    assert VectorMetric.EUCLIDEAN.scores_from_faiss(raw) == pytest.approx([1.0, 1 / 3])
    assert VectorMetric.DOT.scores_from_faiss(raw) == pytest.approx([0.0, 4.0])


def test_prepare_does_not_touch_the_input():
    vectors = np.array([[3.0, 4.0]], dtype=np.float32)

    prepared = VectorMetric.COSINE.prepare(vectors, 2)

    assert prepared.ravel() == pytest.approx([0.6, 0.8])
    assert vectors.tolist() == [[3.0, 4.0]]


@pytest.mark.parametrize("vectors", [[[1.0, 2.0]], [[1.0, 2.0, 3.0, 4.0]], [[1.0, math.nan, 0.0]]])
def test_prepare_rejects_a_wrong_dimension_or_a_non_finite_value(vectors):
    with pytest.raises(InvalidVectorError):
        VectorMetric.EUCLIDEAN.prepare(vectors, 3)


@pytest.mark.parametrize(
    ("name", "metric"),
    [
        ("cosine", VectorMetric.COSINE),
        ("COSENO", VectorMetric.COSINE),
        (" euclidean ", VectorMetric.EUCLIDEAN),
        ("euclidiana", VectorMetric.EUCLIDEAN),
        ("dot", VectorMetric.DOT),
        ("producto_punto", VectorMetric.DOT),
    ],
)
def test_metrics_are_found_by_name(name, metric):
    assert vector_metric_named(name) is metric


def test_an_unknown_metric_names_the_accepted_ones():
    with pytest.raises(UnknownVectorMetricError, match="cosine"):
        vector_metric_named("manhattan")
