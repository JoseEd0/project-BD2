"""El archivo de valores temporal de un operador: solo existe mientras hace falta."""

from pathlib import Path

from config import EngineConfig
from query.spill import SPILLED_VALUES_FILENAME, SpilledValues, spill_serializer
from storage.schema import Field, FieldType, Schema

SCHEMA = Schema([Field("id", FieldType.INT), Field("cuerpo", FieldType.TEXT)])


def test_rows_without_text_create_no_file(config: EngineConfig):
    directory = Path(config.data_directory) / "sort-a"
    plain = Schema([Field("id", FieldType.INT)])
    with SpilledValues(directory, config) as values:
        rows = spill_serializer(plain, values)
        assert rows.unpack(rows.pack((1,))) == (1,)
        assert not directory.exists()


def test_the_file_appears_with_the_first_text_and_goes_away_on_close(config: EngineConfig):
    directory = Path(config.data_directory) / "sort-b"
    with SpilledValues(directory, config) as values:
        rows = spill_serializer(SCHEMA, values)
        record = rows.pack((1, "texto " * 500))
        assert (directory / SPILLED_VALUES_FILENAME).exists()
        assert rows.unpack(record) == (1, "texto " * 500)
    assert not directory.exists()


def test_closing_keeps_a_directory_that_still_holds_other_files(config: EngineConfig):
    directory = Path(config.data_directory) / "sort-c"
    with SpilledValues(directory, config) as values:
        spill_serializer(SCHEMA, values).pack((1, "texto"))
        (directory / "run0.tmp").write_bytes(b"")
    assert sorted(path.name for path in directory.iterdir()) == ["run0.tmp"]
