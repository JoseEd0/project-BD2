"""Carga masiva: el árbol que sale tiene que ser tan legal como uno hecho insertando."""

import random
from itertools import islice

import pytest
from rtree_support import LIMA, TreeValidator, fill, random_points, record_id_of

from config import EngineConfig
from index.rtree import RTree, SearchStats
from index.rtree.bulk import tile
from index.rtree.node import LeafEntry
from spatial.geometry import Point, Rectangle
from spatial.metrics import EUCLIDEAN, HAVERSINE
from storage.types import StorageError

SEED = 20261004
MANY = 2_000


def test_loading_nothing_leaves_an_empty_tree(tree: RTree, assert_tree_is_valid: TreeValidator):
    tree.bulk_load([])
    assert tree.height == 1
    assert_tree_is_valid(tree, set())
    tree.insert(LIMA, record_id_of(0))
    assert_tree_is_valid(tree, {(LIMA, record_id_of(0))})


def test_a_few_points_fit_in_the_root(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = random_points(3, SEED)
    tree.bulk_load(entries)
    assert tree.height == 1
    assert_tree_is_valid(tree, set(entries))


@pytest.mark.parametrize("count", [*range(1, 60), 97, 121, 250, 333, 1_000])
def test_every_size_yields_a_legal_tree(
    tree: RTree, assert_tree_is_valid: TreeValidator, count: int
):
    """Los restos incómodos son el riesgo: el último nodo de cada franja no puede quedar
    por debajo del mínimo de ocupación."""
    entries = random_points(count, SEED + count)
    tree.bulk_load(entries)
    assert_tree_is_valid(tree, set(entries))


def test_searches_match_brute_force_after_loading(tree: RTree):
    entries = random_points(MANY, SEED)
    tree.bulk_load(entries)
    for radius in (500.0, 5_000.0, 40_000.0):
        expected = {entry for entry in entries if HAVERSINE.distance(LIMA, entry[0]) <= radius}
        found = {(n.point, n.record_id) for n in tree.search_radius(LIMA, radius, HAVERSINE)}
        assert found == expected
    expected_order = sorted(EUCLIDEAN.distance(LIMA, point) for point, _ in entries)
    assert [n.distance for n in tree.nearest(LIMA, EUCLIDEAN)] == expected_order
    rectangle = Rectangle(-12.1, -77.1, -12.0, -77.0)
    inside = {entry for entry in entries if rectangle.contains_point(entry[0])}
    assert set(tree.search_rectangle(rectangle)) == inside


def test_loaded_tree_accepts_inserts_and_deletes(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = random_points(600, SEED)
    loaded, later = entries[:400], entries[400:]
    tree.bulk_load(loaded)
    fill(tree, later)
    assert_tree_is_valid(tree, set(entries))
    order = list(entries)
    random.Random(SEED).shuffle(order)
    for point, record_id in order[:500]:
        assert tree.delete(point, record_id) is True
    assert_tree_is_valid(tree, set(order[500:]))


def test_loading_packs_tighter_than_inserting(config: EngineConfig):
    """Menos nodos y, sobre todo, menos nodos abiertos por consulta."""
    entries = random_points(MANY, SEED)
    with (
        RTree(config.data_directory / "masivo.rtree", config) as loaded,
        RTree(config.data_directory / "uno-a-uno.rtree", config) as inserted,
    ):
        loaded.bulk_load(entries)
        fill(inserted, entries)
        assert loaded.node_count < inserted.node_count
        loaded_stats, inserted_stats = SearchStats(), SearchStats()
        for point, _ in entries[:50]:
            list(islice(loaded.nearest(point, HAVERSINE, loaded_stats), 10))
            list(islice(inserted.nearest(point, HAVERSINE, inserted_stats), 10))
        assert loaded_stats.nodes_visited < inserted_stats.nodes_visited


def test_loading_into_a_tree_with_points_is_rejected(tree: RTree):
    tree.insert(LIMA, record_id_of(0))
    with pytest.raises(StorageError):
        tree.bulk_load(random_points(10, SEED))


def test_loading_leaves_no_temporary_files(config: EngineConfig):
    path = config.data_directory / "limpio.rtree"
    with RTree(path, config) as tree:
        tree.bulk_load(random_points(MANY, SEED))
    assert sorted(item.name for item in config.data_directory.iterdir()) == ["limpio.rtree"]


def test_loaded_tree_survives_reopening(config: EngineConfig, assert_tree_is_valid: TreeValidator):
    path = config.data_directory / "persistente.rtree"
    entries = random_points(500, SEED)
    with RTree(path, config) as first:
        first.bulk_load(entries)
    with RTree(path, config) as second:
        assert_tree_is_valid(second, set(entries))


def test_tiles_do_not_overlap_for_distinct_points():
    """La propiedad que da nombre al método: cada grupo cubre su propia baldosa."""
    generator = random.Random(SEED)
    entries = [
        LeafEntry(generator.uniform(0, 10), generator.uniform(0, 10), 1, number)
        for number in range(400)
    ]
    groups = list(tile(entries, 10, 4))
    assert sum(len(group) for group in groups) == 400
    assert all(4 <= len(group) <= 10 for group in groups)
    covers = [Rectangle.enclosing(entry.bounds for entry in group) for group in groups]
    overlapping = sum(
        1
        for first in range(len(covers))
        for second in range(first + 1, len(covers))
        if _overlap_area(covers[first], covers[second]) > 0.0
    )
    assert overlapping == 0


def _overlap_area(first: Rectangle, second: Rectangle) -> float:
    height = min(first.max_lat, second.max_lat) - max(first.min_lat, second.min_lat)
    width = min(first.max_lon, second.max_lon) - max(first.min_lon, second.min_lon)
    return max(height, 0.0) * max(width, 0.0)


def test_points_with_the_same_latitude_are_all_kept(
    tree: RTree, assert_tree_is_valid: TreeValidator
):
    entries = [(Point(-12.0, -77.0 + number / 500.0), record_id_of(number)) for number in range(300)]
    tree.bulk_load(entries)
    assert_tree_is_valid(tree, set(entries))
