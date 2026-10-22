"""Elección del camino de acceso: es la decisión que el plan muestra al usuario."""

from dataclasses import replace

import pytest

from config import EngineConfig
from index.bplustree import UnclusteredBPlusIndex
from query.engine import Engine
from sql.errors import SqlSyntaxError

RANGE_ROWS = 200
RANGE_LIMIT = 3


def plan_of(engine: Engine, sql: str) -> str:
    result = engine.execute(sql)
    assert result.plan is not None
    return result.plan.render()


def test_without_conditions_it_scans(alumnos: Engine):
    assert "SequentialScan" in plan_of(alumnos, "SELECT * FROM alumnos")


def test_equality_on_a_hash_indexed_column_uses_the_index(alumnos: Engine):
    plan = plan_of(alumnos, "SELECT * FROM alumnos WHERE ciudad = 'lima'")
    assert "IndexLookup" in plan
    assert "HASH" in plan


def test_equality_on_the_primary_key_uses_its_index(alumnos: Engine):
    assert "IndexLookup" in plan_of(alumnos, "SELECT * FROM alumnos WHERE id = 4")


def test_range_on_the_primary_key_uses_the_btree(alumnos: Engine):
    assert "IndexRange" in plan_of(alumnos, "SELECT * FROM alumnos WHERE id BETWEEN 2 AND 5")


def test_range_on_a_hash_index_falls_back_to_the_scan(alumnos: Engine):
    """El hash no guarda orden: para un rango no sirve."""
    assert "SequentialScan" in plan_of(alumnos, "SELECT * FROM alumnos WHERE ciudad > 'a'")


def test_column_without_index_falls_back_to_the_scan(alumnos: Engine):
    assert "SequentialScan" in plan_of(alumnos, "SELECT * FROM alumnos WHERE nota = 3.0")


def test_the_index_is_found_inside_a_conjunction(alumnos: Engine):
    plan = plan_of(alumnos, "SELECT * FROM alumnos WHERE nota > 1 AND ciudad = 'lima'")
    assert "IndexLookup" in plan


def test_a_disjunction_cannot_use_the_index(alumnos: Engine):
    plan = plan_of(alumnos, "SELECT * FROM alumnos WHERE ciudad = 'lima' OR nota = 1.0")
    assert "SequentialScan" in plan


def test_sequential_table_uses_its_order_for_ranges(engine: Engine):
    engine.execute("CREATE TABLE s (id INT PRIMARY KEY INDEX SEQ, v VARCHAR(4))")
    engine.execute("INSERT INTO s VALUES (1, 'a')")
    assert "PrimaryKeyRange" in plan_of(engine, "SELECT * FROM s WHERE id BETWEEN 1 AND 2")


def test_clustered_table_uses_its_order_for_ranges(engine: Engine):
    engine.execute("CREATE TABLE c (id INT PRIMARY KEY INDEX BTREE, v VARCHAR(4))")
    engine.execute("INSERT INTO c VALUES (1, 'a')")
    plan = plan_of(engine, "SELECT * FROM c WHERE id BETWEEN 1 AND 2")
    assert "PrimaryKeyRange" in plan
    assert "clustered_btree" in plan


def test_the_plan_shows_the_whole_pipeline(alumnos: Engine):
    plan = plan_of(
        alumnos,
        "SELECT ciudad, COUNT(*) as total FROM alumnos WHERE nota > 2 "
        "GROUP BY ciudad ORDER BY total DESC LIMIT 2",
    )
    for step in ("Limit", "Projection", "ExternalSort", "HashAggregate", "Filter", "Scan"):
        assert step in plan


def test_join_appears_in_the_plan(engine: Engine):
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY)")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY)")
    engine.execute("INSERT INTO a VALUES (1)")
    engine.execute("INSERT INTO b VALUES (1)")
    assert "HashJoin" in plan_of(engine, "SELECT a.id FROM a JOIN b ON a.id = b.id")


def test_plan_serializes_to_a_dictionary(alumnos: Engine, config: EngineConfig):
    result = alumnos.execute("SELECT * FROM alumnos WHERE id = 1")
    assert result.plan is not None
    payload = result.plan.to_dict()
    assert payload["operation"] == "Projection"
    assert payload["children"][0]["children"][0]["operation"] == "IndexLookup"


def test_a_joined_table_also_uses_its_index(engine: Engine):
    """La tabla unida no tiene por qué recorrerse entera si el WHERE la acota."""
    engine.execute("CREATE TABLE pedidos (id INT PRIMARY KEY, cliente_id INT)")
    engine.execute(
        "CREATE TABLE clientes (id INT PRIMARY KEY, ciudad VARCHAR(10) INDEX HASH)"
    )
    engine.execute("INSERT INTO clientes VALUES (1, 'lima'), (2, 'cusco')")
    engine.execute("INSERT INTO pedidos VALUES (10, 1), (11, 2)")
    plan = plan_of(
        engine,
        "SELECT p.id FROM pedidos AS p JOIN clientes AS c ON p.cliente_id = c.id "
        "WHERE c.ciudad = 'lima'",
    )
    assert "IndexLookup" in plan
    assert "idx_clientes_ciudad" in plan


def test_an_unqualified_condition_is_not_pushed_into_a_join(engine: Engine):
    """Sin cualificar, la columna podría ser de la otra tabla: acotar dejaría fuera filas."""
    engine.execute("CREATE TABLE pedidos (id INT PRIMARY KEY, cliente_id INT)")
    engine.execute(
        "CREATE TABLE clientes (id INT PRIMARY KEY, ciudad VARCHAR(10) INDEX HASH)"
    )
    engine.execute("INSERT INTO clientes VALUES (1, 'lima')")
    engine.execute("INSERT INTO pedidos VALUES (10, 1)")
    plan = plan_of(
        engine,
        "SELECT p.id FROM pedidos AS p JOIN clientes AS c ON p.cliente_id = c.id "
        "WHERE ciudad = 'lima'",
    )
    assert "IndexLookup" not in plan


def test_pushing_the_condition_down_keeps_the_same_rows(engine: Engine):
    engine.execute("CREATE TABLE pedidos (id INT PRIMARY KEY, cliente_id INT, total INT)")
    engine.execute(
        "CREATE TABLE clientes (id INT PRIMARY KEY, ciudad VARCHAR(10) INDEX HASH)"
    )
    engine.execute("INSERT INTO clientes VALUES (1, 'lima'), (2, 'cusco'), (3, 'lima')")
    engine.execute("INSERT INTO pedidos VALUES (10, 1, 5), (11, 2, 7), (12, 3, 9)")
    result = engine.execute(
        "SELECT p.id FROM pedidos AS p JOIN clientes AS c ON p.cliente_id = c.id "
        "WHERE c.ciudad = 'lima' ORDER BY p.id"
    )
    assert result.rows == ((10,), (12,))


def test_a_small_sort_is_done_in_memory(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    engine.execute("INSERT INTO t VALUES (1, 30), (2, 10), (3, 20)")
    result = engine.execute("SELECT v FROM t ORDER BY v LIMIT 1")
    assert result.rows == ((10,),)
    assert "en memoria" in result.plan.render()


def test_the_sort_plan_reports_runs_and_merge_passes(config: EngineConfig):
    with Engine(replace(config, sort_buffer_pages=1)) as engine:
        engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        engine.execute("INSERT INTO t VALUES " + ", ".join(f"({i}, {-i})" for i in range(200)))
        result = engine.execute("SELECT v FROM t ORDER BY v LIMIT 1")
        assert result.rows == ((-199,),)
        assert "run(s)" in result.plan.render()
        assert "pasada(s)" in result.plan.render()


def test_explain_returns_the_plan_without_running_the_query(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT INDEX HASH)")
    engine.execute("INSERT INTO t VALUES (1, 10), (2, 20)")
    result = engine.execute("EXPLAIN SELECT id FROM t WHERE v = 10")
    assert result.rows == ()
    assert "IndexLookup" in result.plan.render()
    assert result.plan.actual_rows is None


def test_explain_analyze_reports_actual_rows_per_operator(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    engine.execute("INSERT INTO t VALUES (1, 10), (2, 20), (3, 30)")
    result = engine.execute("EXPLAIN ANALYZE SELECT id FROM t WHERE v > 10")
    assert result.rows == ()
    assert result.affected_rows == 2
    filter_node = result.plan.children[0]
    assert (filter_node.operation, filter_node.actual_rows) == ("Filter", 2)
    assert filter_node.children[0].actual_rows == 3


def test_a_limit_does_not_pull_one_row_too_many(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    engine.execute("INSERT INTO t VALUES (1), (2), (3), (4)")
    plan = engine.execute("SELECT id FROM t LIMIT 2").plan
    assert plan.actual_rows == 2
    assert plan.children[0].actual_rows == 2


def test_explain_only_accepts_a_select(engine: Engine):
    with pytest.raises(SqlSyntaxError, match="EXPLAIN"):
        engine.execute("EXPLAIN DELETE FROM t")


@pytest.fixture(params=["SEQ", "BTREE"])
def ordenada(engine: Engine, request: pytest.FixtureRequest) -> Engine:
    """Una tabla guardada por clave, cargada en desorden para que haya filas en la zona de
    desbordamiento del secuencial y divisiones en el B+."""
    engine.execute(f"CREATE TABLE t (id INT PRIMARY KEY INDEX {request.param}, v INT)")
    keys = list(range(0, 600, 3)) + list(range(1, 600, 3)) + list(range(2, 600, 3))
    for start in range(0, len(keys), 100):
        values = ", ".join(f"({key}, {key % 7})" for key in keys[start : start + 100])
        engine.execute(f"INSERT INTO t VALUES {values}")
    return engine


def test_ordering_by_the_key_of_an_ordered_table_does_not_sort(ordenada: Engine):
    result = ordenada.execute("SELECT id FROM t ORDER BY id")
    assert [row[0] for row in result.rows] == list(range(600))
    assert result.plan is not None
    plan = result.plan.render()
    assert "OrderedScan" in plan and "ExternalSort" not in plan


def test_a_key_range_already_comes_out_in_order(ordenada: Engine):
    result = ordenada.execute("SELECT id FROM t WHERE id BETWEEN 100 AND 180 ORDER BY id")
    assert [row[0] for row in result.rows] == list(range(100, 181))
    assert result.plan is not None
    plan = result.plan.render()
    assert "PrimaryKeyRange" in plan and "ExternalSort" not in plan


def test_a_limit_over_the_table_order_stops_reading_early(ordenada: Engine):
    result = ordenada.execute("SELECT id FROM t WHERE v < 100 ORDER BY id LIMIT 5")
    assert [row[0] for row in result.rows] == [0, 1, 2, 3, 4]
    assert result.plan is not None
    assert "OrderedScan" in result.plan.render() and "(filas=5," in result.plan.render()


@pytest.mark.parametrize(
    "query",
    [
        "SELECT id FROM t ORDER BY id DESC",
        "SELECT v FROM t ORDER BY v",
        "SELECT id FROM t ORDER BY id, v",
        "SELECT v, COUNT(*) FROM t GROUP BY v ORDER BY v",
        "SELECT a.id FROM t a JOIN t b ON a.id = b.id ORDER BY a.id",
    ],
)
def test_any_other_order_still_sorts(ordenada: Engine, query: str):
    result = ordenada.execute(query)
    assert result.plan is not None
    assert "ExternalSort" in result.plan.render()
    values = [row[0] for row in result.rows]
    assert values == sorted(values, reverse="DESC" in query)


def test_a_heap_table_has_no_order_to_offer(alumnos: Engine):
    assert "ExternalSort" in plan_of(alumnos, "SELECT * FROM alumnos ORDER BY id")


def test_two_bounds_on_a_column_form_a_single_range(alumnos: Engine):
    plan = plan_of(alumnos, "SELECT * FROM alumnos WHERE id > 1 AND nota > 0 AND id < 5")
    assert "IndexRange" in plan
    assert "entre 1 y 5" in plan


def test_an_equality_beats_a_range_written_before_it(alumnos: Engine):
    plan = plan_of(alumnos, "SELECT * FROM alumnos WHERE id > 1 AND ciudad = 'lima'")
    assert "IndexLookup" in plan
    assert "ciudad" in plan


def test_in_on_an_indexed_column_looks_up_each_value(alumnos: Engine):
    plan = plan_of(alumnos, "SELECT * FROM alumnos WHERE id IN (2, 4)")
    assert "IndexLookup" in plan
    assert "IN (2, 4)" in plan


def test_in_with_repeated_values_and_null_returns_each_row_once(alumnos: Engine):
    rows = alumnos.execute("SELECT id FROM alumnos WHERE id IN (2, 2, NULL) ORDER BY id").rows
    assert rows == ((2,),)


def test_grouping_and_joining_say_whether_they_needed_disk(config: EngineConfig):
    queries = {
        "HashAggregate": "SELECT v, COUNT(*) FROM t GROUP BY v",
        "HashJoin": "SELECT a.id FROM t a JOIN t b ON a.v = b.v",
    }
    with Engine(config) as small:
        small.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        small.execute("INSERT INTO t VALUES (1, 1), (2, 1), (3, 2)")
        for operator, query in queries.items():
            plan = small.execute(f"EXPLAIN ANALYZE {query}").plan.render()
            assert f"{operator}:" in plan and "en memoria" in plan
    tight_config = replace(
        config,
        sort_buffer_pages=1,
        hash_partitions=4,
        data_directory=config.data_directory / "ajustado",
    )
    with Engine(tight_config) as tight:
        tight.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        tight.execute("INSERT INTO t VALUES " + ", ".join(f"({i}, {i})" for i in range(300)))
        for query in queries.values():
            plan = tight.execute(f"EXPLAIN ANALYZE {query}").plan.render()
            assert "nivel(es) de particiones" in plan


def test_a_limit_stops_reading_an_index_range(engine: Engine, monkeypatch: pytest.MonkeyPatch):
    """El salto al heap pide las direcciones al índice de una en una: con un `LIMIT`, el
    rango deja de leerse en cuanto hay filas suficientes."""
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    engine.execute("INSERT INTO t VALUES " + ", ".join(f"({n}, {n})" for n in range(RANGE_ROWS)))
    read: list[object] = []
    range_search = UnclusteredBPlusIndex.range_search

    def counted(index: UnclusteredBPlusIndex, low: object, high: object):
        for entry in range_search(index, low, high):
            read.append(entry)
            yield entry

    monkeypatch.setattr(UnclusteredBPlusIndex, "range_search", counted)
    result = engine.execute(f"SELECT id FROM t WHERE id > 5 LIMIT {RANGE_LIMIT}")
    assert "IndexRange" in result.plan.render()
    assert [row[0] for row in result.rows] == [6, 7, 8]
    assert len(read) <= RANGE_LIMIT + 1
