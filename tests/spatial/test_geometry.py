import pytest

from spatial.geometry import GeometryError, Point, Polygon, Rectangle, geographic_point

SQUARE = Polygon((Point(0.0, 0.0), Point(0.0, 4.0), Point(4.0, 4.0), Point(4.0, 0.0)))
# Polígono cóncavo en forma de L: la muesca deja fuera el cuadrante superior derecho.
CONCAVE = Polygon(
    (
        Point(0.0, 0.0),
        Point(0.0, 4.0),
        Point(2.0, 4.0),
        Point(2.0, 2.0),
        Point(4.0, 2.0),
        Point(4.0, 0.0),
    )
)


def test_point_keeps_latitude_first():
    point = geographic_point(-12.0464, -77.0428)
    assert point.lat == -12.0464
    assert point.lon == -77.0428
    assert point == (-12.0464, -77.0428)


@pytest.mark.parametrize(("lat", "lon"), [(90.5, 0.0), (-91.0, 0.0), (0.0, 180.5), (0.0, -181.0)])
def test_coordinates_out_of_range_are_rejected(lat: float, lon: float):
    with pytest.raises(GeometryError):
        geographic_point(lat, lon)


def test_poles_and_antimeridian_are_valid():
    assert geographic_point(90.0, 180.0) == Point(90.0, 180.0)
    assert geographic_point(-90.0, -180.0) == Point(-90.0, -180.0)


def test_rectangle_around_a_point_has_no_area():
    rectangle = Rectangle.around(Point(1.0, 2.0))
    assert rectangle == Rectangle(1.0, 2.0, 1.0, 2.0)
    assert rectangle.area == 0.0


def test_union_covers_both_rectangles():
    union = Rectangle(0.0, 0.0, 1.0, 1.0).union(Rectangle(2.0, -1.0, 3.0, 0.5))
    assert union == Rectangle(0.0, -1.0, 3.0, 1.0)


def test_enlargement_is_the_extra_area():
    base = Rectangle(0.0, 0.0, 2.0, 2.0)
    assert base.enlargement(Rectangle(1.0, 1.0, 2.0, 2.0)) == 0.0
    assert base.enlargement(Rectangle(0.0, 0.0, 2.0, 4.0)) == 4.0


def test_enclosing_several_rectangles():
    rectangles = [Rectangle.around(Point(1.0, 5.0)), Rectangle.around(Point(-2.0, 3.0))]
    assert Rectangle.enclosing(rectangles) == Rectangle(-2.0, 3.0, 1.0, 5.0)


def test_enclosing_nothing_is_an_error():
    with pytest.raises(GeometryError):
        Rectangle.enclosing([])


def test_rectangles_that_only_touch_do_intersect():
    assert Rectangle(0.0, 0.0, 1.0, 1.0).intersects(Rectangle(1.0, 1.0, 2.0, 2.0))
    assert not Rectangle(0.0, 0.0, 1.0, 1.0).intersects(Rectangle(1.1, 0.0, 2.0, 1.0))


def test_rectangle_containment():
    outer = Rectangle(0.0, 0.0, 4.0, 4.0)
    assert outer.contains(Rectangle(1.0, 1.0, 2.0, 2.0))
    assert not outer.contains(Rectangle(1.0, 1.0, 5.0, 2.0))
    assert outer.contains_point(Point(4.0, 0.0))
    assert not outer.contains_point(Point(4.1, 0.0))


def test_polygon_needs_three_distinct_vertices():
    with pytest.raises(GeometryError):
        Polygon((Point(0.0, 0.0), Point(1.0, 1.0)))
    with pytest.raises(GeometryError):
        Polygon((Point(0.0, 0.0), Point(1.0, 1.0), Point(0.0, 0.0)))


def test_polygon_bounding_box():
    assert CONCAVE.bounding_box == Rectangle(0.0, 0.0, 4.0, 4.0)


@pytest.mark.parametrize("point", [Point(2.0, 2.0), Point(0.5, 3.5), Point(3.9, 0.1)])
def test_points_inside_the_square(point: Point):
    assert SQUARE.contains(point)


@pytest.mark.parametrize("point", [Point(-0.1, 2.0), Point(2.0, 4.1), Point(5.0, 5.0)])
def test_points_outside_the_square(point: Point):
    assert not SQUARE.contains(point)


@pytest.mark.parametrize(
    "point", [Point(0.0, 0.0), Point(4.0, 4.0), Point(0.0, 2.0), Point(2.0, 4.0), Point(4.0, 1.0)]
)
def test_the_boundary_counts_as_inside(point: Point):
    assert SQUARE.contains(point)


def test_concave_polygon_excludes_its_notch():
    assert CONCAVE.contains(Point(1.0, 3.0))
    assert CONCAVE.contains(Point(3.0, 1.0))
    assert not CONCAVE.contains(Point(3.0, 3.0))
    assert CONCAVE.contains(Point(2.0, 3.0))


def test_a_ray_through_a_vertex_is_counted_once():
    """El rayo desde (2, -1) pasa exactamente por el vértice (2, 2) de la muesca."""
    assert not CONCAVE.contains(Point(2.0, -1.0))
    assert CONCAVE.contains(Point(2.0, 1.0))


def test_vertex_order_does_not_matter():
    reversed_square = Polygon(tuple(reversed(SQUARE.vertices)))
    assert reversed_square.contains(Point(2.0, 2.0))
    assert not reversed_square.contains(Point(5.0, 2.0))
