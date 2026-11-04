"""Filas con TEXT fuera del registro: lo que se guarda, lo que se reutiliza y lo que se libera."""

from collections.abc import Iterator

import pytest

from config import EngineConfig
from storage.out_of_row import MissingValueStoreError, OutOfRowSerializer
from storage.record import RecordSerializer
from storage.record_id import RECORD_ID_SIZE
from storage.schema import Field, FieldType, Schema
from storage.types import InvalidValueError, codec_for
from storage.varlen import VariableLengthFile

SCHEMA = Schema(
    [
        Field("id", FieldType.INT, nullable=False),
        Field("cuerpo", FieldType.TEXT),
        Field("nota", FieldType.FLOAT),
        Field("resumen", FieldType.TEXT),
    ]
)
# Con páginas de 256 bytes, un valor así es una cadena de varias decenas de fragmentos.
LONG_TEXT = "señal " * 2_000


@pytest.fixture
def values(config: EngineConfig) -> Iterator[VariableLengthFile]:
    with VariableLengthFile(config.data_directory / "t.text", config) as file:
        yield file


@pytest.fixture
def rows(values: VariableLengthFile) -> OutOfRowSerializer:
    return OutOfRowSerializer(RecordSerializer(SCHEMA), values)


def test_a_text_field_keeps_only_the_address_in_the_record():
    codec = codec_for(FieldType.TEXT, None)
    assert codec.size == RECORD_ID_SIZE
    assert RecordSerializer(SCHEMA).size < len(LONG_TEXT)
    with pytest.raises(InvalidValueError):
        codec_for(FieldType.TEXT, 10)
    with pytest.raises(InvalidValueError, match="dirección"):
        codec.to_slots("un texto")


def test_a_schema_without_text_needs_no_value_file():
    plain = Schema([Field("id", FieldType.INT), Field("nombre", FieldType.STRING, 8)])
    rows = OutOfRowSerializer(RecordSerializer(plain), None)
    assert rows.unpack(rows.pack((1, "ana"))) == (1, "ana")
    assert rows.normalized((1, "ana")) == (1, "ana")


def test_a_schema_with_text_requires_a_value_file():
    with pytest.raises(MissingValueStoreError):
        OutOfRowSerializer(RecordSerializer(SCHEMA), None)


def test_a_row_round_trips_with_long_short_empty_and_null_texts(
    rows: OutOfRowSerializer, values: VariableLengthFile
):
    for row in [(1, LONG_TEXT, 2.5, ""), (2, None, None, "corto")]:
        record = rows.pack(row)
        assert len(record) == rows.size
        assert rows.unpack(record) == row
        assert rows.unpack_field(record, 1) == row[1]
    assert values.value_count == 3


def test_normalizing_converts_like_the_record_and_writes_nothing(
    rows: OutOfRowSerializer, values: VariableLengthFile
):
    assert rows.normalized((1, LONG_TEXT, 3, None)) == (1, LONG_TEXT, 3.0, None)
    assert values.value_count == 0


@pytest.mark.parametrize(
    "row",
    [
        (1, "válido", "no es un número", "válido"),
        (1, "válido", 1.0, 42),
        (None, "válido", 1.0, "válido"),
        (1, "faltan campos"),
    ],
)
def test_an_invalid_row_stores_no_value(
    rows: OutOfRowSerializer, values: VariableLengthFile, row: tuple
):
    with pytest.raises(InvalidValueError):
        rows.pack(row)
    assert values.value_count == 0


def test_repacking_keeps_an_unchanged_text_at_its_address(
    rows: OutOfRowSerializer, values: VariableLengthFile, monkeypatch: pytest.MonkeyPatch
):
    before = (1, LONG_TEXT, 1.0, "resumen")
    record = rows.pack(before)
    writes: list[str] = []
    monkeypatch.setattr(values, "put", lambda value: writes.append("put"))
    monkeypatch.setattr(values, "replace", lambda address, value: writes.append("replace"))
    after = (1, LONG_TEXT, 9.0, "resumen")
    repacked = rows.repack(record, before, after)
    assert writes == []
    stored = RecordSerializer(SCHEMA)
    for position in (1, 3):
        assert stored.unpack_field(repacked, position) == stored.unpack_field(record, position)
    assert rows.unpack(repacked) == after


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("corto", LONG_TEXT),
        (LONG_TEXT, "corto"),
        (None, "aparece"),
        ("desaparece", None),
        (None, None),
    ],
)
def test_repacking_a_changed_text_leaves_exactly_the_live_values(
    rows: OutOfRowSerializer, values: VariableLengthFile, first: str | None, second: str | None
):
    record = rows.pack((1, first, None, "fijo"))
    repacked = rows.repack(record, (1, first, None, "fijo"), (1, second, None, "fijo"))
    assert rows.unpack(repacked) == (1, second, None, "fijo")
    assert values.value_count == (second is not None) + 1


def test_an_invalid_replacement_changes_no_value(
    rows: OutOfRowSerializer, values: VariableLengthFile
):
    before = (1, "uno", None, "dos")
    record = rows.pack(before)
    with pytest.raises(InvalidValueError):
        rows.repack(record, before, (1, "otro", "no es un número", None))
    assert rows.unpack(record) == before
    assert values.value_count == 2


def test_releasing_a_record_frees_every_text(
    rows: OutOfRowSerializer, values: VariableLengthFile
):
    kept = rows.pack((1, "se queda", None, None))
    released = rows.pack((2, LONG_TEXT, None, "también se va"))
    rows.release(released)
    assert values.value_count == 1
    assert rows.unpack(kept) == (1, "se queda", None, None)
