import pytest
from rtree_support import TreeValidator, assert_tree_is_valid

from config import EngineConfig
from index.rtree import RTree


@pytest.fixture
def tree(config: EngineConfig):
    with RTree(config.data_directory / "puntos.rtree", config) as instance:
        yield instance


@pytest.fixture(name="assert_tree_is_valid")
def tree_validator() -> TreeValidator:
    return assert_tree_is_valid
