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
