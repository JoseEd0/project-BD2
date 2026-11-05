"""El almacén de características: construir, añadir, quitar, leer y reabrir."""

import numpy as np
import pytest

from config import EngineConfig
from index.vector import vector_id_of
from multimedia import FeatureStore, FeatureStoreError, NotEnoughDescriptorsError, tf_idf
from storage.record_id import RecordId

WORDS = 4
DIMENSION = 3
DESCRIPTORS_PER_OBJECT = 20
OBJECTS = 30
# Cada objeto usa solo algunas palabras: si todos las tuvieran todas, el idf de cada una
# sería log(N / N) = 0 y todos los vectores, cero.
WORDS_PER_OBJECT = 2


@pytest.fixture
def config(tmp_path) -> EngineConfig:
    return EngineConfig(data_directory=tmp_path, vocabulary_size=WORDS, kmeans_iterations=5)


@pytest.fixture
def objects() -> list[tuple[RecordId, np.ndarray]]:
    rng = np.random.default_rng(0)
    centers = rng.standard_normal((WORDS, DIMENSION)) * 10
    return [
        (
            RecordId(page_id=1 + number // 3, slot=number % 3),
            (
                centers[
                    rng.choice(rng.permutation(WORDS)[:WORDS_PER_OBJECT], DESCRIPTORS_PER_OBJECT)
                ]
                + rng.normal(0, 0.1, (DESCRIPTORS_PER_OBJECT, DIMENSION))
            ).astype(np.float32),
        )
        for number in range(OBJECTS)
    ]


@pytest.fixture
def store(tmp_path, config, objects) -> FeatureStore:
    built = FeatureStore(tmp_path / "fotos.ruta", config)
    built.build(objects, np.concatenate([descriptors for _, descriptors in objects]))
    yield built
    built.close()


def test_a_new_store_is_not_built(tmp_path, config):
    store = FeatureStore(tmp_path / "vacio", config)

    assert not store.built
    assert store.count == 0
    assert store.describe() == {"built": False}
    with pytest.raises(FeatureStoreError, match="no está construido"):
        store.add(RecordId(1, 0), np.ones((1, DIMENSION)))


def test_building_reports_what_it_did(tmp_path, config, objects):
    store = FeatureStore(tmp_path / "fotos.ruta", config)
    training = np.concatenate([descriptors for _, descriptors in objects])

    build = store.build(objects, training)

    assert build.objects == OBJECTS
    assert build.descriptors == OBJECTS * DESCRIPTORS_PER_OBJECT
    assert build.empty_objects == 0
    assert build.training_descriptors == len(training)
    assert build.vocabulary_size == WORDS
    assert store.count == OBJECTS
    store.close()


def test_each_row_gets_the_tf_idf_of_its_word_counts(store, objects):
    record_id, descriptors = objects[0]
    vocabulary_counts = store._require_vocabulary().word_counts(descriptors)

    assert store.vector_of(record_id) == pytest.approx(tf_idf(vocabulary_counts, store._idf))
    assert store.vectorize(descriptors) == pytest.approx(store.vector_of(record_id))


def test_an_object_without_descriptors_is_the_zero_vector(store):
    store.add(RecordId(99, 0), np.empty((0, DIMENSION), dtype=np.float32))

    assert store.vector_of(RecordId(99, 0)).tolist() == [0.0] * WORDS


def test_vectors_come_in_batches_with_the_row_identifiers(store, objects):
    batches = list(store.vectors())

    ids = np.concatenate([batch_ids for batch_ids, _ in batches])
    matrix = np.concatenate([vectors for _, vectors in batches])
    assert sorted(ids.tolist()) == sorted(vector_id_of(record_id) for record_id, _ in objects)
    assert matrix.shape == (OBJECTS, WORDS)


def test_adding_and_removing_rows(store, objects):
    new_row = RecordId(50, 1)
    store.add(new_row, objects[0][1])
    assert store.contains(new_row)
    assert store.count == OBJECTS + 1

    assert store.remove(new_row)
    assert not store.remove(new_row)
    assert store.vector_of(new_row) is None
    assert store.count == OBJECTS


def test_a_row_enters_once(store, objects):
    with pytest.raises(FeatureStoreError, match="ya tiene vector"):
        store.add(*objects[0])


def test_the_store_is_built_once(store, objects):
    with pytest.raises(FeatureStoreError, match="ya está construido"):
        store.build(objects, objects[0][1])


def test_too_few_descriptors_for_the_vocabulary(tmp_path, config, objects):
    store = FeatureStore(tmp_path / "pocos", config)

    with pytest.raises(NotEnoughDescriptorsError):
        store.build(objects, objects[0][1][: WORDS - 1])


def test_vectors_survive_reopening(tmp_path, config, store, objects):
    before = {record_id: store.vector_of(record_id) for record_id, _ in objects[:5]}
    store.remove(objects[5][0])
    store.close()

    with FeatureStore(tmp_path / "fotos.ruta", config) as reopened:
        assert reopened.built
        assert reopened.count == OBJECTS - 1
        assert reopened.vector_of(objects[5][0]) is None
        for record_id, vector in before.items():
            assert reopened.vector_of(record_id) == pytest.approx(vector)
        assert reopened.describe()["objects_at_build"] == OBJECTS


def test_files_of_lists_every_file(tmp_path, store):
    store.flush()

    files = FeatureStore.files_of(tmp_path / "fotos.ruta")

    assert all(path.exists() for path in files)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(path.name for path in files)


def test_a_damaged_model_is_reported(tmp_path, config, store):
    store.close()
    FeatureStore.files_of(tmp_path / "fotos.ruta")[0].write_bytes(b"no es un modelo")

    with pytest.raises(FeatureStoreError, match="no se puede leer"):
        FeatureStore(tmp_path / "fotos.ruta", config)


def test_describe_reports_the_vocabulary_and_the_rows(store):
    shape = store.describe()

    assert shape["vocabulary_size"] == WORDS
    assert shape["descriptor_dimension"] == DIMENSION
    assert shape["objects"] == OBJECTS
    assert 0 < shape["words_in_use"] <= WORDS
