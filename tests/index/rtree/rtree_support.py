"""Validador estructural del R-Tree y generadores de puntos, compartidos por sus tests.

Que una búsqueda devuelva lo correcto no prueba que el árbol esté sano: con MBR demasiado
grandes todo sigue saliendo bien, solo que sin podar nada. Estas comprobaciones son las que
garantizan que dividir, condensar y reinsertar dejan el árbol en un estado legal.
"""

import random
from collections.abc import Callable

from index.rtree import RTree
from index.rtree.node import MINIMUM_ENTRIES_PER_NODE, NO_NODE, BranchNode, LeafNode
from spatial.geometry import Point, Rectangle
from storage.record_id import RecordId

Indexed = tuple[Point, RecordId]
TreeValidator = Callable[[RTree, set[Indexed]], None]

LIMA = Point(-12.0464, -77.0428)
CITY_SPREAD_DEGREES = 0.25
SLOTS_PER_FAKE_PAGE = 50


def record_id_of(number: int) -> RecordId:
    return RecordId(page_id=1 + number // SLOTS_PER_FAKE_PAGE, slot=number % SLOTS_PER_FAKE_PAGE)


def random_points(count: int, seed: int) -> list[Indexed]:
    """Puntos repartidos alrededor de Lima, cada uno con una dirección de fila distinta."""
    generator = random.Random(seed)
    return [
        (
            Point(
                LIMA.lat + generator.uniform(-CITY_SPREAD_DEGREES, CITY_SPREAD_DEGREES),
                LIMA.lon + generator.uniform(-CITY_SPREAD_DEGREES, CITY_SPREAD_DEGREES),
            ),
            record_id_of(number),
        )
        for number in range(count)
    ]


def fill(tree: RTree, entries: list[Indexed]) -> None:
    for point, record_id in entries:
        tree.insert(point, record_id)


def assert_tree_is_valid(tree: RTree, expected: set[Indexed]) -> None:
    root_id = tree._root
    visited: list[int] = []

    def minimum_of(capacity: int) -> int:
        return max(MINIMUM_ENTRIES_PER_NODE, int(capacity * tree._minimum_fill))

    def walk(page_id: int, depth: int) -> tuple[int, Rectangle | None]:
        node = tree._load(page_id)
        visited.append(page_id)
        is_root = page_id == root_id
        if isinstance(node, LeafNode):
            assert len(node.entries) <= tree.leaf_capacity, f"hoja {page_id} desbordada"
            if not is_root:
                assert len(node.entries) >= minimum_of(tree.leaf_capacity), (
                    f"hoja {page_id} por debajo del mínimo"
                )
            if not node.entries:
                return depth, None
            return depth, Rectangle.enclosing(entry.bounds for entry in node.entries)
        assert isinstance(node, BranchNode)
        assert len(node.entries) <= tree.branch_capacity, f"nodo {page_id} desbordado"
        floor = MINIMUM_ENTRIES_PER_NODE if is_root else minimum_of(tree.branch_capacity)
        assert len(node.entries) >= floor, f"nodo {page_id} por debajo del mínimo"
        depths = set()
        for entry in node.entries:
            child_depth, child_cover = walk(entry.child, depth + 1)
            depths.add(child_depth)
            assert child_cover == entry.bounds, (
                f"el MBR de la entrada hacia {entry.child} no es el ajustado: "
                f"{entry.bounds} frente a {child_cover}"
            )
        assert len(depths) == 1, f"las hojas quedaron a distinta profundidad: {depths}"
        return depths.pop(), Rectangle.enclosing(entry.bounds for entry in node.entries)

    depth, _ = walk(root_id, 1)
    assert depth == tree.height
    assert len(visited) == len(set(visited)), "una página cuelga de dos padres"
    assert tree.node_count == len(visited)
    assert set(tree.scan()) == expected
    assert tree.entry_count == len(expected)
    assert tree.page_count - 1 == tree.node_count + _free_pages(tree)


def _free_pages(tree: RTree) -> int:
    count = 0
    page_id = tree._free_head
    while page_id != NO_NODE:
        count += 1
        page_id = tree._load(page_id).next_free
    return count
