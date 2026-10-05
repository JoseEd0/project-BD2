"""El índice espacial visto desde una tabla: de registros a puntos, y de puntos a filas.

El R-Tree se prueba aparte, con puntos. Aquí se comprueba la capa que lo conecta con las
filas: de qué columna saca el punto, qué hace con las filas sin ubicación y cómo responde
a cada tipo de búsqueda.
"""

from itertools import islice

import pytest
from rtree_support import record_id_of

from config import EngineConfig
from index.rtree import NotSpatialColumnError, SearchStats, SpatialIndex
from spatial.geometry import Point, Polygon
from spatial.metrics import EUCLIDEAN, HAVERSINE
from storage.record import RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType

PLACES = {
    1: Point(-12.05, -77.04),
    2: Point(-12.06, -77.03),
    3: Point(-12.20, -76.90),
    4: Point(-12.05, -77.04),
    5: None,
    6: Point(-13.50, -72.00),
}
SQUARE = Polygon((Point(-12.10, -77.10), Point(-12.10, -77.00), Point(-12.00, -77.00), Point(-12.00, -77.10)))


@pytest.fixture
def serializer() -> RecordSerializer:
    return RecordSerializer(
        Schema([Field("id", FieldType.INT, nullable=False), Field("ubicacion", FieldType.POINT)])
    )


@pytest.fixture
def rows(serializer: RecordSerializer) -> list[tuple[bytes, object]]:
    return [(serializer.pack((number, point)), record_id_of(number)) for number, point in PLACES.items()]


@pytest.fixture
def index(config: EngineConfig, serializer: RecordSerializer):
    path = config.data_directory / "lugares.rtree"
    with SpatialIndex(path, serializer, "ubicacion", config) as spatial:
        yield spatial


def numbers(record_ids) -> list[int]:
    by_address = {record_id_of(number): number for number in PLACES}
    return sorted(by_address[record_id] for record_id in record_ids)


def test_a_column_that_is_not_a_point_cannot_be_indexed(
    config: EngineConfig, serializer: RecordSerializer
):
    with pytest.raises(NotSpatialColumnError, match="'id' es INT"):
        SpatialIndex(config.data_directory / "mal.rtree", serializer, "id", config)


def test_rows_without_a_location_stay_out_of_the_index(index: SpatialIndex, rows):
    index.build(rows)
    assert index.entry_count == len(PLACES) - 1
    assert index.node_count >= 1


def test_bulk_loading_and_inserting_one_by_one_answer_alike(
    config: EngineConfig, serializer: RecordSerializer, index: SpatialIndex, rows
):
    index.build(rows)
    path = config.data_directory / "uno-a-uno.rtree"
    with SpatialIndex(path, serializer, "ubicacion", config) as inserted:
        for record, record_id in rows:
            inserted.insert(record, record_id)
        assert inserted.entry_count == index.entry_count
        for search in (
            lambda spatial: numbers(n.record_id for n in spatial.within_radius(PLACES[1], 5000.0, HAVERSINE)),
            lambda spatial: numbers(spatial.within_polygon(SQUARE)),
            lambda spatial: numbers(spatial.search(PLACES[1])),
        ):
            assert search(inserted) == search(index)


def test_searching_an_exact_point(index: SpatialIndex, rows):
    index.build(rows)
    assert numbers(index.search(PLACES[1])) == [1, 4]
    assert numbers(index.search((-12.20, -76.90))) == [3]
    assert index.search(Point(-12.0, -77.0)) == []
    assert index.search(None) == []


def test_searching_by_radius_polygon_and_nearness(index: SpatialIndex, rows):
    index.build(rows)
    stats = SearchStats()
    near = index.within_radius(PLACES[1], 2000.0, HAVERSINE, stats)
    assert numbers(neighbor.record_id for neighbor in near) == [1, 2, 4]
    assert stats.nodes_visited >= 1
    assert numbers(index.within_polygon(SQUARE)) == [1, 2, 4]
    closest = [neighbor.record_id for neighbor in islice(index.nearest(PLACES[3], EUCLIDEAN), 2)]
    assert numbers(closest[:1]) == [3]
    assert len(list(index.nearest(PLACES[3], HAVERSINE))) == len(PLACES) - 1


def test_deleting_a_row_removes_only_its_entry(index: SpatialIndex, rows):
    index.build(rows)
    record, record_id = rows[0]
    assert index.delete(record, record_id)
    assert not index.delete(record, record_id)
    assert numbers(index.search(PLACES[1])) == [4]
    without_location, its_address = rows[4]
    assert not index.delete(without_location, its_address)
    assert index.entry_count == len(PLACES) - 2


def test_the_index_survives_reopening(config: EngineConfig, serializer: RecordSerializer, rows):
    path = config.data_directory / "persistente.rtree"
    with SpatialIndex(path, serializer, "ubicacion", config) as first:
        first.build(rows)
        shape = first.describe()
    with SpatialIndex(path, serializer, "ubicacion", config) as second:
        assert second.describe() == shape
        assert numbers(second.search(PLACES[6])) == [6]
