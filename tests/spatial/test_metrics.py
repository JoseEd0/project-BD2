"""Las métricas se validan contra valores conocidos y contra fuerza bruta.

`min_distance` es la pieza de la que depende que el R-Tree no pierda resultados, así que no
basta con un par de ejemplos: se compara con el mínimo real sobre una malla densa del
rectángulo, para rectángulos y puntos generados al azar.
"""

import random
from math import pi

import pytest

from spatial.geometry import Point, Rectangle
from spatial.metrics import (
    BOUND_SLACK,
    EARTH_RADIUS_METERS,
    EUCLIDEAN,
    HAVERSINE,
    Metric,
    UnknownMetricError,
    metric_named,
)

SEED = 20261004
RANDOM_CASES = 300
GRID_STEPS = 40
PLAZA_DE_ARMAS_LIMA = Point(-12.0464, -77.0428)
PLAZA_DE_ARMAS_CUSCO = Point(-13.5167, -71.9781)


def test_euclidean_distance_is_the_hypotenuse():
    assert EUCLIDEAN.distance(Point(0.0, 0.0), Point(3.0, 4.0)) == 5.0


def test_haversine_one_degree_of_latitude():
    expected = EARTH_RADIUS_METERS * pi / 180.0
    assert HAVERSINE.distance(Point(0.0, 0.0), Point(1.0, 0.0)) == pytest.approx(expected)


def test_haversine_one_degree_of_longitude_shrinks_with_latitude():
    at_equator = HAVERSINE.distance(Point(0.0, 0.0), Point(0.0, 1.0))
    at_sixty = HAVERSINE.distance(Point(60.0, 0.0), Point(60.0, 1.0))
    assert at_sixty == pytest.approx(at_equator / 2.0, rel=1e-4)


def test_haversine_pole_to_equator_is_a_quarter_circle():
    quarter = EARTH_RADIUS_METERS * pi / 2.0
    assert HAVERSINE.distance(Point(90.0, 0.0), Point(0.0, 45.0)) == pytest.approx(quarter)


def test_haversine_antipodes_are_half_a_circle():
    half = EARTH_RADIUS_METERS * pi
    assert HAVERSINE.distance(Point(10.0, 20.0), Point(-10.0, -160.0)) == pytest.approx(half)


def test_haversine_lima_to_cusco():
    """573 km en línea recta sobre la esfera, medido también con ST_DistanceSphere."""
    distance = HAVERSINE.distance(PLAZA_DE_ARMAS_LIMA, PLAZA_DE_ARMAS_CUSCO)
    assert distance == pytest.approx(573_000, rel=0.01)


def test_haversine_crosses_the_antimeridian():
    across = HAVERSINE.distance(Point(0.0, 179.5), Point(0.0, -179.5))
    assert across == pytest.approx(EARTH_RADIUS_METERS * pi / 180.0)


@pytest.mark.parametrize("metric", [EUCLIDEAN, HAVERSINE])
def test_distance_is_zero_to_itself_and_symmetric(metric: Metric):
    assert metric.distance(PLAZA_DE_ARMAS_LIMA, PLAZA_DE_ARMAS_LIMA) == 0.0
    forward = metric.distance(PLAZA_DE_ARMAS_LIMA, PLAZA_DE_ARMAS_CUSCO)
    backward = metric.distance(PLAZA_DE_ARMAS_CUSCO, PLAZA_DE_ARMAS_LIMA)
    assert forward == pytest.approx(backward)


@pytest.mark.parametrize("metric", [EUCLIDEAN, HAVERSINE])
def test_min_distance_is_zero_inside_the_rectangle(metric: Metric):
    rectangle = Rectangle(-13.0, -78.0, -12.0, -77.0)
    assert metric.min_distance(Point(-12.5, -77.5), rectangle) == 0.0
    assert metric.min_distance(Point(-12.0, -77.0), rectangle) == 0.0


def test_euclidean_min_distance_to_a_corner_and_to_a_side():
    rectangle = Rectangle(0.0, 0.0, 2.0, 2.0)
    assert EUCLIDEAN.min_distance(Point(5.0, 6.0), rectangle) == 5.0
    assert EUCLIDEAN.min_distance(Point(1.0, -3.0), rectangle) == 3.0


def test_haversine_min_distance_along_a_meridian():
    rectangle = Rectangle(10.0, 20.0, 20.0, 30.0)
    expected = EARTH_RADIUS_METERS * pi / 180.0 * 5.0
    assert HAVERSINE.min_distance(Point(5.0, 25.0), rectangle) == pytest.approx(expected)


def test_haversine_min_distance_is_not_always_at_a_corner():
    """Desde 60°N, el punto más cercano del borde oeste queda entre sus dos extremos."""
    rectangle = Rectangle(0.0, 60.0, 80.0, 70.0)
    point = Point(60.0, 0.0)
    to_corners = min(
        HAVERSINE.distance(point, Point(0.0, 60.0)), HAVERSINE.distance(point, Point(80.0, 60.0))
    )
    assert HAVERSINE.min_distance(point, rectangle) < to_corners


def test_haversine_min_distance_wraps_around_the_antimeridian():
    rectangle = Rectangle(-1.0, 170.0, 1.0, 179.0)
    expected = HAVERSINE.distance(Point(0.0, -179.0), Point(0.0, 179.0))
    assert HAVERSINE.min_distance(Point(0.0, -179.0), rectangle) == pytest.approx(expected)


def _random_rectangle(generator: random.Random) -> Rectangle:
    lats = sorted(generator.uniform(-89.0, 89.0) for _ in range(2))
    lons = sorted(generator.uniform(-179.0, 179.0) for _ in range(2))
    return Rectangle(lats[0], lons[0], lats[1], lons[1])


def _grid(rectangle: Rectangle) -> list[Point]:
    return [
        Point(
            rectangle.min_lat + (rectangle.max_lat - rectangle.min_lat) * row / GRID_STEPS,
            rectangle.min_lon + (rectangle.max_lon - rectangle.min_lon) * column / GRID_STEPS,
        )
        for row in range(GRID_STEPS + 1)
        for column in range(GRID_STEPS + 1)
    ]


@pytest.mark.parametrize("metric", [EUCLIDEAN, HAVERSINE])
def test_min_distance_never_exceeds_the_distance_to_any_point_inside(metric: Metric):
    """Cota inferior: si fallara, el R-Tree podaría un nodo que sí tiene resultados."""
    generator = random.Random(SEED)
    for _ in range(RANDOM_CASES):
        rectangle = _random_rectangle(generator)
        point = Point(generator.uniform(-90.0, 90.0), generator.uniform(-180.0, 180.0))
        bound = metric.min_distance(point, rectangle)
        closest = min(metric.distance(point, inside) for inside in _grid(rectangle))
        assert bound <= closest * (1.0 + 1e-9) + 1e-6


@pytest.mark.parametrize("metric", [EUCLIDEAN, HAVERSINE])
def test_min_distance_is_tight(metric: Metric):
    """Cota ajustada: una cota floja seguiría siendo correcta, pero no podaría nada.

    La malla solo se acerca al mínimo real, así que se admite el error de medio paso.
    """
    generator = random.Random(SEED + 1)
    for _ in range(RANDOM_CASES):
        rectangle = _random_rectangle(generator)
        point = Point(generator.uniform(-90.0, 90.0), generator.uniform(-180.0, 180.0))
        bound = metric.min_distance(point, rectangle)
        grid = _grid(rectangle)
        closest = min(metric.distance(point, inside) for inside in grid)
        step = max(
            metric.distance(grid[0], grid[1]), metric.distance(grid[0], grid[GRID_STEPS + 1])
        )
        assert closest - bound <= step


def test_metrics_are_found_by_name_and_alias():
    assert metric_named("haversine") is HAVERSINE
    assert metric_named("Geodesica") is HAVERSINE
    assert metric_named(" EUCLIDIANA ") is EUCLIDEAN
    assert metric_named("euclidean") is EUCLIDEAN


def test_unknown_metric_is_rejected_with_the_alternatives():
    with pytest.raises(UnknownMetricError, match="haversine"):
        metric_named("manhattan")


def test_haversine_bound_does_not_round_above_the_distance_along_a_meridian():
    """Sin tolerancia: con el punto consultado sobre el mismo meridiano, la cota de un
    rectángulo y la distancia a su punto más cercano son la misma cantidad calculada por dos
    caminos, y el redondeo no puede dejar la cota por encima."""
    center = Point(0.05, 20.0)
    for number in range(400):
        point = Point(round(-60.0 + 0.3 * number, 1), 20.0)
        bound = HAVERSINE.min_distance(center, Rectangle.around(point))
        assert bound <= HAVERSINE.distance(center, point)


@pytest.mark.parametrize("metric", [EUCLIDEAN, HAVERSINE])
def test_min_distance_never_rounds_above_the_distance_to_a_corner(metric: Metric):
    """Sin tolerancia y contra puntos que sí son del rectángulo: sus cuatro esquinas."""
    generator = random.Random(SEED + 2)
    for _ in range(RANDOM_CASES * 10):
        rectangle = _random_rectangle(generator)
        point = Point(generator.uniform(-90.0, 90.0), generator.uniform(-180.0, 180.0))
        bound = metric.min_distance(point, rectangle)
        for lat in (rectangle.min_lat, rectangle.max_lat):
            for lon in (rectangle.min_lon, rectangle.max_lon):
                assert bound <= metric.distance(point, Point(lat, lon))


def test_the_slack_taken_off_the_haversine_bound_is_negligible():
    rectangle = Rectangle(10.0, 20.0, 20.0, 30.0)
    exact = EARTH_RADIUS_METERS * pi / 180.0 * 5.0
    bound = HAVERSINE.min_distance(Point(5.0, 25.0), rectangle)
    assert exact * (1.0 - 2.0 * BOUND_SLACK) < bound < exact
