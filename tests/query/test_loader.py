"""Inferencia de tipos al cargar un CSV: qué se reconoce como número, fecha o punto."""

from datetime import date
from pathlib import Path

import pytest

from config import EngineConfig
from query.loader import LoaderError, infer_schema, read_header, read_values
from spatial.geometry import Point
from storage.schema import Field, Schema
from storage.types import FieldType


def write_csv(config: EngineConfig, body: str) -> Path:
    path = config.data_directory / "datos.csv"
    path.write_text(body, encoding="utf-8")
    return path


def types_of(config: EngineConfig, body: str) -> dict[str, FieldType]:
    return {field.name: field.type for field in infer_schema(write_csv(config, body), config)}


@pytest.mark.parametrize("latitude", ["-12.04", "5e-05", ".5", "-12.", "+3", "1E1"])
def test_a_point_takes_any_spelling_of_a_real(config: EngineConfig, latitude: str):
    path = write_csv(config, f'id,ubicacion\n1,"POINT({latitude}, -77.03)"\n')
    schema = infer_schema(path, config)
    assert schema.field_of("ubicacion").type is FieldType.POINT
    assert list(read_values(path, schema, config)) == [(1, Point(float(latitude), -77.03))]


@pytest.mark.parametrize(
    "cell", ["POINT(abc, 1)", "POINT(nan, 1)", "POINT(1, inf)", "POINT(1)", "POINT(1, 2, 3)", "PUNTO(1, 2)"]
)
def test_what_is_not_a_pair_of_finite_numbers_is_not_a_point(config: EngineConfig, cell: str):
    assert types_of(config, f'id,ubicacion\n1,"{cell}"\n')["ubicacion"] is FieldType.STRING


def test_an_integer_beyond_64_bits_is_kept_as_text(config: EngineConfig):
    path = write_csv(config, "id,codigo\n1,12345678901234567890123\n2,7\n")
    schema = infer_schema(path, config)
    assert schema.field_of("codigo").type is FieldType.STRING
    assert [row[1] for row in read_values(path, schema, config)] == ["12345678901234567890123", "7"]


@pytest.mark.parametrize("cell", ["nan", "inf", "-Infinity", "²", "1²"])
def test_names_that_look_like_numbers_are_text(config: EngineConfig, cell: str):
    assert types_of(config, f"id,valor\n1,{cell}\n")["valor"] is FieldType.STRING


def test_numbers_and_dates_are_still_recognised(config: EngineConfig):
    body = "entero,real,mixto,fecha,texto\n1,1.5,2,2024-01-05,ana\n-7,2e3,2.5,2023-12-31,2024\n"
    assert types_of(config, body) == {
        "entero": FieldType.INT,
        "real": FieldType.FLOAT,
        "mixto": FieldType.FLOAT,
        "fecha": FieldType.DATE,
        "texto": FieldType.STRING,
    }


def test_every_inferred_type_is_converted(config: EngineConfig):
    body = (
        "id,precio,alta,activo,nombre,sitio\n"
        '1,9.5,2024-01-05,true,ana,"POINT(-12.0, -77.0)"\n'
        '2,7,2023-12-31,FALSE,2024,"POINT(-13.5, -72.0)"\n'
    )
    path = write_csv(config, body)
    schema = infer_schema(path, config)
    assert [field.type for field in schema] == [
        FieldType.INT,
        FieldType.FLOAT,
        FieldType.DATE,
        FieldType.BOOL,
        FieldType.STRING,
        FieldType.POINT,
    ]
    assert list(read_values(path, schema, config)) == [
        (1, 9.5, date(2024, 1, 5), True, "ana", Point(-12.0, -77.0)),
        (2, 7.0, date(2023, 12, 31), False, "2024", Point(-13.5, -72.0)),
    ]


def test_an_empty_cell_is_null_whatever_the_type(config: EngineConfig):
    path = write_csv(config, "id,precio,alta,activo,nombre\n1,9.5,2024-01-05,true,ana\n2,,,,\n")
    schema = infer_schema(path, config)
    assert list(read_values(path, schema, config))[1] == (2, None, None, None, None)


def test_a_column_without_values_is_an_integer_column(config: EngineConfig):
    assert types_of(config, "id,vacia\n1,\n2,\n")["vacia"] is FieldType.INT


@pytest.mark.parametrize(
    ("values", "kind"),
    [
        ("true,false", FieldType.BOOL),
        ("True,FALSE", FieldType.BOOL),
        ("true,1", FieldType.STRING),
        ("1,0", FieldType.INT),
        ("2024-01-05,7", FieldType.STRING),
        ("si,no", FieldType.STRING),
    ],
)
def test_a_column_takes_the_type_that_fits_all_its_values(
    config: EngineConfig, values: str, kind: FieldType
):
    first, second = values.split(",")
    assert types_of(config, f"id,valor\n1,{first}\n2,{second}\n")["valor"] is kind


def test_blank_lines_are_skipped(config: EngineConfig):
    path = write_csv(config, "id,nombre\n1,ana\n\n2,luis\n\n")
    schema = infer_schema(path, config)
    assert list(read_values(path, schema, config)) == [(1, "ana"), (2, "luis")]


def test_the_header_gives_the_column_names_without_surrounding_spaces(config: EngineConfig):
    assert read_header(write_csv(config, " id , nombre completo\n1,ana\n"), config) == [
        "id",
        "nombre completo",
    ]


def test_a_text_column_is_as_long_as_its_longest_value_up_to_the_limit(config: EngineConfig):
    long_name = "x" * (config.text_length * 2)
    schema = infer_schema(write_csv(config, f"id,corto,largo\n1,ana,{long_name}\n"), config)
    assert schema.field_of("corto").length < schema.field_of("largo").length
    assert schema.field_of("largo").length == config.text_length


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "no tiene cabecera"),
        ("id,nombre\n1,ana,sobra\n", "la fila 2 .* tiene 3 campos y la cabecera 2"),
        ("id,nombre\n1,ana\n2\n", "la fila 3 .* tiene 1 campos"),
    ],
)
def test_a_malformed_file_says_what_is_wrong(config: EngineConfig, body: str, message: str):
    with pytest.raises(LoaderError, match=message):
        infer_schema(write_csv(config, body), config)


def test_a_missing_file_is_reported(config: EngineConfig):
    with pytest.raises(LoaderError, match="no existe el archivo"):
        infer_schema(config.data_directory / "fantasma.csv", config)


def test_a_file_in_another_encoding_is_reported(config: EngineConfig):
    path = config.data_directory / "latin.csv"
    path.write_bytes("id,nombre\n1,añejo\n".encode("latin-1"))
    with pytest.raises(LoaderError, match="no se puede leer como CSV"):
        infer_schema(path, config)


@pytest.mark.parametrize(
    ("kind", "cell", "message"),
    [
        (FieldType.POINT, "Lima", "no es un punto"),
        (FieldType.POINT, "POINT(100, 2)", "punto inválido"),
        (FieldType.BOOL, "quizá", "no es un booleano"),
    ],
)
def test_a_value_that_does_not_fit_a_declared_type_is_reported(
    config: EngineConfig, kind: FieldType, cell: str, message: str
):
    """Con un esquema dado por quien llama, no deducido del archivo."""
    path = write_csv(config, f'id,valor\n1,"{cell}"\n')
    schema = Schema([Field("id", FieldType.INT), Field("valor", kind)])
    with pytest.raises(LoaderError, match=message):
        list(read_values(path, schema, config))
