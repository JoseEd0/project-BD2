"""TF-IDF de Sivic y Zisserman sobre histogramas de palabras, calculado a mano."""

import math

import pytest

from multimedia import inverse_document_frequencies, tf_idf


def test_idf_is_the_log_of_objects_over_objects_with_the_word():
    idf = inverse_document_frequencies([4, 1, 0], documents=4)

    assert idf.tolist() == pytest.approx([0.0, math.log(4), 0.0])


def test_tf_is_relative_to_the_descriptors_of_the_object():
    idf = inverse_document_frequencies([1, 2, 2], documents=4)

    weights = tf_idf([3, 1, 0], idf)

    assert weights.tolist() == pytest.approx([0.75 * math.log(4), 0.25 * math.log(2), 0.0])


def test_an_object_without_descriptors_is_the_zero_vector():
    idf = inverse_document_frequencies([1, 1], documents=2)

    assert tf_idf([0, 0], idf).tolist() == [0.0, 0.0]


def test_several_objects_are_weighted_row_by_row():
    idf = inverse_document_frequencies([1, 1], documents=2)

    weights = tf_idf([[2, 0], [1, 1]], idf)

    assert weights.ravel().tolist() == pytest.approx(
        [math.log(2), 0.0, 0.5 * math.log(2), 0.5 * math.log(2)]
    )
