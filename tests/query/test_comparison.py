"""Reglas de tipos al comparar: qué familias de valores se comparan entre sí."""

from datetime import date

import pytest

from query.comparison import (
    ComparisonError,
    comparable,
    literal_for,
    orderable,
    plainly_comparable,
)
from spatial.geometry import Point, Polygon
from storage.schema import Field
from storage.types import FieldType

TRIANGLE = Polygon((Point(0.0, 0.0), Point(0.0, 1.0), Point(1.0, 0.0)))


@pytest.mark.parametrize(
    ("left", "right"),
    [(1, 2.5), (2.5, 1), ("a", "b"), (True, False), (date(2024, 1, 5), date(2025, 1, 1))],
)
def test_values_of_one_family_are_compared_as_they_are(left: object, right: object):
    assert comparable(left, right) == (left, right)
    assert orderable(left, right) == (left, right)
    assert plainly_comparable(left, right)


@pytest.mark.parametrize(
    ("left", "right"),
    [(1, "1"), ("abc", 5.0), (True, 1), (date(2024, 1, 5), 20240105), (Point(1.0, 2.0), 5)],
)
def test_values_of_different_families_are_rejected(left: object, right: object):
    assert not plainly_comparable(left, right)
    with pytest.raises(ComparisonError):
        comparable(left, right)
    with pytest.raises(ComparisonError):
        orderable(right, left)


def test_a_date_written_as_iso_text_becomes_a_date():
    day = date(2024, 1, 5)
    assert comparable(day, "2024-01-05") == (day, day)
    assert orderable("2023-12-31", day) == (date(2023, 12, 31), day)


def test_text_that_is_not_a_date_cannot_be_compared_with_one():
    with pytest.raises(ComparisonError, match="no es una fecha"):
        comparable(date(2024, 1, 5), "2024-13-40")


def test_points_and_polygons_are_equal_or_not_but_have_no_order():
    assert comparable(Point(1.0, 2.0), (1.0, 2.0)) == (Point(1.0, 2.0), (1.0, 2.0))
    assert comparable(TRIANGLE, TRIANGLE) == (TRIANGLE, TRIANGLE)
    with pytest.raises(ComparisonError, match="no tiene orden"):
        orderable(Point(1.0, 2.0), Point(3.0, 4.0))
    with pytest.raises(ComparisonError, match="no tiene orden"):
        orderable(TRIANGLE, TRIANGLE)


def test_a_literal_takes_the_type_of_its_column():
    assert literal_for(Field("fecha", FieldType.DATE), "2024-01-05") == date(2024, 1, 5)
    whole = literal_for(Field("id", FieldType.INT), 5.0)
    assert whole == 5 and isinstance(whole, int)
    assert literal_for(Field("id", FieldType.INT), 5.5) == 5.5
    assert literal_for(Field("nota", FieldType.FLOAT), 3) == 3
    assert literal_for(Field("nombre", FieldType.STRING, 8), "ana") == "ana"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (Field("id", FieldType.INT), "abc"),
        (Field("nombre", FieldType.STRING, 8), 5),
        (Field("activo", FieldType.BOOL), 1),
        (Field("fecha", FieldType.DATE), "ayer"),
        (Field("ubicacion", FieldType.POINT), 5),
    ],
)
def test_a_literal_of_another_family_names_the_column_it_clashes_with(field: Field, value: object):
    with pytest.raises(ComparisonError, match=f"la columna '{field.name}' es {field.type.value}"):
        literal_for(field, value)


def test_long_values_are_cut_short_in_the_message():
    with pytest.raises(ComparisonError) as raised:
        comparable(10**300, "x")
    assert len(str(raised.value)) < 120
