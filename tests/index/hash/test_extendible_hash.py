import random
from collections.abc import Callable

import pytest

from config import EngineConfig
from index.hash import ExtendibleHashIndex
from index.hash.extendible_hash import HashFormatError, stable_hash
from index.keys import ScalarKeyCodec
from storage.types import StorageError

HashValidator = Callable[[ExtendibleHashIndex, list[int]], None]

VALUE_SIZE = 4
SHUFFLE_SEED = 5


def payload(key: int) -> bytes:
    return key.to_bytes(VALUE_SIZE, "little")


@pytest.fixture
def index(config: EngineConfig, key_codec: ScalarKeyCodec):
    with ExtendibleHashIndex(
        config.data_directory / "h.hash", key_codec, VALUE_SIZE, config
    ) as hash_index:
        yield hash_index


def test_hash_is_stable_across_runs():
    assert stable_hash(b"lima") == stable_hash(b"lima")
    assert stable_hash(b"lima") != stable_hash(b"cusco")


def test_empty_index(index: ExtendibleHashIndex):
    assert index.entry_count == 0
    assert index.search(1) == []
    assert list(index.scan()) == []


def test_single_entry(index: ExtendibleHashIndex):
    index.insert(1, payload(1))
    assert index.search(1) == [payload(1)]
    assert index.entry_count == 1


def test_value_of_the_wrong_size_is_rejected(index: ExtendibleHashIndex):
    with pytest.raises(StorageError):
        index.insert(1, b"x")


def test_missing_key_returns_nothing(index: ExtendibleHashIndex):
    index.insert(1, payload(1))
    assert index.search(2) == []


def test_bucket_overflow_splits_the_bucket(
    index: ExtendibleHashIndex, assert_hash_is_valid: HashValidator
):
    keys = list(range(index.bucket_capacity * 4))
    for key in keys:
        index.insert(key, payload(key))
    assert index.bucket_count > 1
    assert_hash_is_valid(index, keys)


def test_directory_doubles_when_needed(
    index: ExtendibleHashIndex, assert_hash_is_valid: HashValidator
):
    depth_before = index.global_depth
    keys = list(range(400))
    random.Random(SHUFFLE_SEED).shuffle(keys)
    for key in keys:
        index.insert(key, payload(key))
    assert index.global_depth > depth_before
    assert_hash_is_valid(index, keys)


def test_every_key_is_found_after_many_splits(index: ExtendibleHashIndex):
    keys = list(range(400))
    random.Random(SHUFFLE_SEED).shuffle(keys)
    for key in keys:
        index.insert(key, payload(key))
    assert all(index.search(key) == [payload(key)] for key in keys)


def test_repeated_keys_are_all_kept(index: ExtendibleHashIndex):
    for value in range(200):
        index.insert(7, payload(value))
    assert len(index.search(7)) == 200
    assert index.entry_count == 200


def test_repeated_keys_do_not_blow_up_the_directory(index: ExtendibleHashIndex):
    """Todas caen del mismo lado: la cubeta se encadena en vez de duplicar el directorio."""
    for value in range(300):
        index.insert(7, payload(value))
    assert index.global_depth <= index.bucket_capacity


def test_delete_removes_every_entry_with_that_key(index: ExtendibleHashIndex):
    for value in range(30):
        index.insert(7, payload(value))
    index.insert(8, payload(99))
    assert index.delete(7) == 30
    assert index.search(7) == []
    assert index.search(8) == [payload(99)]


def test_deleting_a_missing_key_changes_nothing(index: ExtendibleHashIndex):
    index.insert(1, payload(1))
    assert index.delete(2) == 0
    assert index.entry_count == 1


def test_insert_after_delete_reuses_the_slot(
    index: ExtendibleHashIndex, assert_hash_is_valid: HashValidator
):
    keys = list(range(200))
    for key in keys:
        index.insert(key, payload(key))
    pages_before = index.page_count
    for key in keys[:100]:
        index.delete(key)
    for key in keys[:100]:
        index.insert(key, payload(key))
    assert_hash_is_valid(index, keys)
    assert index.page_count <= pages_before + 1


def test_state_survives_reopening(config: EngineConfig, key_codec: ScalarKeyCodec):
    path = config.data_directory / "p.hash"
    with ExtendibleHashIndex(path, key_codec, VALUE_SIZE, config) as index:
        for key in range(300):
            index.insert(key, payload(key))
        depth = index.global_depth
    with ExtendibleHashIndex(path, key_codec, VALUE_SIZE, config) as index:
        assert index.entry_count == 300
        assert index.global_depth == depth
        assert index.search(150) == [payload(150)]


def test_opening_with_another_value_size_is_rejected(
    config: EngineConfig, key_codec: ScalarKeyCodec
):
    path = config.data_directory / "p.hash"
    with ExtendibleHashIndex(path, key_codec, VALUE_SIZE, config):
        pass
    with pytest.raises(HashFormatError):
        ExtendibleHashIndex(path, key_codec, VALUE_SIZE + 1, config)


def test_delete_entry_removes_only_that_value(index: ExtendibleHashIndex):
    for value in range(5):
        index.insert(7, payload(value))
    assert index.delete_entry(7, payload(3))
    assert sorted(index.search(7)) == [payload(value) for value in (0, 1, 2, 4)]
    assert not index.delete_entry(7, payload(3))
    assert not index.delete_entry(8, payload(0))
    assert index.entry_count == 4


def test_deleting_everything_merges_the_buckets_and_shrinks_the_directory(
    index: ExtendibleHashIndex, assert_hash_is_valid: HashValidator
):
    """El camino de vuelta de las divisiones: al vaciarse, el índice recupera su forma
    inicial de dos cubetas y un directorio de dos punteros."""
    keys = list(range(index.bucket_capacity * 40))
    for key in keys:
        index.insert(key, payload(key))
    grown_depth, grown_buckets = index.global_depth, index.bucket_count
    assert grown_depth > 1 and grown_buckets > 2
    random.Random(SHUFFLE_SEED).shuffle(keys)
    remaining = list(keys)
    for position, key in enumerate(keys):
        assert index.delete(key) == 1
        remaining.remove(key)
        if position % 25 == 0:
            assert_hash_is_valid(index, remaining)
    assert_hash_is_valid(index, [])
    assert index.global_depth == 1
    assert index.bucket_count == 2


def test_the_index_shrinks_step_by_step_as_it_empties(
    index: ExtendibleHashIndex, assert_hash_is_valid: HashValidator
):
    keys = list(range(index.bucket_capacity * 40))
    for key in keys:
        index.insert(key, payload(key))
    full_buckets = index.bucket_count
    for key in keys[: len(keys) * 3 // 4]:
        index.delete(key)
    kept = keys[len(keys) * 3 // 4 :]
    assert_hash_is_valid(index, kept)
    assert index.bucket_count < full_buckets
    for key in kept:
        assert index.search(key) == [payload(key)]


def test_pages_freed_by_merging_are_reused(
    index: ExtendibleHashIndex, assert_hash_is_valid: HashValidator
):
    keys = list(range(index.bucket_capacity * 30))
    for key in keys:
        index.insert(key, payload(key))
    pages = index.page_count
    for _ in range(3):
        for key in keys:
            index.delete(key)
        for key in keys:
            index.insert(key, payload(key))
    assert_hash_is_valid(index, keys)
    assert index.page_count == pages


def test_twin_buckets_merge_only_when_they_fit_with_room_to_spare(
    config: EngineConfig, key_codec: ScalarKeyCodec, assert_hash_is_valid: HashValidator
):
    """Fundir dos cubetas que juntas quedan llenas haría que la siguiente inserción las
    partiera otra vez: solo se funden por debajo de `hash_merge_fill`."""
    path = config.data_directory / "m.hash"
    with ExtendibleHashIndex(path, key_codec, VALUE_SIZE, config) as index:
        keys = list(range(index.bucket_capacity * 20))
        for key in keys:
            index.insert(key, payload(key))
        for key in keys[::2]:
            index.delete(key)
        kept = keys[1::2]
        assert_hash_is_valid(index, kept)
        buckets = index.bucket_count
        for key in keys[::2][:3]:
            index.insert(key, payload(key))
        assert index.bucket_count == buckets
        assert_hash_is_valid(index, sorted([*kept, *keys[::2][:3]]))


def test_overflow_chains_are_released_when_their_keys_go(
    index: ExtendibleHashIndex, assert_hash_is_valid: HashValidator
):
    repeated = index.bucket_capacity * 5
    for value in range(repeated):
        index.insert(7, payload(value))
    for key in range(100, 140):
        index.insert(key, payload(key))
    assert index.describe()["buckets"] and any(
        bucket["overflow_pages"] for bucket in index.describe()["buckets"]
    )
    assert index.delete(7) == repeated
    assert_hash_is_valid(index, list(range(100, 140)))
    assert not any(bucket["overflow_pages"] for bucket in index.describe()["buckets"])
    for value in range(repeated):
        index.insert(7, payload(value))
    assert len(index.search(7)) == repeated
    assert_hash_is_valid(index, [*range(100, 140), *([7] * repeated)])


def test_a_shrunk_index_survives_reopening(
    config: EngineConfig, key_codec: ScalarKeyCodec, assert_hash_is_valid: HashValidator
):
    path = config.data_directory / "s.hash"
    with ExtendibleHashIndex(path, key_codec, VALUE_SIZE, config) as index:
        capacity = index.bucket_capacity
        for key in range(capacity * 40):
            index.insert(key, payload(key))
        for key in range(10, capacity * 40):
            index.delete(key)
        depth = index.global_depth
    with ExtendibleHashIndex(path, key_codec, VALUE_SIZE, config) as index:
        assert index.global_depth == depth
        assert_hash_is_valid(index, list(range(10)))
        for key in range(10, capacity * 10):
            index.insert(key, payload(key))
        assert_hash_is_valid(index, list(range(capacity * 10)))


def test_a_file_that_is_not_a_hash_index_is_rejected(
    config: EngineConfig, key_codec: ScalarKeyCodec
):
    path = config.data_directory / "ajeno.hash"
    path.write_bytes(b"x" * config.page_size)
    with pytest.raises(HashFormatError, match="no es un índice hash"):
        ExtendibleHashIndex(path, key_codec, VALUE_SIZE, config)
