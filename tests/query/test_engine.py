import pytest

from config import EngineConfig
from query.catalog import UnknownTableError
from query.engine import Engine, EngineError, QueryResult, TransactionStatementError
from query.table import DuplicatePrimaryKeyError


def keys(result: QueryResult) -> list:
    return [row[0] for row in result.rows]


def test_create_table_registers_it(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(8))")
    assert engine.table_names() == ["t"]


def test_create_table_if_not_exists_is_idempotent(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    result = engine.execute("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY)")
    assert "ya existía" in result.message


def test_unknown_table_is_rejected(engine: Engine):
    with pytest.raises(UnknownTableError):
        engine.execute("SELECT * FROM fantasma")


def test_insert_and_select_all(alumnos: Engine):
    result = alumnos.execute("SELECT * FROM alumnos")
    assert result.columns == ("id", "nombre", "ciudad", "nota")
    assert len(result.rows) == 30


def test_insert_with_column_list(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, a VARCHAR(4), b INT)")
    engine.execute("INSERT INTO t (b, id) VALUES (9, 1)")
    assert engine.execute("SELECT * FROM t").rows == ((1, None, 9),)


def test_insert_with_wrong_arity_is_rejected(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    with pytest.raises(EngineError):
        engine.execute("INSERT INTO t VALUES (1)")


def test_duplicate_primary_key_is_rejected(alumnos: Engine):
    with pytest.raises(DuplicatePrimaryKeyError):
        alumnos.execute("INSERT INTO alumnos VALUES (1, 'x', 'lima', 1.0)")


def test_where_equality(alumnos: Engine):
    assert keys(alumnos.execute("SELECT id FROM alumnos WHERE id = 7")) == [7]


def test_where_range(alumnos: Engine):
    assert keys(alumnos.execute("SELECT id FROM alumnos WHERE id BETWEEN 5 AND 8")) == [5, 6, 7, 8]


def test_where_with_and_or(alumnos: Engine):
    result = alumnos.execute(
        "SELECT id FROM alumnos WHERE ciudad = 'lima' AND (id > 20 OR id < 3)"
    )
    assert sorted(keys(result)) == [0, 21, 24, 27]


def test_where_like(alumnos: Engine):
    assert len(alumnos.execute("SELECT id FROM alumnos WHERE nombre LIKE 'n1%'").rows) == 11


def test_where_in_and_not_in(alumnos: Engine):
    assert sorted(keys(alumnos.execute("SELECT id FROM alumnos WHERE id IN (1, 3, 5)"))) == [1, 3, 5]
    assert len(alumnos.execute("SELECT id FROM alumnos WHERE id NOT IN (1, 3)").rows) == 28


def test_is_null(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(4))")
    engine.execute("INSERT INTO t (id) VALUES (1)")
    engine.execute("INSERT INTO t VALUES (2, 'x')")
    assert keys(engine.execute("SELECT id FROM t WHERE v IS NULL")) == [1]
    assert keys(engine.execute("SELECT id FROM t WHERE v IS NOT NULL")) == [2]


def test_order_by_ascending_and_descending(alumnos: Engine):
    ascending = keys(alumnos.execute("SELECT id FROM alumnos ORDER BY id"))
    descending = keys(alumnos.execute("SELECT id FROM alumnos ORDER BY id DESC"))
    assert ascending == list(range(30))
    assert descending == list(reversed(range(30)))


def test_order_by_two_columns(alumnos: Engine):
    result = alumnos.execute("SELECT ciudad, id FROM alumnos ORDER BY ciudad, id DESC")
    assert result.rows[0] == ("cusco", 28)


def test_limit_and_offset(alumnos: Engine):
    assert keys(alumnos.execute("SELECT id FROM alumnos ORDER BY id LIMIT 3 OFFSET 2")) == [2, 3, 4]


def test_distinct(alumnos: Engine):
    result = alumnos.execute("SELECT DISTINCT ciudad FROM alumnos")
    assert sorted(row[0] for row in result.rows) == ["cusco", "lima", "piura"]


def test_group_by_with_aggregates(alumnos: Engine):
    result = alumnos.execute(
        "SELECT ciudad, COUNT(*) as total, MIN(id) as menor, MAX(id) as mayor "
        "FROM alumnos GROUP BY ciudad ORDER BY ciudad"
    )
    assert result.columns == ("ciudad", "total", "menor", "mayor")
    assert result.rows[0] == ("cusco", 10, 1, 28)


def test_aggregate_without_group_by(alumnos: Engine):
    result = alumnos.execute("SELECT COUNT(*) as n, SUM(id) as suma FROM alumnos")
    assert result.rows == ((30, 435),)


def test_average(alumnos: Engine):
    assert alumnos.execute("SELECT AVG(id) as media FROM alumnos").rows == ((14.5,),)


def test_having_filters_groups(alumnos: Engine):
    result = alumnos.execute(
        "SELECT ciudad, COUNT(*) as total FROM alumnos GROUP BY ciudad HAVING COUNT(*) > 100"
    )
    assert result.rows == ()


def test_join(engine: Engine):
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY, v VARCHAR(4))")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY, w VARCHAR(4))")
    for number in range(6):
        engine.execute(f"INSERT INTO a VALUES ({number}, 'a{number}')")
    for number in range(0, 6, 2):
        engine.execute(f"INSERT INTO b VALUES ({number}, 'b{number}')")
    result = engine.execute("SELECT a.v, b.w FROM a JOIN b ON a.id = b.id ORDER BY a.v")
    assert result.rows == (("a0", "b0"), ("a2", "b2"), ("a4", "b4"))


def test_update(alumnos: Engine):
    alumnos.execute("UPDATE alumnos SET nota = 20.0 WHERE id = 3")
    assert alumnos.execute("SELECT nota FROM alumnos WHERE id = 3").rows == ((20.0,),)


def test_update_uses_the_old_row(alumnos: Engine):
    alumnos.execute("UPDATE alumnos SET nota = nota + 1 WHERE id = 4")
    assert alumnos.execute("SELECT nota FROM alumnos WHERE id = 4").rows == ((3.0,),)


def test_delete(alumnos: Engine):
    result = alumnos.execute("DELETE FROM alumnos WHERE ciudad = 'piura'")
    assert result.affected_rows == 10
    assert alumnos.execute("SELECT COUNT(*) FROM alumnos").rows == ((20,),)


def test_delete_without_where_empties_the_table(alumnos: Engine):
    alumnos.execute("DELETE FROM alumnos")
    assert alumnos.execute("SELECT COUNT(*) FROM alumnos").rows == ((0,),)


def test_secondary_index_is_created_and_used(alumnos: Engine):
    alumnos.execute("CREATE INDEX idx_nota ON alumnos USING BTREE (nota)")
    plan = alumnos.execute("SELECT id FROM alumnos WHERE nota = 3.0").plan
    assert plan is not None
    assert "idx_nota" in plan.render()


def test_dropping_an_index_returns_to_the_scan(alumnos: Engine):
    alumnos.execute("CREATE INDEX idx_nota ON alumnos USING BTREE (nota)")
    alumnos.execute("DROP INDEX idx_nota")
    plan = alumnos.execute("SELECT id FROM alumnos WHERE nota = 3.0").plan
    assert plan is not None
    assert "SequentialScan" in plan.render()


def test_drop_table(alumnos: Engine):
    alumnos.execute("DROP TABLE alumnos")
    assert alumnos.table_names() == []


def test_transaction_statements_belong_to_the_session(engine: Engine):
    with pytest.raises(TransactionStatementError):
        engine.execute("BEGIN TRANSACTION")


def test_catalog_survives_reopening(config: EngineConfig):
    with Engine(config) as engine:
        engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(8))")
        engine.execute("INSERT INTO t VALUES (1, 'uno')")
    with Engine(config) as engine:
        assert engine.table_names() == ["t"]
        assert engine.execute("SELECT * FROM t").rows == ((1, "uno"),)


def test_sequential_and_clustered_tables(engine: Engine):
    engine.execute("CREATE TABLE s (id INT PRIMARY KEY INDEX SEQ, v VARCHAR(8))")
    engine.execute("CREATE TABLE c (id INT PRIMARY KEY INDEX BTREE, v VARCHAR(8))")
    for number in reversed(range(20)):
        engine.execute(f"INSERT INTO s VALUES ({number}, 's{number}')")
        engine.execute(f"INSERT INTO c VALUES ({number}, 'c{number}')")
    assert keys(engine.execute("SELECT id FROM s")) == list(range(20))
    assert keys(engine.execute("SELECT id FROM c")) == list(range(20))
    assert keys(engine.execute("SELECT id FROM s WHERE id BETWEEN 3 AND 5")) == [3, 4, 5]


def test_create_table_from_csv(engine: Engine, config: EngineConfig):
    path = config.data_directory / "datos.csv"
    path.write_text("id,nombre,nota\n1,ana,4.5\n2,luis,3.25\n", encoding="utf-8")
    result = engine.execute(f"CREATE TABLE cargada FROM FILE '{path}' USING INDEX BTREE(\"id\")")
    assert result.affected_rows == 2
    assert engine.execute("SELECT * FROM cargada ORDER BY id").rows == (
        (1, "ana", 4.5),
        (2, "luis", 3.25),
    )
