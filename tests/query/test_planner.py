"""Elección del camino de acceso: es la decisión que el plan muestra al usuario."""

from config import EngineConfig
from query.engine import Engine


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
