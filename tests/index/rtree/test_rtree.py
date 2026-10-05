import random
from itertools import islice

import pytest
from rtree_support import LIMA, Indexed, TreeValidator, fill, random_points, record_id_of

from config import EngineConfig
from index.rtree import RTree, SearchStats
from index.rtree.node import CorruptNodeError, NodeCapacityError
from index.rtree.tree import TreeFormatError
from spatial.geometry import Point, Rectangle
from spatial.metrics import EUCLIDEAN, HAVERSINE, Metric
from storage.heap import HeapFile
from storage.record_id import RecordId

SEED = 20261004
MANY = 700
# Cinco consultas repartidas por la ciudad: en el centro, en un borde y fuera de los datos.
CENTERS = [
    LIMA,
    Point(-12.12, -77.03),
    Point(-11.85, -76.80),
    Point(-12.29, -77.29),
    Point(-13.0, -76.0),
]


def test_empty_tree(tree: RTree, assert_tree_is_valid: TreeValidator):
    assert tree.entry_count == 0
    assert tree.height == 1
    assert list(tree.scan()) == []
    assert list(tree.search_rectangle(Rectangle(-90.0, -180.0, 90.0, 180.0))) == []
    assert list(tree.search_radius(LIMA, 1e9, HAVERSINE)) == []
    assert list(tree.nearest(LIMA, HAVERSINE)) == []
    assert_tree_is_valid(tree, set())


def test_single_point(tree: RTree, assert_tree_is_valid: TreeValidator):
    tree.insert(LIMA, record_id_of(0))
    assert list(tree.search_rectangle(Rectangle.around(LIMA))) == [(LIMA, record_id_of(0))]
    (neighbor,) = tree.nearest(Point(-12.0, -77.0), HAVERSINE)
    assert neighbor.point == LIMA
    assert neighbor.record_id == record_id_of(0)
    assert neighbor.distance == pytest.approx(HAVERSINE.distance(Point(-12.0, -77.0), LIMA))
    assert_tree_is_valid(tree, {(LIMA, record_id_of(0))})


def test_the_same_point_can_be_indexed_for_several_rows(
    tree: RTree, assert_tree_is_valid: TreeValidator
):
    entries = [(LIMA, record_id_of(number)) for number in range(3)]
    fill(tree, entries)
    assert set(tree.search_rectangle(Rectangle.around(LIMA))) == set(entries)
    assert_tree_is_valid(tree, set(entries))


def test_leaf_split_grows_the_tree(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = random_points(tree.leaf_capacity + 1, SEED)
    fill(tree, entries)
    assert tree.height == 2
    assert tree.node_count == 3
    assert_tree_is_valid(tree, set(entries))


def test_internal_split_grows_the_tree(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = random_points(tree.leaf_capacity * tree.branch_capacity + 1, SEED)
    fill(tree, entries)
    assert tree.height >= 3
    assert_tree_is_valid(tree, set(entries))


def test_random_insertion(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    assert_tree_is_valid(tree, set(entries))


def test_insertion_sorted_by_latitude(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = sorted(random_points(MANY, SEED))
    fill(tree, entries)
    assert_tree_is_valid(tree, set(entries))


def test_points_on_a_single_line(tree: RTree, assert_tree_is_valid: TreeValidator):
    """Todos los MBR tienen área cero: la división no puede apoyarse en el área."""
    entries = [(Point(-12.0, -77.0 + number / 1000.0), record_id_of(number)) for number in range(200)]
    fill(tree, entries)
    assert_tree_is_valid(tree, set(entries))
    found = set(tree.search_rectangle(Rectangle(-12.0, -76.95, -12.0, -76.90)))
    assert found == {entry for entry in entries if -76.95 <= entry[0].lon <= -76.90}


@pytest.mark.parametrize(
    "layout",
    [
        lambda number: Point(-12.0, -77.0 + number / 1000.0),
        lambda number: Point(-12.0 + number / 1000.0, -77.0),
        lambda number: Point(float(number // 40), float(number % 40)),
    ],
    ids=["un paralelo", "un meridiano", "una malla entera"],
)
def test_aligned_points_are_still_pruned(tree: RTree, assert_tree_is_valid: TreeValidator, layout):
    """Con los puntos en línea el área de todo MBR es cero y no decide nada. El desempate
    por semiperímetro tiene que seguir agrupando por cercanía: una búsqueda pequeña abre
    una rama, no el árbol entero."""
    entries = [(layout(number), record_id_of(number)) for number in range(1600)]
    random.Random(SEED).shuffle(entries)
    fill(tree, entries)
    assert_tree_is_valid(tree, set(entries))
    center = entries[0][0]
    for search in (
        lambda stats: list(tree.search_radius(center, 0.0, EUCLIDEAN, stats)),
        lambda stats: list(islice(tree.nearest(center, HAVERSINE, stats), 3)),
    ):
        stats = SearchStats()
        assert search(stats)
        assert stats.nodes_visited <= tree.node_count / 10


def test_many_rows_at_the_same_point(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = [(LIMA, record_id_of(number)) for number in range(150)]
    fill(tree, entries)
    assert_tree_is_valid(tree, set(entries))
    assert len(list(tree.search_radius(LIMA, 0.0, HAVERSINE))) == 150


def _inside(entries: list[Indexed], rectangle: Rectangle) -> set[Indexed]:
    return {entry for entry in entries if rectangle.contains_point(entry[0])}


def test_rectangle_search_matches_brute_force(tree: RTree):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    generator = random.Random(SEED)
    for _ in range(40):
        lat = LIMA.lat + generator.uniform(-0.3, 0.3)
        lon = LIMA.lon + generator.uniform(-0.3, 0.3)
        rectangle = Rectangle(lat, lon, lat + generator.uniform(0, 0.2), lon + generator.uniform(0, 0.2))
        assert set(tree.search_rectangle(rectangle)) == _inside(entries, rectangle)


def test_rectangle_search_includes_its_border(tree: RTree):
    corner = Point(-12.0, -77.0)
    tree.insert(corner, record_id_of(0))
    assert list(tree.search_rectangle(Rectangle(-12.0, -77.0, -11.0, -76.0))) == [(corner, record_id_of(0))]


@pytest.mark.parametrize(
    ("metric", "radii"),
    [(HAVERSINE, [0.0, 500.0, 2_000.0, 10_000.0, 80_000.0]), (EUCLIDEAN, [0.0, 0.01, 0.05, 0.2, 1.0])],
)
def test_radius_search_matches_brute_force(tree: RTree, metric: Metric, radii: list[float]):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    for center in CENTERS:
        for radius in radii:
            expected = {entry for entry in entries if metric.distance(center, entry[0]) <= radius}
            found = list(tree.search_radius(center, radius, metric))
            assert {(neighbor.point, neighbor.record_id) for neighbor in found} == expected
            assert len(found) == len(expected)
            for neighbor in found:
                assert neighbor.distance == metric.distance(center, neighbor.point)


def test_radius_search_includes_points_exactly_at_the_radius(tree: RTree):
    point = Point(-12.0, -77.0)
    tree.insert(point, record_id_of(0))
    center = Point(-12.5, -77.0)
    radius = HAVERSINE.distance(center, point)
    assert len(list(tree.search_radius(center, radius, HAVERSINE))) == 1
    assert list(tree.search_radius(center, radius * 0.999, HAVERSINE)) == []


@pytest.mark.parametrize("metric", [HAVERSINE, EUCLIDEAN])
def test_nearest_returns_every_point_in_distance_order(tree: RTree, metric: Metric):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    for center in CENTERS:
        neighbors = list(tree.nearest(center, metric))
        distances = [neighbor.distance for neighbor in neighbors]
        assert distances == sorted(distances)
        assert distances == sorted(metric.distance(center, point) for point, _ in entries)
        assert {(neighbor.point, neighbor.record_id) for neighbor in neighbors} == set(entries)


@pytest.mark.parametrize("metric", [HAVERSINE, EUCLIDEAN])
@pytest.mark.parametrize("k", [1, 10, 50])
def test_k_nearest_matches_brute_force(tree: RTree, metric: Metric, k: int):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    for center in CENTERS:
        expected = sorted(metric.distance(center, point) for point, _ in entries)[:k]
        found = [neighbor.distance for neighbor in islice(tree.nearest(center, metric), k)]
        assert found == expected


@pytest.mark.parametrize("metric", [HAVERSINE, EUCLIDEAN])
def test_points_at_the_same_distance_come_out_in_row_address_order(tree: RTree, metric: Metric):
    """Muchas filas repartidas entre unos pocos puntos, insertadas en desorden: dentro de
    cada empate, el orden es el de la dirección de la fila."""
    spots = [Point(lat, lon) for lat in (-1.0, 0.0, 1.0) for lon in (-1.0, 0.0, 1.0)]
    entries = [(spots[number % len(spots)], record_id_of(number)) for number in range(MANY)]
    random.Random(SEED).shuffle(entries)
    fill(tree, entries)
    center = Point(0.0, 0.0)
    found = [(neighbor.distance, neighbor.record_id) for neighbor in tree.nearest(center, metric)]
    assert found == sorted((metric.distance(center, point), address) for point, address in entries)


def test_metrics_can_disagree_on_who_is_nearest(tree: RTree):
    """A 60° de latitud un grado de longitud mide la mitad que uno de latitud."""
    by_longitude = Point(60.0, 1.5)
    by_latitude = Point(61.0, 0.0)
    tree.insert(by_longitude, record_id_of(0))
    tree.insert(by_latitude, record_id_of(1))
    center = Point(60.0, 0.0)
    assert next(tree.nearest(center, EUCLIDEAN)).point == by_latitude
    assert next(tree.nearest(center, HAVERSINE)).point == by_longitude


def test_a_small_radius_opens_only_a_few_nodes(tree: RTree):
    fill(tree, random_points(MANY, SEED))
    stats = SearchStats()
    found = list(tree.search_radius(LIMA, 1_000.0, HAVERSINE, stats))
    assert found
    assert 0 < stats.nodes_visited < tree.node_count / 4


def test_nearest_is_lazy(tree: RTree):
    """Pedir un solo vecino no abre el árbol entero: es lo que hace barato el k-NN."""
    fill(tree, random_points(MANY, SEED))
    first, everything = SearchStats(), SearchStats()
    next(tree.nearest(LIMA, HAVERSINE, first))
    list(tree.nearest(LIMA, HAVERSINE, everything))
    assert everything.nodes_visited == tree.node_count
    assert first.nodes_visited <= tree.height * 3


def test_delete_missing_point(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = random_points(30, SEED)
    fill(tree, entries)
    assert tree.delete(Point(0.0, 0.0), record_id_of(0)) is False
    point, _ = entries[0]
    assert tree.delete(point, RecordId(page_id=999, slot=0)) is False
    assert_tree_is_valid(tree, set(entries))


def test_delete_the_only_point(tree: RTree, assert_tree_is_valid: TreeValidator):
    tree.insert(LIMA, record_id_of(0))
    assert tree.delete(LIMA, record_id_of(0)) is True
    assert_tree_is_valid(tree, set())


def test_delete_one_of_several_rows_at_the_same_point(
    tree: RTree, assert_tree_is_valid: TreeValidator
):
    entries = [(LIMA, record_id_of(number)) for number in range(40)]
    fill(tree, entries)
    assert tree.delete(LIMA, record_id_of(17)) is True
    remaining = set(entries) - {(LIMA, record_id_of(17))}
    assert_tree_is_valid(tree, remaining)


def test_deleting_everything_condenses_back_to_a_single_leaf(
    tree: RTree, assert_tree_is_valid: TreeValidator
):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    assert tree.height >= 3
    remaining = set(entries)
    order = list(entries)
    random.Random(SEED).shuffle(order)
    for number, (point, record_id) in enumerate(order):
        assert tree.delete(point, record_id) is True
        remaining.discard((point, record_id))
        if number % 25 == 0:
            assert_tree_is_valid(tree, remaining)
    assert tree.height == 1
    assert tree.node_count == 1
    assert_tree_is_valid(tree, set())


def test_searches_stay_correct_while_deleting(tree: RTree):
    entries = random_points(400, SEED)
    fill(tree, entries)
    alive = list(entries)
    generator = random.Random(SEED)
    while len(alive) > 20:
        for _ in range(40):
            point, record_id = alive.pop(generator.randrange(len(alive)))
            assert tree.delete(point, record_id)
        expected = {entry for entry in alive if HAVERSINE.distance(LIMA, entry[0]) <= 15_000.0}
        found = {(n.point, n.record_id) for n in tree.search_radius(LIMA, 15_000.0, HAVERSINE)}
        assert found == expected
        nearest = [n.distance for n in islice(tree.nearest(LIMA, HAVERSINE), 5)]
        assert nearest == sorted(HAVERSINE.distance(LIMA, point) for point, _ in alive)[:5]


def test_pages_are_reused_after_deletions(tree: RTree, assert_tree_is_valid: TreeValidator):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    pages_when_full = tree.page_count
    for point, record_id in entries:
        tree.delete(point, record_id)
    fill(tree, entries)
    assert tree.page_count <= pages_when_full
    assert_tree_is_valid(tree, set(entries))


def test_interleaved_insert_and_delete(tree: RTree, assert_tree_is_valid: TreeValidator):
    generator = random.Random(SEED)
    pool = random_points(900, SEED)
    alive: set[Indexed] = set()
    for step in range(2_500):
        entry = pool[generator.randrange(len(pool))]
        if entry in alive:
            assert tree.delete(*entry) is True
            alive.discard(entry)
        else:
            tree.insert(*entry)
            alive.add(entry)
        if step % 250 == 0:
            assert_tree_is_valid(tree, alive)
    assert_tree_is_valid(tree, alive)


def test_state_survives_reopening(config: EngineConfig, assert_tree_is_valid: TreeValidator):
    path = config.data_directory / "persistente.rtree"
    entries = random_points(300, SEED)
    with RTree(path, config) as first:
        fill(first, entries)
        height = first.height
    with RTree(path, config) as second:
        assert second.height == height
        assert_tree_is_valid(second, set(entries))
        expected = sorted(HAVERSINE.distance(LIMA, point) for point, _ in entries)[:10]
        assert [n.distance for n in islice(second.nearest(LIMA, HAVERSINE), 10)] == expected


def test_opening_another_kind_of_file_is_rejected(config: EngineConfig):
    path = config.data_directory / "tabla.heap"
    with HeapFile(path, 16, config):
        pass
    with pytest.raises(TreeFormatError):
        RTree(path, config)


def test_page_too_small_is_rejected(config: EngineConfig):
    tiny = EngineConfig(page_size=64, data_directory=config.data_directory)
    with pytest.raises(NodeCapacityError):
        RTree(config.data_directory / "diminuto.rtree", tiny)


def test_describe_reports_every_level(tree: RTree):
    entries = random_points(MANY, SEED)
    fill(tree, entries)
    description = tree.describe()
    assert description["kind"] == "rtree"
    assert description["height"] == tree.height == len(description["levels"])
    assert description["entries"] == MANY
    assert description["levels"][-1]["kind"] == "hojas"
    assert description["levels"][-1]["entry_count"] == MANY
    assert sum(level["node_count"] for level in description["levels"]) == tree.node_count
    root_bounds = Rectangle(*description["levels"][0]["nodes"][0]["bounds"])
    assert all(root_bounds.contains_point(point) for point, _ in entries)


def test_describe_an_empty_tree(tree: RTree):
    description = tree.describe()
    assert description["height"] == 1
    assert description["levels"][0]["nodes"] == [{"bounds": None, "entry_count": 0}]


def test_a_page_that_is_not_a_node_is_reported_as_corrupt(config: EngineConfig):
    """La cabecera está intacta, pero las páginas de los nodos se han pisado con basura."""
    path = config.data_directory / "roto.rtree"
    with RTree(path, config) as tree:
        tree.insert(LIMA, record_id_of(1))
    raw = path.read_bytes()
    header = raw[: config.page_size]
    path.write_bytes(header + b"\xff" * (len(raw) - len(header)))
    with RTree(path, config) as tree, pytest.raises(CorruptNodeError):
        list(tree.search_radius(LIMA, 10.0, HAVERSINE))
