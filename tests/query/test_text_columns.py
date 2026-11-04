"""Columnas TEXT: el texto vive fuera de la fila y la consulta no lo nota.

Cada escritura deja en el archivo de valores exactamente un valor por TEXT no nulo de la
tabla: ni huérfanos tras un borrado, una actualización o una sentencia que falla, ni
reescrituras cuando el texto no cambia.
"""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

import query.index_methods as index_methods
from config import EngineConfig
from index.hash import HashSecondaryIndex
from query.catalog import CatalogError
from query.engine import Engine
from sql.nodes import IndexType
from storage.record import FieldReader
from storage.types import FieldType, StorageError
from storage.varlen import VariableLengthFile

# Con las páginas de 256 bytes de los tests, cada uno es una cadena de unos 85 fragmentos.
LONG_TEXT = "x" * 20_000
ORGANIZATIONS = {
    "heap": "id INT PRIMARY KEY",
    "heap sin clave": "id INT",
    "secuencial": "id INT PRIMARY KEY INDEX SEQ",
    "b+ agrupado": "id INT PRIMARY KEY INDEX BTREE",
}
KEYED_ORGANIZATIONS = [name for name, key in ORGANIZATIONS.items() if "PRIMARY KEY" in key]
ROWS = ((1, "largo", LONG_TEXT), (2, "corto", "un texto corto"), (3, "nulo", None))
# Con un buffer de una página caben unas 15 filas: 80 obligan a ordenar y reunir en disco.
SPILLED_ROWS = 80
TEXT_STATEMENTS = [
    "SELECT * FROM {t} WHERE cuerpo LIKE '%nota 1%' ORDER BY id",
    "SELECT * FROM {t} WHERE cuerpo = 'nota 7 del grupo b' ORDER BY id",
    "SELECT * FROM {t} WHERE cuerpo BETWEEN 'nota 2' AND 'nota 4' ORDER BY id",
    "SELECT * FROM {t} WHERE cuerpo IN ('nota 3 del grupo a', 'nada') ORDER BY id",
    "SELECT * FROM {t} WHERE cuerpo IS NULL ORDER BY id",
    "SELECT id, cuerpo FROM {t} ORDER BY cuerpo DESC, id",
    "SELECT cuerpo, COUNT(*) FROM {t} GROUP BY cuerpo ORDER BY cuerpo",
    "SELECT id, cuerpo, COUNT(*) FROM {t} GROUP BY cuerpo, id ORDER BY id",
    "SELECT grupo, MIN(cuerpo), MAX(cuerpo) FROM {t} GROUP BY grupo ORDER BY grupo",
    "SELECT DISTINCT cuerpo FROM {t} ORDER BY cuerpo",
    "SELECT a.id, b.id FROM {t} a JOIN {t} b ON a.cuerpo = b.cuerpo ORDER BY a.id, b.id",
    "SELECT a.id, b.id FROM {t} a JOIN {t} b ON a.cuerpo < b.cuerpo AND b.id < 5 "
    "ORDER BY a.id, b.id",
]


def stored_values(engine: Engine, table: str) -> int:
    values = engine.table(table).describe_structure()["text_values"]
    return int(values["values"])


def literal(value: str | None) -> str:
    return "NULL" if value is None else f"'{value}'"


def insert(engine: Engine, table: str, rows: tuple) -> None:
    values = ", ".join(f"({key}, '{subject}', {literal(body)})" for key, subject, body in rows)
    engine.execute(f"INSERT INTO {table} VALUES {values}")


@pytest.fixture(params=list(ORGANIZATIONS))
def notas(engine: Engine, request: pytest.FixtureRequest) -> Engine:
    key = ORGANIZATIONS[request.param]
    engine.execute(f"CREATE TABLE notas ({key}, asunto VARCHAR(8), cuerpo TEXT)")
    insert(engine, "notas", ROWS)
    return engine


@pytest.fixture(params=KEYED_ORGANIZATIONS)
def keyed_notas(engine: Engine, request: pytest.FixtureRequest) -> Engine:
    key = ORGANIZATIONS[request.param]
    engine.execute(f"CREATE TABLE notas ({key}, asunto VARCHAR(8), cuerpo TEXT)")
    insert(engine, "notas", ROWS)
    return engine


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Cada `put` y `replace` que llega a un archivo de valores."""
    seen: list[str] = []
    original_put = VariableLengthFile.put
    original_replace = VariableLengthFile.replace

    def put(self: VariableLengthFile, value: bytes):
        seen.append("put")
        return original_put(self, value)

    def replace(self: VariableLengthFile, address, value: bytes):
        seen.append("replace")
        return original_replace(self, address, value)

    monkeypatch.setattr(VariableLengthFile, "put", put)
    monkeypatch.setattr(VariableLengthFile, "replace", replace)
    yield seen


def test_texts_of_any_length_come_back_whole_in_every_organization(notas: Engine):
    assert notas.execute("SELECT * FROM notas ORDER BY id").rows == ROWS
    assert notas.table("notas").schema.field_of("cuerpo").type is FieldType.TEXT
    assert stored_values(notas, "notas") == 2


def test_deleting_a_row_frees_its_text(notas: Engine):
    notas.execute("DELETE FROM notas WHERE id = 1")
    assert stored_values(notas, "notas") == 1
    notas.execute("DELETE FROM notas")
    assert stored_values(notas, "notas") == 0


@pytest.mark.parametrize(
    ("assignment", "expected", "values"),
    [
        ("cuerpo = 'otro texto'", "otro texto", 2),
        (f"cuerpo = '{LONG_TEXT}y'", f"{LONG_TEXT}y", 2),
        ("cuerpo = NULL", None, 1),
    ],
)
def test_updating_a_text_replaces_its_value(
    notas: Engine, assignment: str, expected: str | None, values: int
):
    notas.execute(f"UPDATE notas SET {assignment} WHERE id = 2")
    assert notas.execute("SELECT cuerpo FROM notas WHERE id = 2").rows == ((expected,),)
    assert stored_values(notas, "notas") == values
    notas.execute("UPDATE notas SET cuerpo = 'aparece' WHERE id = 3")
    assert stored_values(notas, "notas") == values + 1


def test_an_update_that_does_not_change_the_text_does_not_rewrite_it(
    notas: Engine, writes: list[str]
):
    notas.execute("UPDATE notas SET asunto = 'otro' WHERE id < 3")
    notas.execute(f"UPDATE notas SET cuerpo = '{LONG_TEXT}' WHERE id = 1")
    assert writes == []
    assert notas.execute("SELECT asunto, cuerpo FROM notas WHERE id = 1").rows == (
        ("otro", LONG_TEXT),
    )


def test_changing_the_key_moves_the_rows_with_their_texts(keyed_notas: Engine):
    keyed_notas.execute("UPDATE notas SET id = id + 1")
    expected = tuple((key + 1, subject, body) for key, subject, body in ROWS)
    assert keyed_notas.execute("SELECT * FROM notas ORDER BY id").rows == expected
    assert stored_values(keyed_notas, "notas") == 2


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO notas VALUES (7, 'nueva', 'texto nuevo'), (1, 'repetida', 'otro')",
        "INSERT INTO notas VALUES (7, 'nueva', 'texto nuevo'), (8, 'mala', 42)",
        "UPDATE notas SET cuerpo = 'cambio', id = 2 WHERE id = 1",
        "UPDATE notas SET cuerpo = 'cambio', asunto = 'demasiado largo' WHERE id = 1",
    ],
)
def test_a_failed_statement_leaves_no_text_behind(keyed_notas: Engine, statement: str):
    with pytest.raises(StorageError):
        keyed_notas.execute(statement)
    assert keyed_notas.execute("SELECT * FROM notas ORDER BY id").rows == ROWS
    assert stored_values(keyed_notas, "notas") == 2


def test_texts_survive_closing_and_reopening_the_engine(config: EngineConfig):
    with Engine(config) as engine:
        engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, asunto VARCHAR(8), cuerpo TEXT)")
        insert(engine, "notas", ROWS)
    with Engine(config) as engine:
        assert engine.execute("SELECT * FROM notas ORDER BY id").rows == ROWS
        engine.execute("DELETE FROM notas WHERE id = 1")
    with Engine(config) as engine:
        assert stored_values(engine, "notas") == 1


def test_dropping_the_table_removes_its_value_file(notas: Engine, config: EngineConfig):
    assert (config.data_directory / "notas.text").exists()
    notas.execute("DROP TABLE notas")
    assert not list(config.data_directory.glob("notas.*"))


def test_a_table_without_text_has_no_value_file(engine: Engine, config: EngineConfig):
    engine.execute("CREATE TABLE fichas (id INT PRIMARY KEY, nombre VARCHAR(8))")
    assert not (config.data_directory / "fichas.text").exists()
    assert engine.table("fichas").describe_structure()["text_values"] is None


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("CREATE INDEX ON notas USING BTREE (cuerpo)", "vive fuera de la fila"),
        ("CREATE INDEX ON notas USING HASH (cuerpo)", "vive fuera de la fila"),
        ("CREATE INDEX ON notas USING RTREE (cuerpo)", "necesita una columna POINT"),
        ("CREATE TABLE otra (id INT, cuerpo TEXT UNIQUE)", "vive fuera de la fila"),
        ("CREATE TABLE otra (cuerpo TEXT PRIMARY KEY)", "no puede ser clave primaria"),
    ],
)
def test_structures_that_store_the_value_in_a_key_refuse_a_text_column(
    engine: Engine, statement: str, message: str
):
    engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, cuerpo TEXT)")
    with pytest.raises(CatalogError, match=message):
        engine.execute(statement)


def test_an_index_reads_the_text_of_a_row_not_its_address(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    """Lo que necesitará el índice invertido: la tabla le da a cada índice un lector de
    campos que resuelve los TEXT."""
    readers: list[FieldReader] = []

    def opener(
        path: Path, reader: FieldReader, column: str, config: EngineConfig
    ) -> HashSecondaryIndex:
        readers.append(reader)
        return HashSecondaryIndex(path, reader, column, config)

    hash_method = replace(index_methods._METHODS[IndexType.HASH], opener=opener)
    monkeypatch.setitem(index_methods._METHODS, IndexType.HASH, hash_method)
    engine.execute("CREATE TABLE notas (id INT, asunto VARCHAR(8), cuerpo TEXT)")
    insert(engine, "notas", ROWS)
    engine.execute("CREATE INDEX ON notas USING HASH (asunto)")
    records = [record for _, record in engine.table("notas").addressed_scan()]
    assert [readers[-1].unpack_field(record, 2) for record in records] == [
        body for _, _, body in ROWS
    ]


@pytest.fixture
def spilling_engine(config: EngineConfig) -> Iterator[Engine]:
    """Un buffer de una página: ordenar, agrupar, quitar repetidos y reunir van a disco."""
    tight = EngineConfig(
        page_size=config.page_size,
        buffer_pool_pages=config.buffer_pool_pages,
        sort_buffer_pages=1,
        merge_fan_in=2,
        data_directory=config.data_directory,
    )
    with Engine(tight) as engine:
        for table, kind in (("con_texto", "TEXT"), ("con_varchar", "VARCHAR(40)")):
            engine.execute(
                f"CREATE TABLE {table} (id INT PRIMARY KEY, grupo VARCHAR(2), cuerpo {kind})"
            )
            insert(engine, table, tuple(spilled_row(number) for number in range(SPILLED_ROWS)))
        yield engine


def spilled_row(number: int) -> tuple[int, str, str | None]:
    """Textos repetidos, para que agrupar y quitar repetidos tengan qué juntar, y algún NULL."""
    group = "ab"[number % 2]
    body = None if number % 9 == 0 else f"nota {number % 13} del grupo {group}"
    return number, group, body


@pytest.mark.parametrize("template", TEXT_STATEMENTS)
def test_a_text_column_answers_like_a_varchar_one_even_on_disk(
    spilling_engine: Engine, config: EngineConfig, template: str
):
    with_text = spilling_engine.execute(template.format(t="con_texto")).rows
    assert with_text == spilling_engine.execute(template.format(t="con_varchar")).rows
    assert with_text
    leftovers = [path.name for path in Path(config.data_directory).iterdir() if path.is_dir()]
    assert leftovers == []


def test_a_text_column_joins_with_a_varchar_one(spilling_engine: Engine):
    joined = spilling_engine.execute(
        "SELECT t.id, v.id FROM con_texto t JOIN con_varchar v ON t.cuerpo = v.cuerpo "
        "WHERE t.id = v.id ORDER BY t.id"
    ).rows
    assert joined == tuple(
        (number, number) for number in range(SPILLED_ROWS) if spilled_row(number)[2] is not None
    )


def test_an_index_on_another_column_answers_the_same(spilling_engine: Engine):
    query = "SELECT * FROM con_texto WHERE grupo = 'a' ORDER BY id"
    without_index = spilling_engine.execute(query).rows
    spilling_engine.execute("CREATE INDEX por_grupo ON con_texto USING HASH (grupo)")
    assert "por_grupo" in str(spilling_engine.execute(f"EXPLAIN {query}").plan)
    assert spilling_engine.execute(query).rows == without_index


def test_a_csv_with_20_kb_texts_loads_them_whole(engine: Engine, config: EngineConfig):
    bodies = [LONG_TEXT, "ñandú " * 3_500, "breve"]
    path = config.data_directory / "documentos.csv"
    lines = ["id,titulo,cuerpo", *(f"{key},doc {key},{body}" for key, body in enumerate(bodies))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    engine.execute(f"CREATE TABLE documentos FROM FILE '{path}' USING INDEX BTREE(id)")
    schema = engine.table("documentos").schema
    assert schema.field_of("titulo").type is FieldType.STRING
    assert schema.field_of("cuerpo").type is FieldType.TEXT
    rows = engine.execute("SELECT id, cuerpo FROM documentos ORDER BY id").rows
    assert rows == tuple((key, body.strip()) for key, body in enumerate(bodies))
