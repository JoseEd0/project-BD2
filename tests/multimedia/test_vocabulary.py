"""Vocabulario: k-means sobre los descriptores, la palabra de cada uno y la muestra."""

import numpy as np
import pytest

from multimedia import NotEnoughDescriptorsError, Vocabulary, training_positions, training_sample

CENTROIDS = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]], dtype=np.float32)
ITERATIONS = 10
SEED = 3


def test_each_descriptor_counts_for_its_nearest_word():
    vocabulary = Vocabulary(CENTROIDS)
    descriptors = [[0.1, 0.2], [-0.3, 0.1], [9.0, 1.0], [0.4, 0.0]]

    assert vocabulary.word_counts(descriptors).tolist() == [3.0, 1.0, 0.0]


def test_an_object_without_descriptors_has_no_words():
    vocabulary = Vocabulary(CENTROIDS)

    assert vocabulary.word_counts(np.empty((0, 2))).tolist() == [0.0, 0.0, 0.0]


def test_descriptors_of_another_dimension_are_rejected():
    with pytest.raises(ValueError, match="dimensión"):
        Vocabulary(CENTROIDS).word_counts([[1.0, 2.0, 3.0]])


def test_training_groups_the_descriptors():
    rng = np.random.default_rng(0)
    descriptors = np.concatenate([center + rng.normal(0, 0.1, (200, 2)) for center in CENTROIDS])

    vocabulary = Vocabulary.train(descriptors, size=6, iterations=ITERATIONS, seed=SEED)

    nearest = np.min(
        np.linalg.norm(descriptors[:, None, :] - vocabulary.centroids[None], axis=2), axis=1
    )
    spread = np.linalg.norm(descriptors - descriptors.mean(axis=0), axis=1)
    assert (vocabulary.size, vocabulary.descriptor_dimension) == (6, 2)
    assert nearest.mean() < spread.mean() / 10


def test_the_same_seed_gives_the_same_vocabulary():
    descriptors = np.random.default_rng(1).standard_normal((500, 4))

    first = Vocabulary.train(descriptors, size=8, iterations=ITERATIONS, seed=SEED)
    second = Vocabulary.train(descriptors, size=8, iterations=ITERATIONS, seed=SEED)

    assert np.array_equal(first.centroids, second.centroids)


def test_fewer_descriptors_than_words_is_an_error():
    with pytest.raises(NotEnoughDescriptorsError, match="4 palabras"):
        Vocabulary.train(np.ones((3, 2)), size=4, iterations=ITERATIONS, seed=SEED)


def test_the_sample_keeps_at_most_the_limit_and_comes_from_every_batch():
    batches = [np.full((100, 2), batch, dtype=np.float32) for batch in range(10)]

    sample = training_sample(batches, limit=200, seed=SEED)

    assert sample.shape == (200, 2)
    assert set(sample[:, 0].tolist()) == set(range(10))


def test_a_sample_smaller_than_the_limit_is_everything():
    batches = [np.ones((3, 2)), np.empty((0, 2)), np.zeros((2, 2))]

    assert training_sample(batches, limit=50, seed=SEED).shape == (5, 2)


def test_the_sample_is_deterministic():
    batches = [np.random.default_rng(seed).standard_normal((300, 3)) for seed in range(5)]

    first = training_sample(batches, limit=100, seed=SEED)
    second = training_sample(batches, limit=100, seed=SEED)

    assert np.array_equal(first, second)


def test_no_descriptors_give_an_empty_sample():
    assert training_sample([np.empty((0, 2))], limit=10, seed=SEED).size == 0


def test_batches_of_different_dimensions_are_rejected():
    with pytest.raises(ValueError, match="dimensión"):
        training_sample([np.ones((2, 2)), np.ones((2, 3))], limit=10, seed=SEED)


def test_the_training_objects_are_a_sorted_seeded_sample_without_repeats():
    positions = training_positions(1_000, 50, seed=SEED)

    assert len(set(positions)) == 50
    assert positions == sorted(positions)
    assert all(0 <= position < 1_000 for position in positions)
    assert training_positions(1_000, 50, seed=SEED) == positions


def test_with_fewer_objects_than_asked_every_object_trains():
    assert training_positions(7, 50, seed=SEED) == list(range(7))
