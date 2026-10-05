"""Sentencias que cambian el catálogo: tablas e índices que se crean y se borran.

Además de lo que hacen cuando todo va bien, importa lo que dejan cuando algo falla: ni una
entrada en el catálogo ni un archivo en disco.
"""

from pathlib import Path

import pytest

from config import EngineConfig
from query.catalog import (
    CatalogError,
    DuplicateIndexError,
    DuplicateTableError,
    UnknownIndexError,
    UnknownTableError,
)
from query.engine import Engine, EngineError
from query.operators import UnsupportedQueryError
from query.table import DuplicatePrimaryKeyError, DuplicateValueError
from storage.types import InvalidValueError, StorageError


@pytest.fixture
def tabla(engine: Engine) -> Engine:
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, n INT, s VARCHAR(8), p POINT)")
    engine.execute(
        "INSERT INTO t VALUES (1, 10, 'ana', POINT(-12.0, -77.0)), (2, 20, 'luis', NULL), "
        "(3, 10, NULL, POINT(-13.5, -72.0))"
    )
    return engine


def index_names(engine: Engine) -> list[str]:
    return sorted(index["name"] for index in engine.describe_table("t")["indexes"])


def access_path(engine: Engine, condition: str) -> str:
    plan = engine.execute(f"EXPLAIN SELECT id FROM t WHERE {condition}").plan
    while plan.children:
        plan = plan.children[0]
    return plan.operation


def index_files(config: EngineConfig) -> list[str]:
    return sorted(path.name for path in Path(config.data_directory).glob("t.*.idx"))


def test_only_one_statement_at_a_time(tabla: Engine):
    with pytest.raises(EngineError, match="se esperaba una sentencia y llegaron 2"):
        tabla.execute("SELECT id FROM t; SELECT id FROM t")


def test_creating_a_table_twice(tabla: Engine):
    with pytest.raises(DuplicateTableError, match="la tabla 't' ya existe"):
        tabla.execute("CREATE TABLE t (id INT)")
    assert "ya existía" in tabla.execute("CREATE TABLE IF NOT EXISTS t (id INT)").message
    assert tabla.execute("SELECT COUNT(*) FROM t").rows == ((3,),)


def test_dropping_a_table_that_does_not_exist(engine: Engine):
    with pytest.raises(UnknownTableError, match="la tabla 'nada' no existe"):
        engine.execute("DROP TABLE nada")
    assert "no existía" in engine.execute("DROP TABLE IF EXISTS nada").message


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM nada",
        "INSERT INTO nada VALUES (1)",
        "UPDATE nada SET a = 1",
        "DELETE FROM nada",
        "CREATE INDEX i ON nada USING BTREE (a)",
        "SELECT * FROM t JOIN nada ON t.id = nada.id",
    ],
)
def test_every_statement_rejects_an_unknown_table(tabla: Engine, statement: str):
    with pytest.raises(UnknownTableError, match="la tabla 'nada' no existe"):
        tabla.execute(statement)


def test_text_and_blob_columns_take_the_configured_length(config: EngineConfig):
    short = EngineConfig(
        page_size=config.page_size,
        text_length=40,
        blob_length=24,
        data_directory=config.data_directory,
    )
    with Engine(short) as engine:
        engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, cuerpo TEXT, adjunto BLOB)")
        schema = engine.table("notas").schema
        assert schema.field_of("cuerpo").length == short.text_length
        assert schema.field_of("adjunto").length == short.blob_length
        long_text = "x" * short.text_length
        engine.execute(f"INSERT INTO notas VALUES (1, '{long_text}', NULL)")
        assert engine.execute("SELECT cuerpo FROM notas").rows == ((long_text,),)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "CREATE TABLE ancha (id INT PRIMARY KEY, cuerpo TEXT, adjunto BLOB)",
            "no cabe en páginas",
        ),
        (
            "CREATE TABLE ancha (id INT PRIMARY KEY, clave VARCHAR(120) INDEX BTREE)",
            "solo caben",
        ),
        (
            "CREATE TABLE ancha (clave VARCHAR(120) PRIMARY KEY INDEX BTREE, n INT)",
            "solo caben",
        ),
    ],
)
def test_a_table_whose_files_cannot_be_created_is_not_registered(
    engine: Engine, config: EngineConfig, statement: str, message: str
):
    """Con páginas de 256 bytes, una fila de 500 o una clave de 120 no caben. La tabla no
    puede quedar en el catálogo: no se podría consultar, volver a crear ni borrar."""
    with pytest.raises(StorageError, match=message):
        engine.execute(statement)
    assert engine.table_names() == []
    assert not list(Path(config.data_directory).glob("ancha.*"))
    engine.execute("CREATE TABLE ancha (id INT PRIMARY KEY, n INT)")
    engine.execute("INSERT INTO ancha VALUES (1, 2)")
    assert engine.execute("SELECT * FROM ancha").rows == ((1, 2),)


def test_a_file_too_wide_for_a_page_is_not_loaded(engine: Engine, config: EngineConfig):
    path = Path(config.data_directory) / "ancho.csv"
    wide = ",".join("x" * 60 for _ in range(6))
    path.write_text(f"a,b,c,d,e,f\n{wide}\n", encoding="utf-8")
    with pytest.raises(StorageError, match="no cabe en páginas"):
        engine.execute(f'CREATE TABLE ancho FROM FILE "{path}"')
    assert engine.table_names() == []
    assert not list(Path(config.data_directory).glob("ancho.heap"))
    assert path.exists()


def test_a_column_declared_not_null_rejects_null(engine: Engine):
    engine.execute("CREATE TABLE c (id INT PRIMARY KEY, libre VARCHAR(4) NULL, fija INT NOT NULL)")
    engine.execute("INSERT INTO c VALUES (1, NULL, 5)")
    with pytest.raises(InvalidValueError, match="el campo 'fija' no admite NULL"):
        engine.execute("INSERT INTO c VALUES (2, 'a', NULL)")
    assert engine.execute("SELECT COUNT(*) FROM c").rows == ((1,),)


def test_an_index_gets_a_name_when_none_is_given(tabla: Engine):
    message = tabla.execute("CREATE INDEX ON t USING HASH (s)").message
    assert "idx_t_s" in message
    assert index_names(tabla) == ["idx_t_s", "pk_t"]
    assert access_path(tabla, "s = 'ana'") == "IndexLookup"


def test_an_index_name_or_an_indexed_column_cannot_be_repeated(tabla: Engine):
    tabla.execute("CREATE INDEX por_n ON t USING BTREE (n)")
    with pytest.raises(DuplicateIndexError, match="el índice 'por_n' ya existe"):
        tabla.execute("CREATE INDEX por_n ON t USING BTREE (s)")
    with pytest.raises(DuplicateIndexError, match="la columna 'n' ya tiene un índice"):
        tabla.execute("CREATE INDEX otro ON t USING HASH (n)")
    message = tabla.execute("CREATE INDEX IF NOT EXISTS otro ON t USING HASH (n)").message
    assert "ya tenía índice" in message
    assert index_names(tabla) == ["pk_t", "por_n"]


@pytest.mark.parametrize(
    ("statement", "error", "message"),
    [
        ("CREATE INDEX i ON t USING BTREE (n, s)", UnsupportedQueryError, "varias columnas"),
        ("CREATE INDEX i ON t USING BTREE (nada)", CatalogError, "la columna 'nada' no existe"),
        ("CREATE INDEX i ON t USING RTREE (n)", CatalogError, "necesita una columna POINT"),
        ("CREATE INDEX i ON t USING HNSW (p)", CatalogError, "no sirve como índice secundario"),
    ],
)
def test_an_index_that_cannot_be_built_leaves_nothing_behind(
    tabla: Engine, config: EngineConfig, statement: str, error: type[Exception], message: str
):
    files_before = index_files(config)
    with pytest.raises(error, match=message):
        tabla.execute(statement)
    assert index_names(tabla) == ["pk_t"]
    assert index_files(config) == files_before
    tabla.execute("CREATE INDEX i ON t USING BTREE (n)")
    assert access_path(tabla, "n = 10") == "IndexLookup"


def test_dropping_an_index(tabla: Engine, config: EngineConfig):
    tabla.execute("CREATE INDEX por_n ON t USING BTREE (n)")
    assert access_path(tabla, "n = 10") == "IndexLookup"
    files_with_index = index_files(config)
    assert "eliminado" in tabla.execute("DROP INDEX por_n").message
    assert access_path(tabla, "n = 10") == "SequentialScan"
    assert len(index_files(config)) == len(files_with_index) - 1
    assert sorted(tabla.execute("SELECT id FROM t WHERE n = 10").rows) == [(1,), (3,)]
    with pytest.raises(UnknownIndexError, match="el índice 'por_n' no existe"):
        tabla.execute("DROP INDEX por_n")
    assert "no existía" in tabla.execute("DROP INDEX IF EXISTS por_n").message


def test_a_table_keeps_working_without_the_index_of_its_key(tabla: Engine):
    """Sin el índice, buscar por clave primaria es recorrer el heap. Es más lento, pero la
    clave sigue sin poder repetirse."""
    tabla.execute("DROP INDEX pk_t")
    assert access_path(tabla, "id = 2") == "PrimaryKeyLookup"
    assert tabla.execute("SELECT s FROM t WHERE id = 2").rows == (("luis",),)
    with pytest.raises(DuplicatePrimaryKeyError):
        tabla.execute("INSERT INTO t VALUES (2, 0, 'dup', NULL)")
    with pytest.raises(DuplicatePrimaryKeyError):
        tabla.execute("UPDATE t SET id = 1 WHERE id = 2")
    tabla.execute("INSERT INTO t VALUES (4, 0, 'ok', NULL)")
    tabla.execute("UPDATE t SET id = 9 WHERE id = 4")
    tabla.execute("DELETE FROM t WHERE id = 1")
    assert tabla.execute("SELECT id FROM t ORDER BY id").rows == ((2,), (3,), (9,))


def test_a_unique_column_takes_several_nulls(engine: Engine):
    engine.execute("CREATE TABLE q (id INT PRIMARY KEY, c VARCHAR(8) UNIQUE)")
    engine.execute("INSERT INTO q VALUES (1, 'a'), (2, NULL), (3, NULL)")
    with pytest.raises(DuplicateValueError, match=r"el valor 'a' ya existe en 'q\.c'"):
        engine.execute("INSERT INTO q VALUES (4, 'a')")
    engine.execute("UPDATE q SET c = NULL WHERE id = 1")
    engine.execute("INSERT INTO q VALUES (4, 'a')")
    assert engine.execute("SELECT COUNT(*), COUNT(c) FROM q").rows == ((4, 1),)


def test_loading_a_file_into_an_existing_table_name(engine: Engine, config: EngineConfig):
    path = Path(config.data_directory) / "datos.csv"
    path.write_text("id,nombre\n1,ana\n2,luis\n", encoding="utf-8")
    engine.execute(f'CREATE TABLE gente FROM FILE "{path}"')
    with pytest.raises(DuplicateTableError):
        engine.execute(f'CREATE TABLE gente FROM FILE "{path}"')
    again = engine.execute(f'CREATE TABLE IF NOT EXISTS gente FROM FILE "{path}"')
    assert "ya existía" in again.message
    assert engine.execute("SELECT COUNT(*) FROM gente").rows == ((2,),)


def test_an_index_whose_keys_do_not_fit_a_node_is_not_created(engine: Engine, config: EngineConfig):
    """La tabla existe y tiene filas; lo que no cabe es la clave en un nodo del B+. El
    índice no puede quedar registrado a medias, y otro método sí sirve."""
    engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, texto VARCHAR(120))")
    engine.execute("INSERT INTO notas VALUES (1, 'uno'), (2, 'dos')")
    with pytest.raises(StorageError, match="solo caben"):
        engine.execute("CREATE INDEX por_texto ON notas USING BTREE (texto)")
    assert engine.table_of_index("por_texto") is None
    assert not list(Path(config.data_directory).glob("notas.por_texto*"))
    engine.execute("CREATE INDEX por_texto ON notas USING HASH (texto)")
    assert engine.execute("SELECT id FROM notas WHERE texto = 'dos'").rows == ((2,),)


@pytest.mark.parametrize(
    ("method", "column"), [("BTREE", "n"), ("HASH", "s"), ("RTREE", "p")]
)
def test_no_index_holds_the_rows_whose_value_is_null(tabla: Engine, method: str, column: str):
    tabla.execute("INSERT INTO t VALUES (9, NULL, NULL, NULL)")
    tabla.execute(f"CREATE INDEX i ON t USING {method} ({column})")
    table = tabla.table("t")
    assert list(table.search_index("i", None)) == []
    assert tabla.execute(f"SELECT id FROM t WHERE {column} IS NULL ORDER BY id").rows[-1] == (9,)
