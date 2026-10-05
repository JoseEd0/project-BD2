"""Reuniones de punta a punta: cada tipo de JOIN y cada forma de `ON`, contra el resultado
calculado a mano con dos bucles."""

from collections.abc import Callable

import pytest

from query.engine import Engine
from query.expressions import AmbiguousColumnError, ExpressionError

Row = tuple
Condition = Callable[[Row, Row], bool | None]

# pedidos(id, cliente, total) — el pedido 5 no tiene cliente y el 6 apunta a uno que no existe.
PEDIDOS = [(1, 10, 50.0), (2, 10, 70.0), (3, 20, 20.0), (4, 30, 99.0), (5, None, 5.0), (6, 77, 1.0)]
# clientes(id, nombre, tope) — el cliente 40 no tiene pedidos y el de id NULL no empareja con nadie.
CLIENTES = [(10, "ana", 60.0), (20, "luis", 10.0), (30, "eva", None), (40, "sin pedidos", 500.0)]
NO_PEDIDO = (None, None, None)
NO_CLIENTE = (None, None, None)
KINDS = {
    "JOIN": (False, False),
    "LEFT JOIN": (True, False),
    "RIGHT JOIN": (False, True),
    "FULL JOIN": (True, True),
}


def same_client(pedido: Row, cliente: Row) -> bool | None:
    return None if pedido[1] is None else pedido[1] == cliente[0]


def under_the_limit(pedido: Row, cliente: Row) -> bool | None:
    return None if cliente[2] is None else pedido[2] < cliente[2]


def both(pedido: Row, cliente: Row) -> bool | None:
    return bool(same_client(pedido, cliente)) and bool(under_the_limit(pedido, cliente))


CONDITIONS: dict[str, Condition] = {
    "p.cliente = c.id": same_client,
    "c.id = p.cliente": same_client,
    "p.cliente = c.id AND p.total < c.tope": both,
    "p.total < c.tope AND c.id = p.cliente": both,
    "p.total < c.tope": under_the_limit,
    "p.total < c.tope OR p.cliente = c.id": lambda pedido, cliente: bool(
        under_the_limit(pedido, cliente)
    )
    or bool(same_client(pedido, cliente)),
}


def expected(kind: str, condition: Condition) -> list[Row]:
    keep_left, keep_right = KINDS[kind]
    rows = [
        (*pedido, *cliente)
        for pedido in PEDIDOS
        for cliente in CLIENTES
        if condition(pedido, cliente) is True
    ]
    if keep_left:
        rows += [
            (*pedido, *NO_CLIENTE)
            for pedido in PEDIDOS
            if not any(condition(pedido, cliente) is True for cliente in CLIENTES)
        ]
    if keep_right:
        rows += [
            (*NO_PEDIDO, *cliente)
            for cliente in CLIENTES
            if not any(condition(pedido, cliente) is True for pedido in PEDIDOS)
        ]
    return sorted(rows, key=repr)


def literal(value: object) -> str:
    return "NULL" if value is None else repr(value)


@pytest.fixture
def tienda(engine: Engine) -> Engine:
    engine.execute("CREATE TABLE pedidos (id INT PRIMARY KEY, cliente INT, total FLOAT)")
    engine.execute("CREATE TABLE clientes (id INT PRIMARY KEY, nombre VARCHAR(12), tope FLOAT)")
    for table, rows in (("pedidos", PEDIDOS), ("clientes", CLIENTES)):
        values = ", ".join(f"({', '.join(map(literal, row))})" for row in rows)
        engine.execute(f"INSERT INTO {table} VALUES {values}")
    return engine


def joined(engine: Engine, kind: str, condition: str, tail: str = "") -> list[Row]:
    result = engine.execute(f"SELECT * FROM pedidos p {kind} clientes c ON {condition}{tail}")
    return sorted(result.rows, key=repr)


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("condition", list(CONDITIONS))
def test_every_kind_of_join_with_every_kind_of_condition(tienda: Engine, kind: str, condition: str):
    assert joined(tienda, kind, condition) == expected(kind, CONDITIONS[condition])


def test_an_equality_is_joined_by_hashing_and_anything_else_by_nested_loops(tienda: Engine):
    def plan(condition: str) -> str:
        result = tienda.execute(f"SELECT * FROM pedidos p LEFT JOIN clientes c ON {condition}")
        assert result.plan is not None
        return result.plan.render()

    assert "HashJoin: LEFT · p.cliente = c.id" in plan("p.cliente = c.id")
    with_rest = plan("p.total < c.tope AND c.id = p.cliente")
    assert "HashJoin: LEFT · p.cliente = c.id AND p.total < c.tope" in with_rest
    assert "NestedLoopJoin: LEFT · p.total < c.tope" in plan("p.total < c.tope")


def test_null_never_matches_null(engine: Engine):
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY, k INT)")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY, k INT)")
    engine.execute("INSERT INTO a VALUES (1, NULL), (2, 7)")
    engine.execute("INSERT INTO b VALUES (1, NULL), (2, 7)")
    inner = engine.execute("SELECT a.id, b.id FROM a JOIN b ON a.k = b.k").rows
    assert inner == ((2, 2),)
    full = engine.execute("SELECT a.id, b.id FROM a FULL JOIN b ON a.k = b.k").rows
    assert sorted(full, key=repr) == sorted([(2, 2), (1, None), (None, 1)], key=repr)


def test_a_join_on_two_columns_needs_both_to_agree(engine: Engine):
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY, x INT, y INT)")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY, x INT, y INT)")
    engine.execute("INSERT INTO a VALUES (1, 1, 1), (2, 1, 2), (3, 2, NULL)")
    engine.execute("INSERT INTO b VALUES (1, 1, 1), (2, 1, 3), (3, 2, NULL)")
    result = engine.execute("SELECT a.id, b.id FROM a JOIN b ON a.x = b.x AND a.y = b.y")
    assert result.rows == ((1, 1),)
    assert result.plan is not None and "a.x = b.x AND a.y = b.y" in result.plan.render()


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_where_on_either_table_filters_after_the_join(tienda: Engine, kind: str):
    """El WHERE se aplica al resultado de la reunión, también cuando una de sus condiciones
    baja al índice de una tabla para acotar su lectura."""
    everything = expected(kind, same_client)
    for where, keep in (
        ("c.id = 10", lambda row: row[3] == 10),
        ("p.id = 3", lambda row: row[0] == 3),
        ("c.id IS NULL", lambda row: row[3] is None),
        ("p.id IS NULL", lambda row: row[0] is None),
        ("p.id BETWEEN 2 AND 5", lambda row: row[0] is not None and 2 <= row[0] <= 5),
    ):
        got = joined(tienda, kind, "p.cliente = c.id", f" WHERE {where}")
        assert got == [row for row in everything if keep(row)], where


def test_an_outer_join_with_an_empty_side(tienda: Engine):
    tienda.execute("CREATE TABLE vacia (id INT PRIMARY KEY, cliente INT)")
    left = tienda.execute("SELECT c.id, v.id FROM clientes c LEFT JOIN vacia v ON v.cliente = c.id")
    assert sorted(left.rows) == [(10, None), (20, None), (30, None), (40, None)]
    right = tienda.execute("SELECT c.id, v.id FROM vacia v RIGHT JOIN clientes c ON v.cliente = c.id")
    assert sorted(right.rows) == [(10, None), (20, None), (30, None), (40, None)]
    inner = tienda.execute("SELECT c.id FROM clientes c JOIN vacia v ON v.cliente > c.id")
    assert inner.rows == ()


def test_outer_joins_feed_grouping_and_sorting(tienda: Engine):
    result = tienda.execute(
        "SELECT c.nombre, COUNT(p.id) AS pedidos FROM clientes c "
        "LEFT JOIN pedidos p ON p.cliente = c.id GROUP BY c.nombre ORDER BY c.nombre"
    )
    assert result.rows == (("ana", 2), ("eva", 1), ("luis", 1), ("sin pedidos", 0))


def test_joins_of_different_kinds_chain(tienda: Engine):
    tienda.execute("CREATE TABLE envios (id INT PRIMARY KEY, pedido INT)")
    tienda.execute("INSERT INTO envios VALUES (1, 1), (2, 3), (3, 99)")
    result = tienda.execute(
        "SELECT c.id, p.id, e.id FROM clientes c "
        "LEFT JOIN pedidos p ON p.cliente = c.id "
        "LEFT JOIN envios e ON e.pedido = p.id"
    )
    assert sorted(result.rows, key=repr) == sorted(
        [(10, 1, 1), (10, 2, None), (20, 3, 2), (30, 4, None), (40, None, None)], key=repr
    )


def test_a_nested_loop_join_larger_than_one_block(engine: Engine):
    """Con páginas de prueba un bloque son pocas filas: la entrada izquierda ocupa varios y
    la derecha se recorre una vez por cada uno."""
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY)")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY)")
    engine.execute("INSERT INTO a VALUES " + ", ".join(f"({number})" for number in range(400)))
    engine.execute("INSERT INTO b VALUES " + ", ".join(f"({number})" for number in range(0, 400, 50)))
    result = engine.execute("SELECT COUNT(*) FROM a JOIN b ON a.id < b.id")
    assert result.rows == ((sum(range(0, 400, 50)),),)
    full = engine.execute("SELECT COUNT(*) FROM a FULL JOIN b ON a.id < b.id AND b.id < 100")
    assert full.rows == ((50 + 350 + 7,),)


def test_join_conditions_are_validated_before_reading_rows(tienda: Engine):
    with pytest.raises(AmbiguousColumnError):
        tienda.execute("SELECT * FROM pedidos p JOIN clientes c ON id = p.cliente")
    with pytest.raises(ExpressionError, match="no existe"):
        tienda.execute("SELECT * FROM pedidos p LEFT JOIN clientes c ON p.nope = c.id")
    with pytest.raises(ExpressionError, match="no se puede comparar"):
        tienda.execute("SELECT * FROM pedidos p JOIN clientes c ON p.total < c.nombre")


def test_joins_leave_no_temporary_files(tienda: Engine):
    tienda.execute("SELECT * FROM pedidos p FULL JOIN clientes c ON p.total < c.tope")
    tienda.execute("SELECT * FROM pedidos p FULL JOIN clientes c ON p.cliente = c.id")
    leftovers = [path for path in tienda.config.data_directory.iterdir() if path.is_dir()]
    assert leftovers == []


def test_an_equality_within_one_table_is_not_a_join_key(tienda: Engine):
    """`p.id = p.cliente` compara dos columnas del mismo lado: no reparte nada entre las
    dos tablas, así que no sirve de clave de hash y la reunión va por bucles anidados."""
    result = tienda.execute(
        "EXPLAIN ANALYZE SELECT p.id, c.id FROM pedidos AS p JOIN clientes AS c "
        "ON p.id = p.cliente OR p.cliente = c.id"
    )
    assert "NestedLoopJoin" in result.plan.render()
    same_side = tienda.execute(
        "SELECT p.id, c.id FROM pedidos AS p JOIN clientes AS c ON p.cliente = p.cliente "
        "AND p.cliente = c.id ORDER BY p.id"
    )
    assert "HashJoin" in same_side.plan.render()
    assert same_side.rows == ((1, 10), (2, 10), (3, 20), (4, 30))
    only_one_side = tienda.execute(
        "SELECT COUNT(*) FROM pedidos AS p JOIN clientes AS c ON p.id = p.cliente"
    )
    assert "NestedLoopJoin" in only_one_side.plan.render()
    assert only_one_side.rows == ((0,),)
