"""Integridad de INSERT, UPDATE y DELETE: una sentencia que no puede completarse no deja
nada escrito, la clave primaria sigue siendo única y los índices siguen a las filas."""

import pytest

from config import EngineConfig
from query.catalog import CatalogError
from query.engine import Engine, EngineError
from query.expressions import ExpressionError, UnknownColumnError
from query.table import DuplicatePrimaryKeyError, DuplicateValueError
from sql.nodes import IndexType
from storage.types import StorageError

ORGANIZATIONS = {"heap": "", "secuencial": " INDEX SEQ", "agrupada": " INDEX BTREE"}


@pytest.fixture(params=list(ORGANIZATIONS))
def tabla(engine: Engine, request: pytest.FixtureRequest) -> Engine:
    """La misma tabla de tres filas, una vez por cada organización física."""
    engine.execute(f"CREATE TABLE t (id INT PRIMARY KEY{ORGANIZATIONS[request.param]}, v INT)")
    engine.execute("INSERT INTO t VALUES (1, 10), (2, 20), (3, 30)")
    return engine


def rows(engine: Engine, sql: str = "SELECT id, v FROM t") -> list[tuple]:
    return sorted(engine.execute(sql).rows)


def test_an_update_cannot_move_a_row_onto_an_existing_key(tabla: Engine):
    with pytest.raises(DuplicatePrimaryKeyError):
        tabla.execute("UPDATE t SET id = 2 WHERE id = 1")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]


def test_an_update_cannot_give_two_rows_the_same_key(tabla: Engine):
    with pytest.raises(DuplicatePrimaryKeyError):
        tabla.execute("UPDATE t SET id = 7")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]
    assert rows(tabla, "SELECT id, v FROM t WHERE id = 7") == []


def test_an_update_can_take_a_key_that_the_same_statement_frees(tabla: Engine):
    """Lo que cuenta es el resultado: tras `id + 1` las claves son 2, 3 y 4, todas distintas,
    aunque la 2 y la 3 estuvieran ocupadas al empezar."""
    tabla.execute("UPDATE t SET id = id + 1")
    assert rows(tabla) == [(2, 10), (3, 20), (4, 30)]
    assert rows(tabla, "SELECT id, v FROM t WHERE id = 3") == [(3, 20)]
    assert rows(tabla, "SELECT id, v FROM t WHERE id = 1") == []
    tabla.execute("UPDATE t SET id = id - 1")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]


def test_an_update_can_swap_two_keys(tabla: Engine):
    tabla.execute("UPDATE t SET id = 3 - id WHERE id IN (1, 2)")
    assert rows(tabla) == [(1, 20), (2, 10), (3, 30)]
    assert rows(tabla, "SELECT id, v FROM t WHERE id = 1") == [(1, 20)]


def test_a_moved_row_cannot_land_on_a_row_that_stays(tabla: Engine):
    with pytest.raises(DuplicatePrimaryKeyError):
        tabla.execute("UPDATE t SET id = id + 1 WHERE id <= 2")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]


def test_a_key_change_keeps_the_secondary_indexes_in_step(engine: Engine):
    engine.execute("CREATE TABLE c (id INT PRIMARY KEY, ciudad VARCHAR(8) INDEX HASH, n INT INDEX BTREE)")
    engine.execute("INSERT INTO c VALUES (1, 'lima', 5), (2, 'cusco', 6), (3, 'lima', 7)")
    engine.execute("UPDATE c SET id = id + 1, n = n * 10")
    assert rows(engine, "SELECT id FROM c WHERE ciudad = 'lima'") == [(2,), (4,)]
    assert rows(engine, "SELECT id FROM c WHERE n BETWEEN 55 AND 65") == [(3,)]
    assert rows(engine, "SELECT id FROM c WHERE n = 5") == []


def test_an_update_can_move_rows_to_free_keys(tabla: Engine):
    tabla.execute("UPDATE t SET id = id + 10")
    assert rows(tabla) == [(11, 10), (12, 20), (13, 30)]
    assert rows(tabla, "SELECT id, v FROM t WHERE id = 12") == [(12, 20)]
    assert rows(tabla, "SELECT id, v FROM t WHERE id = 2") == []
    tabla.execute("INSERT INTO t VALUES (2, 99)")
    assert rows(tabla, "SELECT id, v FROM t WHERE id = 2") == [(2, 99)]


def test_an_update_that_fails_on_one_row_changes_none(tabla: Engine):
    with pytest.raises(ExpressionError, match="división por cero"):
        tabla.execute("UPDATE t SET v = 100 / (id - 3)")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]


def test_an_update_with_a_value_the_column_cannot_hold_changes_none(tabla: Engine):
    with pytest.raises(StorageError):
        tabla.execute("UPDATE t SET v = 'texto' WHERE id >= 2")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]


def test_an_insert_with_a_repeated_key_inserts_nothing(tabla: Engine):
    with pytest.raises(DuplicatePrimaryKeyError, match="la clave primaria 7 ya existe"):
        tabla.execute("INSERT INTO t VALUES (7, 1), (8, 1), (7, 2)")
    with pytest.raises(DuplicatePrimaryKeyError, match="la clave primaria 2 ya existe"):
        tabla.execute("INSERT INTO t VALUES (9, 1), (2, 1)")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]


def test_an_insert_with_one_invalid_row_inserts_nothing(tabla: Engine):
    with pytest.raises(StorageError):
        tabla.execute("INSERT INTO t VALUES (7, 1), (8, 'texto')")
    assert rows(tabla) == [(1, 10), (2, 20), (3, 30)]


def test_values_cannot_name_columns(tabla: Engine):
    with pytest.raises(EngineError, match="VALUES solo admite valores constantes"):
        tabla.execute("INSERT INTO t VALUES (7, id)")


def test_delete_and_update_reject_an_unknown_column_even_on_an_empty_table(engine: Engine):
    engine.execute("CREATE TABLE vacia (id INT PRIMARY KEY, v INT)")
    with pytest.raises(UnknownColumnError):
        engine.execute("DELETE FROM vacia WHERE noexiste = 1")
    with pytest.raises(UnknownColumnError):
        engine.execute("UPDATE vacia SET v = 1 WHERE noexiste = 1")
    with pytest.raises(UnknownColumnError):
        engine.execute("UPDATE vacia SET v = noexiste + 1")


@pytest.mark.parametrize("method", ["BTREE", "HASH"])
def test_null_is_stored_but_not_indexed(engine: Engine, method: str):
    engine.execute(f"CREATE TABLE c (id INT PRIMARY KEY, ciudad VARCHAR(8) INDEX {method})")
    engine.execute("INSERT INTO c VALUES (1, 'lima'), (2, NULL), (3, 'cusco'), (4, NULL)")
    assert rows(engine, "SELECT id FROM c WHERE ciudad IS NULL") == [(2,), (4,)]
    by_city = engine.execute("SELECT id FROM c WHERE ciudad = 'lima'")
    assert by_city.rows == ((1,),)
    assert by_city.plan is not None and "IndexLookup" in by_city.plan.render()


@pytest.mark.parametrize("method", ["BTREE", "HASH"])
def test_an_indexed_column_can_change_to_null_and_back(engine: Engine, method: str):
    engine.execute(f"CREATE TABLE c (id INT PRIMARY KEY, ciudad VARCHAR(8) INDEX {method})")
    engine.execute("INSERT INTO c VALUES (1, 'lima'), (2, NULL)")
    engine.execute("UPDATE c SET ciudad = NULL WHERE id = 1")
    assert rows(engine, "SELECT id FROM c WHERE ciudad = 'lima'") == []
    engine.execute("UPDATE c SET ciudad = 'piura' WHERE id = 2")
    assert rows(engine, "SELECT id FROM c WHERE ciudad = 'piura'") == [(2,)]
    engine.execute("DELETE FROM c WHERE ciudad IS NULL")
    assert rows(engine, "SELECT id, ciudad FROM c") == [(2, "piura")]


def test_an_index_built_later_skips_the_rows_with_null(engine: Engine):
    engine.execute("CREATE TABLE c (id INT PRIMARY KEY, nota FLOAT)")
    engine.execute("INSERT INTO c VALUES (1, 4.5), (2, NULL), (3, 1.5)")
    engine.execute("CREATE INDEX idx_nota ON c USING BTREE (nota)")
    ranged = engine.execute("SELECT id FROM c WHERE nota BETWEEN 0 AND 10")
    assert sorted(ranged.rows) == [(1,), (3,)]
    assert ranged.plan is not None and "IndexRange" in ranged.plan.render()


def test_the_primary_key_gets_the_index_declared_on_it(engine: Engine):
    engine.execute("CREATE TABLE con_hash (id INT PRIMARY KEY INDEX HASH, v INT)")
    engine.execute("CREATE TABLE sin_metodo (id INT PRIMARY KEY, v INT)")
    assert [index.method for index in engine.table("con_hash").definition.indexes] == [
        IndexType.HASH
    ]
    assert [index.method for index in engine.table("sin_metodo").definition.indexes] == [
        IndexType.BTREE
    ]
    engine.execute("INSERT INTO con_hash VALUES (1, 10), (2, 20)")
    with pytest.raises(DuplicatePrimaryKeyError):
        engine.execute("INSERT INTO con_hash VALUES (2, 99)")
    assert rows(engine, "SELECT v FROM con_hash WHERE id = 2") == [(20,)]


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE mala (id INT PRIMARY KEY INDEX RTREE, v INT)",
        "CREATE TABLE mala (id INT PRIMARY KEY INDEX HNSW, v INT)",
        "CREATE TABLE mala (id INT PRIMARY KEY, v INT INDEX SEQ)",
        "CREATE TABLE mala (id INT PRIMARY KEY, v VARCHAR(8) INDEX INVERTED)",
    ],
)
def test_an_index_method_that_does_not_fit_leaves_no_table_behind(engine: Engine, ddl: str):
    with pytest.raises(CatalogError):
        engine.execute(ddl)
    assert "mala" not in engine.table_names()
    assert not list(engine.config.data_directory.glob("mala.*"))
    engine.execute("CREATE TABLE mala (id INT PRIMARY KEY, v INT)")


@pytest.fixture
def usuarios(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE usuarios (id INT PRIMARY KEY, correo VARCHAR(12) UNIQUE, alias VARCHAR(8))"
    )
    engine.execute("INSERT INTO usuarios VALUES (1, 'ana@x', 'ana'), (2, 'luis@x', 'lu'), (3, NULL, 'eva')")
    return engine


def test_a_unique_column_gets_an_index_that_is_used(usuarios: Engine):
    definition = usuarios.table("usuarios").definition.index_on("correo")
    assert definition is not None and definition.unique and definition.method is IndexType.HASH
    result = usuarios.execute("SELECT id FROM usuarios WHERE correo = 'luis@x'")
    assert result.rows == ((2,),)
    assert result.plan is not None and "IndexLookup" in result.plan.render()


def test_a_unique_column_rejects_a_repeated_value(usuarios: Engine):
    with pytest.raises(DuplicateValueError, match="'ana@x' ya existe en 'usuarios"):
        usuarios.execute("INSERT INTO usuarios VALUES (4, 'ana@x', 'otra')")
    with pytest.raises(DuplicateValueError):
        usuarios.execute("INSERT INTO usuarios VALUES (5, 'nuevo@x', 'a'), (6, 'nuevo@x', 'b')")
    with pytest.raises(DuplicateValueError):
        usuarios.execute("UPDATE usuarios SET correo = 'ana@x' WHERE id = 2")
    with pytest.raises(DuplicateValueError):
        usuarios.execute("UPDATE usuarios SET correo = 'todos@x'")
    assert rows(usuarios, "SELECT id, correo FROM usuarios") == [
        (1, "ana@x"),
        (2, "luis@x"),
        (3, None),
    ]


def test_null_does_not_count_as_a_repeated_value(usuarios: Engine):
    usuarios.execute("INSERT INTO usuarios VALUES (4, NULL, 'sin')")
    usuarios.execute("UPDATE usuarios SET correo = NULL WHERE id = 1")
    assert rows(usuarios, "SELECT id FROM usuarios WHERE correo IS NULL") == [(1,), (3,), (4,)]


def test_unique_values_can_change_hands_within_one_statement(usuarios: Engine):
    """Como con la clave primaria, cuenta el resultado: dos filas pueden intercambiar sus
    valores únicos, y un valor que queda libre se puede volver a usar."""
    usuarios.execute("UPDATE usuarios SET correo = alias WHERE id <= 2")
    assert rows(usuarios, "SELECT id, correo FROM usuarios WHERE id <= 2") == [(1, "ana"), (2, "lu")]
    usuarios.execute("INSERT INTO usuarios VALUES (9, 'ana@x', 'nueva')")
    usuarios.execute("DELETE FROM usuarios WHERE id = 9")
    usuarios.execute("INSERT INTO usuarios VALUES (10, 'ana@x', 'otra')")
    assert rows(usuarios, "SELECT id FROM usuarios WHERE correo = 'ana@x'") == [(10,)]


def test_unique_survives_reopening(config: EngineConfig):
    with Engine(config) as engine:
        engine.execute("CREATE TABLE u (id INT PRIMARY KEY, correo VARCHAR(12) UNIQUE INDEX BTREE)")
        engine.execute("INSERT INTO u VALUES (1, 'ana@x')")
    with Engine(config) as engine:
        index = engine.table("u").definition.index_on("correo")
        assert index is not None and index.unique and index.method is IndexType.BTREE
        with pytest.raises(DuplicateValueError):
            engine.execute("INSERT INTO u VALUES (2, 'ana@x')")


@pytest.mark.parametrize("organization", ["SEQ", "BTREE"])
def test_unique_needs_a_heap_table_like_any_secondary_index(engine: Engine, organization: str):
    with pytest.raises(CatalogError, match="heap"):
        engine.execute(
            f"CREATE TABLE u (id INT PRIMARY KEY INDEX {organization}, correo VARCHAR(12) UNIQUE)"
        )
    assert engine.table_names() == []
