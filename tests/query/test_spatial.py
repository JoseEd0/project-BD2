"""Consultas espaciales de punta a punta: del SQL a las filas.

Cada consulta se ejecuta dos veces, sin índice y con R-Tree, y las dos se comparan entre sí
y contra el cálculo directo en Python. Que el índice acelere no sirve de nada si cambia la
respuesta.
"""

import random

import pytest

from config import EngineConfig
from query.catalog import CatalogError
from query.engine import Engine, QueryResult
from query.expressions import ExpressionError
from query.loader import LoaderError
from spatial.geometry import Point, Polygon
from spatial.metrics import EUCLIDEAN, HAVERSINE
from storage.types import InvalidValueError

SEED = 20261004
STORES = 320
PLAZA = Point(-12.0464, -77.0428)
SPREAD_DEGREES = 0.15
DISTRICTS = ("lima", "miraflores", "surco")
BLOCK = Polygon(
    (Point(-12.10, -77.08), Point(-12.10, -77.00), Point(-12.02, -77.00), Point(-12.02, -77.08))
)
BLOCK_SQL = "POLYGON((-12.10, -77.08), (-12.10, -77.00), (-12.02, -77.00), (-12.02, -77.08))"
CREATE_INDEX = "CREATE INDEX idx_tiendas_ubicacion ON tiendas USING RTREE (ubicacion)"

Store = tuple[int, str, str, Point]


def build_stores() -> list[Store]:
    generator = random.Random(SEED)
    return [
        (
            number,
            f"tienda{number}",
            DISTRICTS[number % len(DISTRICTS)],
            Point(
                round(PLAZA.lat + generator.uniform(-SPREAD_DEGREES, SPREAD_DEGREES), 6),
                round(PLAZA.lon + generator.uniform(-SPREAD_DEGREES, SPREAD_DEGREES), 6),
            ),
        )
        for number in range(STORES)
    ]


STORE_ROWS = build_stores()


@pytest.fixture
def tiendas(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE tiendas ("
        "  id INT PRIMARY KEY, nombre VARCHAR(12), distrito VARCHAR(12), ubicacion POINT"
        ")"
    )
    values = ", ".join(
        f"({number}, '{name}', '{district}', POINT({point.lat}, {point.lon}))"
        for number, name, district, point in STORE_ROWS
    )
    engine.execute(f"INSERT INTO tiendas VALUES {values}")
    return engine


@pytest.fixture
def indexed(tiendas: Engine) -> Engine:
    tiendas.execute(CREATE_INDEX)
    return tiendas


def ids(result: QueryResult) -> list[int]:
    return [int(row[0]) for row in result.rows]


def plan_of(result: QueryResult) -> str:
    assert result.plan is not None
    return result.plan.render()


def within(radius: float, center: Point = PLAZA) -> set[int]:
    return {
        number
        for number, _, _, point in STORE_ROWS
        if HAVERSINE.distance(center, point) < radius
    }


def nearest(count: int, center: Point = PLAZA) -> list[int]:
    ranked = sorted(STORE_ROWS, key=lambda store: HAVERSINE.distance(center, store[3]))
    return [store[0] for store in ranked[:count]]


def test_points_round_trip_through_storage(tiendas: Engine):
    row = tiendas.execute("SELECT ubicacion FROM tiendas WHERE id = 7").rows[0]
    assert row[0] == STORE_ROWS[7][3]
    assert isinstance(row[0], Point)


def test_distance_is_haversine_in_metres_by_default(tiendas: Engine):
    result = tiendas.execute(
        "SELECT distancia(ubicacion, POINT(-12.0464, -77.0428)) AS metros FROM tiendas WHERE id = 3"
    )
    assert result.columns == ("metros",)
    assert result.rows[0][0] == pytest.approx(HAVERSINE.distance(PLAZA, STORE_ROWS[3][3]))


@pytest.mark.parametrize("option", ["metrica='euclidiana'", "metrica=euclidiana", "metric='euclidean'"])
def test_euclidean_distance_is_requested_by_name(tiendas: Engine, option: str):
    result = tiendas.execute(
        f"SELECT distancia(ubicacion, POINT(-12.0464, -77.0428), {option}) FROM tiendas WHERE id = 3"
    )
    assert result.rows[0][0] == pytest.approx(EUCLIDEAN.distance(PLAZA, STORE_ROWS[3][3]))


def test_a_coordinate_pair_works_as_a_point(tiendas: Engine):
    result = tiendas.execute(
        "SELECT distancia(ubicacion, (-12.0464, -77.0428)) FROM tiendas WHERE id = 3"
    )
    assert result.rows[0][0] == pytest.approx(HAVERSINE.distance(PLAZA, STORE_ROWS[3][3]))


def test_distance_to_a_missing_point_is_null(tiendas: Engine):
    tiendas.execute("INSERT INTO tiendas VALUES (9000, 'sin sitio', 'lima', NULL)")
    result = tiendas.execute(
        "SELECT distancia(ubicacion, POINT(-12.0464, -77.0428)) FROM tiendas WHERE id = 9000"
    )
    assert result.rows == ((None,),)
    inside = tiendas.execute(
        "SELECT id FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 99999999"
    )
    assert 9000 not in ids(inside)


@pytest.mark.parametrize("radius", [800, 5_000, 12_000, 60_000])
def test_radius_query_with_and_without_the_index(tiendas: Engine, radius: int):
    sql = f"SELECT id FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < {radius}"
    scanned = tiendas.execute(sql)
    assert "SequentialScan" in plan_of(scanned)
    tiendas.execute(CREATE_INDEX)
    searched = tiendas.execute(sql)
    assert "SpatialRangeScan" in plan_of(searched)
    assert "SequentialScan" not in plan_of(searched)
    assert set(ids(scanned)) == set(ids(searched)) == within(radius)


def test_radius_condition_can_be_written_the_other_way_round(indexed: Engine):
    forms = [
        "distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000",
        "5000 > distancia(ubicacion, POINT(-12.0464, -77.0428))",
        "distancia(POINT(-12.0464, -77.0428), ubicacion) < 5000",
        "distancia(ubicacion, (-12.0464, -77.0428)) < 5000",
    ]
    for form in forms:
        result = indexed.execute(f"SELECT id FROM tiendas WHERE {form}")
        assert "SpatialRangeScan" in plan_of(result), form
        assert set(ids(result)) == within(5_000), form


def test_strict_and_inclusive_radius_differ_exactly_at_the_border(indexed: Engine):
    border = HAVERSINE.distance(PLAZA, STORE_ROWS[11][3])
    query = "SELECT id FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428))"
    strict = set(ids(indexed.execute(f"{query} < {border!r}")))
    inclusive = set(ids(indexed.execute(f"{query} <= {border!r}")))
    assert 11 not in strict
    assert inclusive == strict | {11}


def test_euclidean_radius_uses_the_same_index(indexed: Engine):
    sql = (
        "SELECT id FROM tiendas "
        "WHERE distancia(ubicacion, POINT(-12.0464, -77.0428), metrica='euclidiana') < 0.05"
    )
    result = indexed.execute(sql)
    assert "SpatialRangeScan" in plan_of(result)
    assert "euclidiana" in plan_of(result)
    expected = {s[0] for s in STORE_ROWS if EUCLIDEAN.distance(PLAZA, s[3]) < 0.05}
    assert set(ids(result)) == expected


@pytest.mark.parametrize("count", [1, 10, 50])
def test_nearest_neighbours_with_and_without_the_index(tiendas: Engine, count: int):
    sql = (
        "SELECT id FROM tiendas "
        f"ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT {count}"
    )
    sorted_result = tiendas.execute(sql)
    assert "ExternalSort" in plan_of(sorted_result)
    tiendas.execute(CREATE_INDEX)
    indexed_result = tiendas.execute(sql)
    assert "SpatialNearestScan" in plan_of(indexed_result)
    assert "ExternalSort" not in plan_of(indexed_result)
    assert ids(sorted_result) == ids(indexed_result) == nearest(count)


def test_nearest_neighbours_through_a_select_alias(indexed: Engine):
    result = indexed.execute(
        "SELECT id, distancia(ubicacion, POINT(-12.0464, -77.0428)) AS metros "
        "FROM tiendas ORDER BY metros LIMIT 5"
    )
    assert "SpatialNearestScan" in plan_of(result)
    assert ids(result) == nearest(5)
    distances = [row[1] for row in result.rows]
    assert distances == sorted(distances)


def test_nearest_without_a_limit_returns_every_row_in_order(indexed: Engine):
    result = indexed.execute(
        "SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428))"
    )
    assert "SpatialNearestScan" in plan_of(result)
    assert ids(result) == nearest(STORES)


def test_nearest_with_a_filter_keeps_looking_until_it_has_enough(indexed: Engine):
    """«Las 5 tiendas de Surco más cercanas»: el índice entrega por cercanía y el filtro
    descarta las de otros distritos, sin perder el orden."""
    result = indexed.execute(
        "SELECT id FROM tiendas WHERE distrito = 'surco' "
        "ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 5"
    )
    plan = plan_of(result)
    assert "SpatialNearestScan" in plan
    assert "Filter" in plan
    expected = [n for n in nearest(STORES) if STORE_ROWS[n][2] == "surco"][:5]
    assert ids(result) == expected


def test_nearest_with_offset(indexed: Engine):
    result = indexed.execute(
        "SELECT id FROM tiendas "
        "ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 5 OFFSET 10"
    )
    assert ids(result) == nearest(15)[10:]


def test_farthest_first_cannot_use_the_index(indexed: Engine):
    result = indexed.execute(
        "SELECT id FROM tiendas "
        "ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) DESC LIMIT 3"
    )
    assert "ExternalSort" in plan_of(result)
    assert ids(result) == list(reversed(nearest(STORES)))[:3]


def test_a_second_sort_key_cannot_use_the_index(indexed: Engine):
    result = indexed.execute(
        "SELECT id FROM tiendas "
        "ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)), id LIMIT 3"
    )
    assert "ExternalSort" in plan_of(result)
    assert ids(result) == nearest(3)


def test_grouping_after_a_nearest_order_falls_back_to_sorting(indexed: Engine):
    result = indexed.execute("SELECT distrito, COUNT(*) AS n FROM tiendas GROUP BY distrito ORDER BY n")
    assert "SpatialNearestScan" not in plan_of(result)


def test_polygon_query_with_and_without_the_index(tiendas: Engine):
    sql = f"SELECT id FROM tiendas WHERE intersecta(ubicacion, {BLOCK_SQL})"
    scanned = tiendas.execute(sql)
    assert "SequentialScan" in plan_of(scanned)
    tiendas.execute(CREATE_INDEX)
    searched = tiendas.execute(sql)
    assert "SpatialPolygonScan" in plan_of(searched)
    expected = {s[0] for s in STORE_ROWS if BLOCK.contains(s[3])}
    assert expected
    assert set(ids(scanned)) == set(ids(searched)) == expected


def test_polygon_vertices_can_be_points(indexed: Engine):
    as_points = "POLYGON(POINT(-12.10, -77.08), POINT(-12.10, -77.00), POINT(-12.02, -77.00), POINT(-12.02, -77.08))"
    with_points = indexed.execute(f"SELECT id FROM tiendas WHERE intersecta(ubicacion, {as_points})")
    with_pairs = indexed.execute(f"SELECT id FROM tiendas WHERE intersecta(ubicacion, {BLOCK_SQL})")
    assert set(ids(with_points)) == set(ids(with_pairs))


def test_concave_polygon_filters_the_candidates_of_its_bounding_box(indexed: Engine):
    """El R-Tree busca por el rectángulo que encierra al polígono; la muesca de la L queda
    dentro de ese rectángulo y solo la comprobación exacta la deja fuera."""
    notch = "POLYGON((-12.15,-77.15),(-12.15,-76.95),(-12.05,-76.95),(-12.05,-77.05),(-11.95,-77.05),(-11.95,-77.15))"
    shape = Polygon(
        (
            Point(-12.15, -77.15),
            Point(-12.15, -76.95),
            Point(-12.05, -76.95),
            Point(-12.05, -77.05),
            Point(-11.95, -77.05),
            Point(-11.95, -77.15),
        )
    )
    result = indexed.execute(f"SELECT id FROM tiendas WHERE intersecta(ubicacion, {notch})")
    expected = {s[0] for s in STORE_ROWS if shape.contains(s[3])}
    in_the_box = {s[0] for s in STORE_ROWS if shape.bounding_box.contains_point(s[3])}
    assert expected < in_the_box
    assert set(ids(result)) == expected


def test_spatial_condition_combines_with_other_filters(indexed: Engine):
    result = indexed.execute(
        "SELECT id FROM tiendas WHERE distrito = 'miraflores' "
        "AND distancia(ubicacion, POINT(-12.0464, -77.0428)) < 9000"
    )
    assert "SpatialRangeScan" in plan_of(result)
    expected = {n for n in within(9_000) if STORE_ROWS[n][2] == "miraflores"}
    assert set(ids(result)) == expected


def test_aggregation_over_a_spatial_filter(indexed: Engine):
    result = indexed.execute(
        "SELECT distrito, COUNT(*) AS tiendas FROM tiendas "
        "WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 9000 GROUP BY distrito"
    )
    counted = {str(row[0]): row[1] for row in result.rows}
    for district in DISTRICTS:
        assert counted.get(district, 0) == sum(
            1 for n in within(9_000) if STORE_ROWS[n][2] == district
        )


def test_spatial_condition_inside_a_join_uses_the_index(indexed: Engine):
    indexed.execute("CREATE TABLE distritos (nombre VARCHAR(12) PRIMARY KEY, zona VARCHAR(8))")
    indexed.execute(
        "INSERT INTO distritos VALUES ('lima', 'centro'), ('miraflores', 'sur'), ('surco', 'sur')"
    )
    result = indexed.execute(
        "SELECT t.id, d.zona FROM distritos AS d JOIN tiendas AS t ON t.distrito = d.nombre "
        "WHERE distancia(t.ubicacion, POINT(-12.0464, -77.0428)) < 5000"
    )
    assert "SpatialRangeScan" in plan_of(result)
    assert set(ids(result)) == within(5_000)


def test_a_disjunction_cannot_use_the_index(indexed: Engine):
    result = indexed.execute(
        "SELECT id FROM tiendas WHERE distrito = 'lima' "
        "OR distancia(ubicacion, POINT(-12.0464, -77.0428)) < 3000"
    )
    assert "SequentialScan" in plan_of(result)


def test_a_distance_between_two_columns_cannot_use_the_index(indexed: Engine):
    result = indexed.execute("SELECT id FROM tiendas WHERE distancia(ubicacion, ubicacion) < 1")
    assert "SequentialScan" in plan_of(result)
    assert len(result.rows) == STORES


def test_the_plan_reports_how_many_nodes_were_opened(indexed: Engine):
    result = indexed.execute(
        "SELECT id FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 2000"
    )
    assert "nodos visitados" in plan_of(result)
    description = indexed.describe_table("tiendas")
    spatial = next(index for index in description["indexes"] if index["method"] == "RTREE")
    assert f"de {spatial['structure']['nodes']} nodos" in plan_of(result)


def test_explain_shows_the_spatial_path_without_running_it(indexed: Engine):
    result = indexed.execute(
        "EXPLAIN SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 4"
    )
    plan = plan_of(result)
    assert "SpatialNearestScan" in plan
    assert "nodos visitados" not in plan
    assert result.rows == ()


def test_the_index_follows_inserts_updates_and_deletes(indexed: Engine):
    query = "SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 1"
    indexed.execute("INSERT INTO tiendas VALUES (5000, 'nueva', 'lima', POINT(-12.0464, -77.0428))")
    assert ids(indexed.execute(query)) == [5000]
    indexed.execute("UPDATE tiendas SET ubicacion = POINT(-13.5, -72.0) WHERE id = 5000")
    assert ids(indexed.execute(query)) == nearest(1)
    far = "SELECT id FROM tiendas WHERE distancia(ubicacion, POINT(-13.5, -72.0)) < 100"
    assert ids(indexed.execute(far)) == [5000]
    indexed.execute("DELETE FROM tiendas WHERE id = 5000")
    assert ids(indexed.execute(far)) == []
    assert "SpatialRangeScan" in plan_of(indexed.execute(far))


@pytest.mark.parametrize("limit", ["", " LIMIT 1", " LIMIT 2", " LIMIT 5"])
def test_rows_without_a_point_are_ordered_like_a_sort_orders_them(tiendas: Engine, limit: str):
    """El R-Tree no guarda las filas sin ubicación, pero la consulta tiene que devolverlas
    donde las pone el ordenamiento: primero, porque su distancia es NULL."""
    tiendas.execute(
        "INSERT INTO tiendas VALUES (9000, 'sin sitio', 'lima', NULL), (9001, 'tampoco', 'lima', NULL)"
    )
    query = f"SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)){limit}"
    sorted_rows = tiendas.execute(query)
    tiendas.execute(CREATE_INDEX)
    by_index = tiendas.execute(query)
    assert "SpatialNearestScan" in plan_of(by_index)
    assert ids(by_index) == ids(sorted_rows)
    assert ids(by_index)[:2] == [9000, 9001][: len(by_index.rows)]


def test_rows_without_a_point_can_be_filtered_out_of_a_nearest_query(indexed: Engine):
    indexed.execute("INSERT INTO tiendas VALUES (9000, 'sin sitio', 'lima', NULL)")
    result = indexed.execute(
        "SELECT id FROM tiendas WHERE ubicacion IS NOT NULL "
        "ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 3"
    )
    assert ids(result) == nearest(3)
    indexed.execute("DELETE FROM tiendas WHERE id = 9000")
    assert len(indexed.execute("SELECT id FROM tiendas").rows) == STORES


def test_ties_come_out_in_the_same_order_with_and_without_the_index(engine: Engine):
    """Puntos repetidos y a la misma distancia: un LIMIT que corta en medio del empate
    tiene que devolver las mismas filas por los dos caminos."""
    engine.execute("CREATE TABLE malla (id INT PRIMARY KEY, ubicacion POINT)")
    cells = [(lat, lon) for lat in range(-3, 4) for lon in range(-3, 4)] * 3
    values = ", ".join(
        f"({number}, POINT({lat}, {lon}))" for number, (lat, lon) in enumerate(cells)
    )
    engine.execute(f"INSERT INTO malla VALUES {values}")
    queries = [
        f"SELECT id FROM malla ORDER BY distancia(ubicacion, POINT(0, 0){option}) LIMIT {limit}"
        for option in ("", ", metrica='euclidiana'")
        for limit in (1, 2, 4, 7, 20, 60, len(cells))
    ]
    sorted_ids = [ids(engine.execute(query)) for query in queries]
    engine.execute("CREATE INDEX idx_malla ON malla USING RTREE (ubicacion)")
    for query, expected in zip(queries, sorted_ids, strict=True):
        result = engine.execute(query)
        assert "SpatialNearestScan" in plan_of(result)
        assert ids(result) == expected, query


def test_an_alias_that_hides_a_column_orders_the_same_with_and_without_the_index(tiendas: Engine):
    """`ORDER BY id` es la columna `id`, aunque el SELECT llame `id` a una distancia."""
    query = (
        "SELECT distancia(ubicacion, POINT(-12.0464, -77.0428)) AS id FROM tiendas ORDER BY id LIMIT 5"
    )
    by_column = [HAVERSINE.distance(PLAZA, STORE_ROWS[number][3]) for number in range(5)]
    assert [row[0] for row in tiendas.execute(query).rows] == pytest.approx(by_column)
    tiendas.execute(CREATE_INDEX)
    indexed_result = tiendas.execute(query)
    assert "SpatialNearestScan" not in plan_of(indexed_result)
    assert [row[0] for row in indexed_result.rows] == pytest.approx(by_column)


@pytest.mark.parametrize(
    "condition", ["ubicacion = 5", "1.5 = ubicacion", "ubicacion = 'ab'", "ubicacion < 5"]
)
def test_a_point_compared_with_a_scalar_is_rejected_the_same_way(tiendas: Engine, condition: str):
    query = f"SELECT id FROM tiendas WHERE {condition}"
    with pytest.raises(ExpressionError, match="la columna 'ubicacion' es POINT"):
        tiendas.execute(query)
    tiendas.execute(CREATE_INDEX)
    with pytest.raises(ExpressionError, match="la columna 'ubicacion' es POINT"):
        tiendas.execute(query)


def test_points_can_be_compared_for_equality_but_not_ordered(indexed: Engine):
    lat, lon = STORE_ROWS[7][3]
    same = indexed.execute(f"SELECT id FROM tiendas WHERE ubicacion = POINT({lat}, {lon})")
    assert ids(same) == [7]
    with pytest.raises(ExpressionError, match="no tiene orden"):
        indexed.execute(f"SELECT id FROM tiendas WHERE ubicacion < POINT({lat}, {lon})")


def test_a_radius_on_the_edge_of_a_node_is_not_lost_to_rounding(engine: Engine):
    """Puntos sobre un mismo meridiano, con el radio justo en la distancia a cada uno: la
    cota del nodo no puede quedar, por redondeo, por encima de esa distancia."""
    center = Point(0.05, 20.0)
    points = [Point(round(-60 + 0.3 * number, 1), 20.0) for number in range(400)]
    engine.execute("CREATE TABLE linea (id INT PRIMARY KEY, ubicacion POINT)")
    values = ", ".join(f"({n}, POINT({p.lat}, {p.lon}))" for n, p in enumerate(points))
    engine.execute(f"INSERT INTO linea VALUES {values}")
    queries = [
        "SELECT id FROM linea WHERE distancia(ubicacion, "
        f"POINT({center.lat}, {center.lon})) <= {HAVERSINE.distance(center, point)!r}"
        for point in points
    ]
    scanned = [len(engine.execute(query).rows) for query in queries]
    engine.execute("CREATE INDEX idx_linea ON linea USING RTREE (ubicacion)")
    assert [len(engine.execute(query).rows) for query in queries] == scanned


def test_dropping_the_index_goes_back_to_scanning(indexed: Engine):
    indexed.execute("DROP INDEX idx_tiendas_ubicacion")
    result = indexed.execute(
        "SELECT id FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000"
    )
    assert "SequentialScan" in plan_of(result)
    assert set(ids(result)) == within(5_000)


def test_the_index_survives_reopening_the_engine(tiendas: Engine, config: EngineConfig):
    tiendas.execute(CREATE_INDEX)
    tiendas.close()
    with Engine(config) as reopened:
        result = reopened.execute(
            "SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 7"
        )
        assert "SpatialNearestScan" in plan_of(result)
        assert ids(result) == nearest(7)


def test_index_declared_in_create_table(engine: Engine):
    engine.execute("CREATE TABLE sitios (id INT PRIMARY KEY, lugar POINT INDEX RTREE)")
    engine.execute("INSERT INTO sitios VALUES (1, POINT(-12.0, -77.0)), (2, POINT(-13.0, -72.0))")
    result = engine.execute("SELECT id FROM sitios ORDER BY distancia(lugar, POINT(-13.1, -72.1)) LIMIT 1")
    assert "SpatialNearestScan" in plan_of(result)
    assert ids(result) == [2]


def test_structure_describes_the_tree(indexed: Engine):
    description = indexed.describe_table("tiendas")
    spatial = next(index for index in description["indexes"] if index["method"] == "RTREE")
    structure = spatial["structure"]
    assert structure["kind"] == "rtree"
    assert structure["entries"] == STORES
    assert structure["height"] == len(structure["levels"]) >= 2


def test_rtree_needs_a_point_column(tiendas: Engine):
    with pytest.raises(CatalogError, match="POINT"):
        tiendas.execute("CREATE INDEX idx_nombre ON tiendas USING RTREE (nombre)")
    assert tiendas.table("tiendas").definition.index_on("nombre") is None


@pytest.mark.parametrize("method", ["BTREE", "HASH"])
def test_point_columns_only_take_an_rtree(tiendas: Engine, method: str):
    with pytest.raises(CatalogError, match="RTREE"):
        tiendas.execute(f"CREATE INDEX idx_mal ON tiendas USING {method} (ubicacion)")


def test_a_point_cannot_be_the_primary_key(engine: Engine):
    with pytest.raises(CatalogError, match="clave primaria"):
        engine.execute("CREATE TABLE mal (lugar POINT PRIMARY KEY, nombre VARCHAR(8))")
    assert engine.table_names() == []


def test_rtree_needs_a_heap_table(engine: Engine):
    engine.execute("CREATE TABLE ordenada (id INT PRIMARY KEY INDEX SEQ, lugar POINT)")
    with pytest.raises(CatalogError, match="heap"):
        engine.execute("CREATE INDEX idx_lugar ON ordenada USING RTREE (lugar)")


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("distancia(ubicacion, POINT(95, 0)) < 1", "latitud"),
        ("distancia(ubicacion, POINT(0, 200)) < 1", "longitud"),
        ("distancia(ubicacion, POINT(0, 0), metrica='manhattan') < 1", "métrica desconocida"),
        ("distancia(ubicacion) < 1", "al menos 2"),
        ("distancia(ubicacion, POINT(0, 0), POINT(1, 1)) < 1", "como mucho 2"),
        ("distancia(ubicacion, POINT(0, 0), radio=3) < 1", "radio"),
        ("distancia(nombre, POINT(0, 0)) < 1", "no puede ser la columna 'nombre', que es STRING"),
        ("distancia(ubicacion, (nombre, 1)) < 1", "se esperaba un número"),
        ("distanca(ubicacion, POINT(0, 0)) < 1", "no existe"),
        ("intersecta(ubicacion, POINT(0, 0))", "POLYGON"),
        ("intersecta(ubicacion, POLYGON((0, 0), (1, 1)))", "al menos 3"),
        ("intersecta(ubicacion, POLYGON((0, 0), (1, 1), (0, 0)))", "vértices distintos"),
        ("COUNT(id) > 1", "agregación"),
    ],
)
def test_malformed_spatial_expressions_are_rejected(tiendas: Engine, expression: str, message: str):
    with pytest.raises(ExpressionError, match=message):
        tiendas.execute(f"SELECT id FROM tiendas WHERE {expression}")


def test_malformed_expressions_fail_even_on_an_empty_table(engine: Engine):
    """El error es de la consulta, no de los datos: no puede depender de que haya filas."""
    engine.execute("CREATE TABLE vacia (id INT PRIMARY KEY, lugar POINT)")
    with pytest.raises(ExpressionError, match="latitud"):
        engine.execute("SELECT id FROM vacia WHERE distancia(lugar, POINT(95, 0)) < 1")
    with pytest.raises(ExpressionError, match="no existe"):
        engine.execute("SELECT id FROM vacia ORDER BY cercania(lugar, POINT(0, 0))")


def test_out_of_range_points_cannot_be_stored(tiendas: Engine):
    with pytest.raises(ExpressionError, match="latitud"):
        tiendas.execute("INSERT INTO tiendas VALUES (7000, 'x', 'lima', POINT(91, 0))")
    with pytest.raises(InvalidValueError, match="longitud"):
        tiendas.execute("INSERT INTO tiendas VALUES (7000, 'x', 'lima', (0, 181))")
    assert tiendas.execute("SELECT id FROM tiendas WHERE id = 7000").rows == ()


def write_csv(config: EngineConfig, body: str) -> str:
    path = config.data_directory / "sitios.csv"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_points_are_inferred_from_a_csv(engine: Engine, config: EngineConfig):
    path = write_csv(
        config,
        'id,nombre,ubicacion\n1,plaza,"POINT(-12.0464, -77.0428)"\n2,cusco,"point(-13.5167,-71.9781)"\n3,sin sitio,\n',
    )
    engine.execute(f"CREATE TABLE sitios FROM FILE '{path}' USING INDEX HASH(\"id\")")
    rows = engine.execute("SELECT id, ubicacion FROM sitios ORDER BY id").rows
    assert rows == ((1, Point(-12.0464, -77.0428)), (2, Point(-13.5167, -71.9781)), (3, None))


def test_csv_loaded_with_an_rtree_gets_the_index_but_no_key(engine: Engine, config: EngineConfig):
    path = write_csv(
        config,
        'id,ubicacion\n1,"POINT(-12.0, -77.0)"\n2,"POINT(-12.0, -77.0)"\n3,"POINT(-13.5, -72.0)"\n',
    )
    engine.execute(f"CREATE TABLE sitios FROM FILE '{path}' USING INDEX RTREE(\"ubicacion\")")
    definition = engine.table("sitios").definition
    assert definition.primary_key is None
    assert [(index.column, index.method.value) for index in definition.indexes] == [
        ("ubicacion", "RTREE")
    ]
    result = engine.execute("SELECT id FROM sitios WHERE distancia(ubicacion, POINT(-12.0, -77.0)) < 10")
    assert "SpatialRangeScan" in plan_of(result)
    assert sorted(ids(result)) == [1, 2]


def test_a_csv_with_a_bad_point_creates_nothing(engine: Engine, config: EngineConfig):
    path = write_csv(config, 'id,ubicacion\n1,"POINT(-12.0, -77.0)"\n2,"POINT(-120.0, -77.0)"\n')
    with pytest.raises(LoaderError, match="latitud"):
        engine.execute(f"CREATE TABLE sitios FROM FILE '{path}'")
    assert engine.table_names() == []


def test_a_column_mixing_points_and_text_stays_text(engine: Engine, config: EngineConfig):
    path = write_csv(config, 'id,ubicacion\n1,"POINT(-12.0, -77.0)"\n2,desconocida\n')
    engine.execute(f"CREATE TABLE sitios FROM FILE '{path}'")
    assert engine.table("sitios").schema.field_of("ubicacion").type.value == "STRING"


def test_rtree_on_a_csv_column_that_is_not_a_point_creates_nothing(
    engine: Engine, config: EngineConfig
):
    path = write_csv(config, "id,nombre\n1,ana\n")
    with pytest.raises(CatalogError, match="POINT"):
        engine.execute(f"CREATE TABLE sitios FROM FILE '{path}' USING INDEX RTREE(\"nombre\")")
    assert engine.table_names() == []


def test_the_map_gets_the_circle_of_a_radius_query(indexed: Engine):
    result = indexed.execute(
        "SELECT * FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000"
    )
    view = result.spatial
    assert view is not None
    assert (view.table, view.column) == ("tiendas", "ubicacion")
    (search,) = view.radius_searches
    assert search.target.center == PLAZA
    assert search.radius == 5_000.0
    assert search.target.metric is HAVERSINE
    assert view.nearest_searches == view.polygon_searches == ()


def test_the_map_gets_the_reference_point_of_a_nearest_query(tiendas: Engine):
    """La figura no depende de que haya índice: describe la consulta, no el plan."""
    result = tiendas.execute(
        "SELECT * FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 3"
    )
    assert result.spatial is not None
    (target,) = result.spatial.nearest_searches
    assert target.center == PLAZA


def test_the_map_gets_the_polygon(indexed: Engine):
    result = indexed.execute(f"SELECT * FROM tiendas WHERE intersecta(ubicacion, {BLOCK_SQL})")
    assert result.spatial is not None
    (search,) = result.spatial.polygon_searches
    assert search.polygon == BLOCK


def test_a_plain_query_over_a_table_with_points_still_feeds_the_map(tiendas: Engine):
    result = tiendas.execute("SELECT * FROM tiendas LIMIT 5")
    assert result.spatial is not None
    assert (result.spatial.table, result.spatial.column) == ("tiendas", "ubicacion")
    assert result.spatial.radius_searches == ()


def test_a_query_without_points_has_nothing_for_the_map(alumnos: Engine):
    assert alumnos.execute("SELECT * FROM alumnos").spatial is None
