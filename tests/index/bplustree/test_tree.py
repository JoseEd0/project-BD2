import random
from collections.abc import Callable

import pytest

from config import EngineConfig
from index.bplustree.node import NodeCapacityError
from index.bplustree.tree import BPlusTree, DuplicateKeyError, TreeFormatError
from index.keys import ScalarKeyCodec
from storage.types import StorageError

TreeValidator = Callable[[BPlusTree, set[int]], None]

VALUE_SIZE = 4
SHUFFLE_SEED = 11


def payload(key: int) -> bytes:
    return key.to_bytes(VALUE_SIZE, "little")


@pytest.fixture
def tree(config: EngineConfig, key_codec: ScalarKeyCodec):
    with BPlusTree(config.data_directory / "i.bpt", key_codec, VALUE_SIZE, config) as index:
        yield index


@pytest.fixture
def tiny_tree(config: EngineConfig, key_codec: ScalarKeyCodec):
    """Páginas de 64 bytes: el árbol se divide y fusiona con muy pocas claves."""
    tiny = EngineConfig(page_size=64, data_directory=config.data_directory)
    with BPlusTree(tiny.data_directory / "tiny.bpt", key_codec, VALUE_SIZE, tiny) as index:
        yield index


def test_empty_tree(tree: BPlusTree):
    assert tree.entry_count == 0
    assert tree.height == 1
    assert tree.search(1) is None
    assert list(tree.scan()) == []


def test_single_entry(tree: BPlusTree):
    tree.insert(1, payload(1))
    assert tree.search(1) == payload(1)
    assert tree.entry_count == 1
    assert tree.height == 1


def test_duplicate_key_is_rejected(tree: BPlusTree):
    tree.insert(1, payload(1))
    with pytest.raises(DuplicateKeyError):
        tree.insert(1, payload(2))
    assert tree.entry_count == 1


def test_value_of_the_wrong_size_is_rejected(tree: BPlusTree):
    with pytest.raises(StorageError):
        tree.insert(1, b"x")


def test_leaf_split_grows_the_tree(tree: BPlusTree, assert_tree_is_valid: TreeValidator):
    for key in range(tree.leaf_capacity + 1):
        tree.insert(key, payload(key))
    assert tree.height == 2
    assert_tree_is_valid(tree, set(range(tree.leaf_capacity + 1)))


def test_internal_split_grows_the_tree(tiny_tree: BPlusTree, assert_tree_is_valid: TreeValidator):
    keys = set(range(200))
    for key in keys:
        tiny_tree.insert(key, payload(key))
    assert tiny_tree.height >= 3
    assert_tree_is_valid(tiny_tree, keys)


def test_ascending_insertion(tiny_tree: BPlusTree, assert_tree_is_valid: TreeValidator):
    keys = set(range(150))
    for key in sorted(keys):
        tiny_tree.insert(key, payload(key))
    assert_tree_is_valid(tiny_tree, keys)


def test_descending_insertion(tiny_tree: BPlusTree, assert_tree_is_valid: TreeValidator):
    keys = set(range(150))
    for key in sorted(keys, reverse=True):
        tiny_tree.insert(key, payload(key))
    assert_tree_is_valid(tiny_tree, keys)


def test_random_insertion(tiny_tree: BPlusTree, assert_tree_is_valid: TreeValidator):
    keys = list(range(300))
    random.Random(SHUFFLE_SEED).shuffle(keys)
    for key in keys:
        tiny_tree.insert(key, payload(key))
    assert_tree_is_valid(tiny_tree, set(keys))
    assert all(tiny_tree.search(key) == payload(key) for key in keys)


def test_range_scan_is_inclusive(tree: BPlusTree):
    for key in range(50):
        tree.insert(key, payload(key))
    assert [key for key, _ in tree.range_scan(10, 14)] == [10, 11, 12, 13, 14]


def test_range_scan_without_bounds(tree: BPlusTree):
    for key in range(20):
        tree.insert(key, payload(key))
    assert [key for key, _ in tree.range_scan(None, 3)] == [0, 1, 2, 3]
    assert [key for key, _ in tree.range_scan(17, None)] == [17, 18, 19]


def test_range_scan_outside_the_data(tree: BPlusTree):
    tree.insert(5, payload(5))
    assert list(tree.range_scan(10, 20)) == []


def test_leaf_chain_survives_splits(tiny_tree: BPlusTree):
    keys = list(range(120))
    random.Random(SHUFFLE_SEED).shuffle(keys)
    for key in keys:
        tiny_tree.insert(key, payload(key))
    assert [key for key, _ in tiny_tree.scan()] == sorted(keys)


def test_delete_missing_key(tree: BPlusTree):
    tree.insert(1, payload(1))
    assert not tree.delete(2)
    assert tree.entry_count == 1


def test_delete_the_only_entry(tree: BPlusTree):
    tree.insert(1, payload(1))
    assert tree.delete(1)
    assert tree.entry_count == 0
    assert tree.search(1) is None
    assert tree.height == 1


def test_delete_redistributes_and_merges(tiny_tree: BPlusTree, assert_tree_is_valid: TreeValidator):
    keys = list(range(200))
    random.Random(SHUFFLE_SEED).shuffle(keys)
    for key in keys:
        tiny_tree.insert(key, payload(key))
    live = set(keys)
    order = list(keys)
    random.Random(SHUFFLE_SEED + 1).shuffle(order)
    for position, key in enumerate(order):
        assert tiny_tree.delete(key)
        live.discard(key)
        if position % 23 == 0:
            assert_tree_is_valid(tiny_tree, live)
    assert_tree_is_valid(tiny_tree, live)
    assert tiny_tree.height == 1


def test_pages_are_reused_after_deletions(tiny_tree: BPlusTree):
    for key in range(120):
        tiny_tree.insert(key, payload(key))
    pages_after_growth = tiny_tree.page_count
    for key in range(120):
        tiny_tree.delete(key)
    for key in range(120):
        tiny_tree.insert(key, payload(key))
    assert tiny_tree.page_count == pages_after_growth


def test_interleaved_insert_and_delete(tiny_tree: BPlusTree, assert_tree_is_valid: TreeValidator):
    generator = random.Random(SHUFFLE_SEED)
    live: set[int] = set()
    for _ in range(1500):
        key = generator.randrange(300)
        if key in live:
            assert tiny_tree.delete(key)
            live.discard(key)
        else:
            tiny_tree.insert(key, payload(key))
            live.add(key)
    assert_tree_is_valid(tiny_tree, live)


def test_state_survives_reopening(config: EngineConfig, key_codec: ScalarKeyCodec, assert_tree_is_valid: TreeValidator):
    path = config.data_directory / "p.bpt"
    with BPlusTree(path, key_codec, VALUE_SIZE, config) as tree:
        for key in range(80):
            tree.insert(key, payload(key))
        height = tree.height
    with BPlusTree(path, key_codec, VALUE_SIZE, config) as tree:
        assert tree.entry_count == 80
        assert tree.height == height
        assert tree.search(40) == payload(40)
        assert_tree_is_valid(tree, set(range(80)))


def test_opening_with_another_value_size_is_rejected(
    config: EngineConfig, key_codec: ScalarKeyCodec
):
    path = config.data_directory / "p.bpt"
    with BPlusTree(path, key_codec, VALUE_SIZE, config):
        pass
    with pytest.raises(TreeFormatError):
        BPlusTree(path, key_codec, VALUE_SIZE + 1, config)


def test_page_too_small_is_rejected(config: EngineConfig, key_codec: ScalarKeyCodec):
    cramped = EngineConfig(page_size=32, data_directory=config.data_directory)
    with pytest.raises(NodeCapacityError):
        BPlusTree(cramped.data_directory / "c.bpt", key_codec, 64, cramped)
