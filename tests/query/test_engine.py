import pytest

from config import EngineConfig
from query.catalog import CatalogError, UnknownTableError
from query.engine import Engine, EngineError, QueryResult, TransactionStatementError
from query.expressions import ExpressionError, UnknownColumnError
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


def test_chained_joins_do_not_clobber_each_other(engine: Engine):
    """Dos JOIN en el mismo plan escriben particiones a la vez: cada uno necesita su sitio."""
    engine.execute("CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(10))")
    engine.execute("CREATE TABLE cursos (id INT PRIMARY KEY, nombre VARCHAR(10))")
    engine.execute("CREATE TABLE matriculas (id INT PRIMARY KEY, alumno_id INT, curso_id INT)")
    engine.execute("INSERT INTO alumnos VALUES (1, 'ana'), (2, 'luis')")
    engine.execute("INSERT INTO cursos VALUES (10, 'bd2'), (11, 'algo')")
    engine.execute("INSERT INTO matriculas VALUES (100, 1, 10), (101, 2, 11)")
    result = engine.execute(
        "SELECT a.nombre, c.nombre FROM matriculas AS m "
        "JOIN alumnos AS a ON m.alumno_id = a.id "
        "JOIN cursos AS c ON m.curso_id = c.id "
        "ORDER BY a.nombre"
    )
    assert result.rows == (("ana", "bd2"), ("luis", "algo"))


def test_a_join_combined_with_grouping_and_sorting(engine: Engine):
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY, ciudad VARCHAR(10))")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY, a_id INT)")
    engine.execute("INSERT INTO a VALUES (1, 'lima'), (2, 'lima'), (3, 'cusco')")
    engine.execute("INSERT INTO b VALUES (10, 1), (11, 1), (12, 3)")
    result = engine.execute(
        "SELECT a.ciudad, COUNT(*) AS total FROM b JOIN a ON b.a_id = a.id "
        "GROUP BY a.ciudad ORDER BY total DESC"
    )
    assert result.rows == (("lima", 2), ("cusco", 1))


def test_an_unknown_column_is_rejected_even_on_an_empty_table(engine: Engine):
    """La validación va antes de leer filas: sin datos, el error debe salir igual."""
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(8))")
    for query in (
        "SELECT falsa FROM t",
        "SELECT * FROM t WHERE falsa = 1",
        "SELECT * FROM t ORDER BY falsa",
        "SELECT SUM(falsa) FROM t",
        "SELECT * FROM t WHERE (id + falsa) > 1",
    ):
        with pytest.raises(UnknownColumnError):
            engine.execute(query)


def test_an_unknown_function_is_rejected_before_reading_rows(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    with pytest.raises(ExpressionError):
        engine.execute("SELECT RAIZ(id) FROM t")


def test_a_valid_query_on_an_empty_table_returns_no_rows(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(8))")
    assert engine.execute("SELECT id, v FROM t WHERE id > 0 ORDER BY v").rows == ()


def test_drop_index_if_exists_tolerates_a_missing_index(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    assert "no existía" in engine.execute("DROP INDEX IF EXISTS fantasma").message


def test_drop_index_if_exists_drops_an_existing_index(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    engine.execute("CREATE INDEX idx_v ON t USING HASH (v)")
    engine.execute("DROP INDEX IF EXISTS idx_v")
    assert engine.table("t").definition.index_on("v") is None


def test_loading_a_file_with_an_unknown_key_registers_nothing(engine: Engine, tmp_path):
    source = tmp_path / "p.csv"
    source.write_text("id,nombre\n1,a\n")
    with pytest.raises(CatalogError):
        engine.execute(f"CREATE TABLE p FROM FILE '{source}' USING INDEX SEQ(\"falsa\")")
    assert engine.table_names() == []


def test_a_load_that_fails_halfway_leaves_no_table_behind(engine: Engine, tmp_path):
    """Una clave repetida a mitad del archivo no debe dejar una tabla a medio cargar."""
    source = tmp_path / "p.csv"
    source.write_text("id,nombre\n1,a\n2,b\n1,c\n")
    with pytest.raises(DuplicatePrimaryKeyError):
        engine.execute(f"CREATE TABLE p FROM FILE '{source}' USING INDEX BTREE(\"id\")")
    assert engine.table_names() == []
    engine.execute(f"CREATE TABLE p FROM FILE '{source}'")
    assert len(engine.execute("SELECT * FROM p").rows) == 3


def test_dropping_a_table_leaves_foreign_files_alone(engine: Engine, tmp_path):
    """`productos.csv` empieza igual que los archivos de la tabla, pero no es suyo."""
    engine.execute("CREATE TABLE productos (id INT PRIMARY KEY, v INT INDEX HASH)")
    foreign = tmp_path / "productos.csv"
    foreign.write_text("id\n1\n")
    engine.execute("DROP TABLE productos")
    assert foreign.exists()
    assert not list(tmp_path.glob("productos.*.idx*"))
    assert not (tmp_path / "productos.heap").exists()


def test_a_secondary_index_on_an_ordered_table_is_rejected_cleanly(engine: Engine):
    """Fallar al crear el índice no puede dejarlo apuntado en el catálogo."""
    engine.execute("CREATE TABLE p (id INT PRIMARY KEY INDEX SEQ, estado VARCHAR(10))")
    engine.execute("INSERT INTO p VALUES (1, 'a'), (2, 'b')")
    with pytest.raises(CatalogError, match="heap file"):
        engine.execute("CREATE INDEX idx_estado ON p USING HASH (estado)")
    assert engine.table("p").definition.indexes == ()
    engine.close()
    with Engine(engine.config) as reopened:
        assert reopened.table("p").definition.indexes == ()
        assert reopened.execute("SELECT id FROM p WHERE estado = 'a'").rows == ((1,),)


def test_dropping_a_hash_index_removes_its_directory_file(engine: Engine, tmp_path):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    engine.execute("CREATE INDEX idx_v ON t USING HASH (v)")
    assert list(tmp_path.glob("t.idx_v.idx*"))
    engine.execute("DROP INDEX idx_v")
    assert list(tmp_path.glob("t.idx_v.idx*")) == []


def test_spilling_operators_leave_no_temporary_directories(engine: Engine, tmp_path):
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY, g INT)")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY, a_id INT)")
    engine.execute("INSERT INTO a VALUES (1, 10), (2, 20), (3, 10)")
    engine.execute("INSERT INTO b VALUES (7, 1), (8, 3)")
    engine.execute("SELECT g, COUNT(*) FROM a GROUP BY g ORDER BY g")
    engine.execute("SELECT b.id FROM b JOIN a ON b.a_id = a.id ORDER BY b.id")
    leftovers = [path.name for path in tmp_path.iterdir() if path.is_dir()]
    assert leftovers == []
