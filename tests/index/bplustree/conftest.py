"""Validador estructural del árbol B+, compartido por sus tests.

Comprobar la respuesta de `search` no basta: un árbol puede devolver bien y estar roto por
dentro. Estas comprobaciones son las que garantizan que las divisiones y fusiones dejan el
árbol en un estado legal.
"""

from collections.abc import Callable

import pytest

from index.bplustree.tree import BPlusTree
from index.keys import Key

TreeValidator = Callable[[BPlusTree, set[Key]], None]


@pytest.fixture
def assert_tree_is_valid() -> TreeValidator:
    return _assert_tree_is_valid


def _assert_tree_is_valid(tree: BPlusTree, expected_keys: set[Key]) -> None:
    leaf_minimum = (tree.leaf_capacity + 1) // 2
    internal_minimum = tree.internal_capacity // 2
    root_id = tree._root

    def walk(page_id: int, depth: int, low: Key | None, high: Key | None) -> int:
        node = tree._load(page_id)
        assert node.keys == sorted(node.keys), f"el nodo {page_id} no está ordenado"
        for key in node.keys:
            assert low is None or key >= low, f"clave {key} menor que el separador {low}"
            assert high is None or key < high, f"clave {key} no menor que el separador {high}"
        if node.is_leaf:
            assert len(node.keys) <= tree.leaf_capacity
            if page_id != root_id:
                assert len(node.keys) >= leaf_minimum, f"hoja {page_id} por debajo del mínimo"
            return depth
        assert len(node.children) == len(node.keys) + 1
        assert len(node.keys) <= tree.internal_capacity
        if page_id != root_id:
            assert len(node.keys) >= internal_minimum, f"nodo {page_id} por debajo del mínimo"
        depths = {
            walk(
                child,
                depth + 1,
                node.keys[index - 1] if index > 0 else low,
                node.keys[index] if index < len(node.keys) else high,
            )
            for index, child in enumerate(node.children)
        }
        assert len(depths) == 1, f"las hojas quedaron a distinta profundidad: {depths}"
        return depths.pop()

    assert walk(root_id, 1, None, None) == tree.height
    assert [key for key, _ in tree.scan()] == sorted(expected_keys)
    assert tree.entry_count == len(expected_keys)
