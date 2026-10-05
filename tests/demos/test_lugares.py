"""El conversor de puntos de interés de OpenStreetMap, sin tocar la red.

Se le da un shapefile mínimo construido aquí mismo, con el formato que publica Geofabrik:
puntos en el `.shp`, atributos de ancho fijo en el `.dbf`.
"""

import importlib.util
import io
import struct
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from config import EngineConfig
from query.engine import Engine
from spatial.geometry import Point

REPOSITORY = Path(__file__).resolve().parents[2]
FIELDS = (("osm_id", 12), ("code", 4), ("fclass", 28), ("name", 100))
POINT_SHAPE, NULL_SHAPE = 1, 0


@pytest.fixture(scope="module")
def lugares() -> ModuleType:
    path = REPOSITORY / "demos" / "descargar_lugares.py"
    spec = importlib.util.spec_from_file_location("descargar_lugares", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def shp_of(points: list[tuple[float, float] | None]) -> bytes:
    """Un `.shp`: cabecera de 100 bytes y, por registro, longitud y latitud en ese orden."""
    body = b""
    for number, point in enumerate(points, start=1):
        content = (
            struct.pack("<i", NULL_SHAPE)
            if point is None
            else struct.pack("<idd", POINT_SHAPE, point[1], point[0])
        )
        body += struct.pack(">ii", number, len(content) // 2) + content
    return bytes(100) + body


def dbf_of(rows: list[tuple[str, str, str, str] | None]) -> bytes:
    """Un `.dbf`: descriptores de 32 bytes por campo y registros de ancho fijo."""
    record_bytes = 1 + sum(length for _, length in FIELDS)
    header_bytes = 32 + 32 * len(FIELDS) + 1
    header = struct.pack("<4xIHH20x", len(rows), header_bytes, record_bytes)
    descriptors = b"".join(
        struct.pack("<11sc4xB15x", name.encode(), b"C", length) for name, length in FIELDS
    )
    records = b""
    for row in rows:
        values = ("", "", "", "") if row is None else row
        flag = b"*" if row is None else b" "
        cells = b"".join(
            value.encode("utf-8").ljust(length) for value, (_, length) in zip(values, FIELDS, strict=True)
        )
        records += flag + cells
    return header + descriptors + b"\r" + records


def archive_of(layers: dict[str, tuple[bytes, bytes]]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for layer, (shp, dbf) in layers.items():
            archive.writestr(f"{layer}.shp", shp)
            archive.writestr(f"{layer}.dbf", dbf)
            archive.writestr(f"{layer}.cpg", "UTF-8")
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def test_points_come_out_as_latitude_then_longitude(lugares: ModuleType):
    shp = shp_of([(-12.0464, -77.0428), None, (-13.5167, -71.9781)])
    assert list(lugares.read_points(io.BytesIO(shp))) == [
        (-12.0464, -77.0428),
        None,
        (-13.5167, -71.9781),
    ]


def test_an_empty_shapefile_has_no_points(lugares: ModuleType):
    assert list(lugares.read_points(io.BytesIO(shp_of([])))) == []
    assert list(lugares.read_attributes(io.BytesIO(dbf_of([])), "utf-8")) == []


def test_attributes_are_read_by_field_name(lugares: ModuleType):
    dbf = dbf_of([("101", "2301", "restaurant", "Cevichería El Muelle"), None, ("103", "2101", "pharmacy", "")])
    assert list(lugares.read_attributes(io.BytesIO(dbf), "utf-8")) == [
        {"osm_id": "101", "code": "2301", "fclass": "restaurant", "name": "Cevichería El Muelle"},
        None,
        {"osm_id": "103", "code": "2101", "fclass": "pharmacy", "name": ""},
    ]


def test_places_join_both_files_translate_the_category_and_skip_repeats(lugares: ModuleType):
    pois = (
        shp_of([(-12.05, -77.04), (-12.06, -77.03), (-12.07, -77.02), (-12.08, -77.01), (-12.09, -77.0)]),
        dbf_of(
            [
                ("1", "2301", "restaurant", "El Muelle"),
                ("2", "2101", "pharmacy", "Botica Central"),
                ("2", "2501", "supermarket", "Repetido"),
                ("4", "9999", "etiqueta_sin_traducir", "Sitio Raro"),
                ("7", "2902", "bench", ""),
            ]
        ),
    )
    traffic = (
        shp_of([(-12.10, -77.00), (-12.11, -77.00), (-12.12, -77.00)]),
        dbf_of(
            [
                ("5", "5250", "fuel", "Grifo Norte"),
                ("6", "5201", "traffic_signals", "Cruce"),
                ("8", "5250", "fuel", ""),
            ]
        ),
    )
    archive = archive_of({"gis_osm_pois_free_1": pois, "gis_osm_traffic_free_1": traffic})
    places = list(lugares.read_places(archive))
    assert [(place.identifier, place.name, place.category) for place in places] == [
        (1, "El Muelle", "restaurante"),
        (2, "Botica Central", "farmacia"),
        (4, "Sitio Raro", "etiqueta_sin_traducir"),
        (5, "Grifo Norte", "gasolinera"),
    ]
    assert (places[0].latitude, places[0].longitude) == (-12.05, -77.04)


def test_a_place_without_a_name_is_left_out_and_does_not_take_its_identifier(lugares: ModuleType):
    """El primer registro del nodo 3 no tiene nombre; el segundo sí, y es el que queda."""
    pois = (
        shp_of([(-12.05, -77.04), (-12.05, -77.04)]),
        dbf_of([("3", "2902", "bench", ""), ("3", "2301", "restaurant", "La Esquina")]),
    )
    archive = archive_of({"gis_osm_pois_free_1": pois, "gis_osm_traffic_free_1": (shp_of([]), dbf_of([]))})
    places = list(lugares.read_places(archive))
    assert [(place.identifier, place.name, place.category) for place in places] == [
        (3, "La Esquina", "restaurante")
    ]


def test_every_translated_category_is_written_in_spanish(lugares: ModuleType):
    assert lugares.RUBROS["guesthouse"] == "hospedaje"
    assert lugares.RUBROS["town_hall"] == "municipalidad"
    assert all(value == value.lower() and "_" not in value for value in lugares.RUBROS.values())


def test_the_box_keeps_only_what_is_inside(lugares: ModuleType):
    box = lugares.Box(south=-12.2, west=-77.1, north=-12.0, east=-77.0)
    inside = lugares.Place(1, "a", "x", -12.1, -77.05)
    outside = lugares.Place(2, "b", "x", -13.5, -71.9)
    on_the_edge = lugares.Place(3, "c", "x", -12.0, -77.0)
    assert [box.contains(place) for place in (inside, outside, on_the_edge)] == [True, False, True]


def test_the_csv_is_loaded_by_the_engine_as_it_is(lugares: ModuleType, config: EngineConfig):
    places = [
        lugares.Place(11, "Botica Central", "farmacia", -12.0464, -77.0428),
        lugares.Place(12, "", "gasolinera", -12.1211, -77.0297),
        lugares.Place(13, 'Bar "El Ancla", Callao', "bar", -12.05, -77.15),
    ]
    path = config.data_directory / "lugares.csv"
    assert lugares.write_csv(iter(places), path) == 3
    with Engine(config) as engine:
        engine.execute(f"CREATE TABLE lugares FROM FILE '{path}' USING INDEX RTREE(\"ubicacion\")")
        nearest = engine.execute(
            "SELECT id, nombre, rubro, ubicacion FROM lugares "
            "ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 3"
        )
    assert nearest.rows == (
        (11, "Botica Central", "farmacia", Point(-12.0464, -77.0428)),
        (12, None, "gasolinera", Point(-12.1211, -77.0297)),
        (13, 'Bar "El Ancla", Callao', "bar", Point(-12.05, -77.15)),
    )
