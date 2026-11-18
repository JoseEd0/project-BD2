"""Una escritura que un índice rechaza a mitad no deja nada, con transacción o sin ella.

La tabla tiene un índice de cada tipo. Se hace fallar uno de ellos con la fila de una
clave elegida —su `insert` o su `delete` lanzan antes de tocar nada, como lo haría un
archivo que no existe— y se comprueba que la tabla y **todos** sus índices quedan como
estaban: las mismas filas y la misma respuesta de cada índice, que además coincide con la
de recorrer la tabla.
"""

from collections.abc import Callable, Iterator

import pytest

from config import EngineConfig
from index.secondary import ColumnIndex
from query.engine import Engine
from storage.record_id import RecordId
from txn import LockManager, Session, TransactionManager

ROWS = 20
KEY_POSITION = 0
UPDATED = range(10, 20)
VICTIM = 15
FAR_RADIUS = 1e9
MORE_THAN_ALL = ROWS * 2
COLUMNS = ("id", "u", "v", "w", "p", "txt", "emb")
EQUALITY_COLUMNS = COLUMNS[:4]
TEXT_POSITION = COLUMNS.index("txt")
INDEXES = (
    "CREATE INDEX iv ON t USING BTREE (v)",
    "CREATE INDEX iw ON t USING HASH (w)",
    "CREATE INDEX ip ON t USING RTREE (p)",
    "CREATE INDEX itxt ON t USING INVERTED (txt)",
)
WORDS = ("rojo", "azul", "verde")
INSERT_ROWS = (100, 101, 102, 103)
ALL_ROWS_UPDATE = (
    "UPDATE t SET v = v + 1000, w = w + 1000, u = u + 1000, p = POINT(1.5, 2.5), "
    f"txt = 'verde cambiado', emb = [9.0, 1.0] WHERE id >= {UPDATED.start} AND id < {UPDATED.stop}"
)
KEY_UPDATE = f"UPDATE t SET id = id + 100 WHERE id >= {UPDATED.start} AND id < {UPDATED.stop}"


class InjectedError(Exception):
    """El fallo que se le inyecta a un índice."""


def row_sql(key: int) -> str:
    word = WORDS[key % len(WORDS)]
    return (
        f"({key}, {key + 500}, {key % 7}, {key % 5}, POINT({key / 10}, {key / 20}), "
        f"'{word} fila{key}', [{key + 1}.0, {key % 3 + 1}.0])"
    )


def similarity_method(failing: str) -> str:
    return "IVF" if failing == "ivf" else "HNSW"


def position_of(failing: str) -> int:
    return COLUMNS.index({"hnsw": "emb", "ivf": "emb"}.get(failing, failing))


@pytest.fixture(params=["id", "u", "v", "w", "p", "txt", "hnsw", "ivf"])
def failing(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def session(config: EngineConfig, failing: str) -> Iterator[Session]:
    with Engine(config) as engine, Session(engine, LockManager(config), TransactionManager()) as s:
        s.execute(
            "CREATE TABLE t (id INT PRIMARY KEY, u INT UNIQUE, v INT, w INT, p POINT, "
            "txt TEXT, emb VECTOR(2))"
        )
        s.execute("INSERT INTO t VALUES " + ", ".join(row_sql(key) for key in range(ROWS)))
        for statement in INDEXES:
            s.execute(statement)
        s.execute(f"CREATE INDEX iemb ON t USING {similarity_method(failing)} (emb)")
        yield s


def inject(monkeypatch: pytest.MonkeyPatch, operation: str, position: int, victim: int) -> None:
    """Hace que el índice de la columna en `position` rechace una vez el `operation` de la
    fila con clave `victim`, como un archivo que no se puede leer: devolverle después el
    valor que tenía funciona."""
    original: Callable[[ColumnIndex, bytes, RecordId], object] = getattr(ColumnIndex, operation)
    pending = {victim}

    def failing_operation(index: ColumnIndex, record: bytes, record_id: RecordId) -> object:
        key = index._serializer.unpack_field(record, KEY_POSITION)
        if index._position == position and key in pending:
            pending.discard(key)
            raise InjectedError(f"{operation} de la fila {victim} en la columna {position}")
        return original(index, record, record_id)

    monkeypatch.setattr(ColumnIndex, operation, failing_operation)


def rows(session: Session) -> tuple[tuple[object, ...], ...]:
    return session.execute("SELECT * FROM t ORDER BY id").rows


def ids(session: Session, sql: str) -> list[int]:
    return sorted(row[0] for row in session.execute(sql).rows)


def answers(session: Session, method: str) -> dict[str, object]:
    """Lo que responde cada índice, tras comprobar que coincide con recorrer la tabla."""
    table = rows(session)
    found: dict[str, object] = {}
    for position, column in enumerate(EQUALITY_COLUMNS):
        by_index = [ids(session, f"SELECT id FROM t WHERE {column} = {row[position]}") for row in table]
        scanned = [sorted(other[0] for other in table if other[position] == row[position]) for row in table]
        assert by_index == scanned
        found[column] = by_index
    found["p"] = ids(session, f"SELECT id FROM t WHERE distancia(p, POINT(0, 0)) < {FAR_RADIUS}")
    found["knn"] = ids(session, f"SELECT id FROM t ORDER BY distancia(p, POINT(0, 0)) LIMIT {MORE_THAN_ALL}")
    found["emb"] = ids(session, similar(method))
    assert found["p"] == found["knn"] == found["emb"] == sorted(row[0] for row in table)
    for word in WORDS:
        found[word] = ids(session, f"SELECT id FROM t WHERE MATCH(txt, '{word}')")
        assert found[word] == sorted(row[0] for row in table if word in str(row[TEXT_POSITION]).split())
    return found


def similar(method: str) -> str:
    effort = "ef_search" if method == "HNSW" else "probes"
    return (
        f"SELECT id FROM t WHERE SIMILAR_TO(emb, [1.0, 1.0], k = {MORE_THAN_ALL}) "
        f"USING {method} WITH {effort} = {MORE_THAN_ALL}"
    )


def state(session: Session, failing: str) -> tuple[object, ...]:
    return rows(session), answers(session, similarity_method(failing))


def assert_failure_leaves_nothing(
    session: Session, failing: str, statement: str, in_transaction: bool
) -> None:
    if in_transaction:
        session.execute("BEGIN")
        session.execute(f"INSERT INTO t VALUES {row_sql(ROWS + 50)}")
    before = state(session, failing)

    with pytest.raises(InjectedError):
        session.execute(statement)

    assert session.in_transaction is in_transaction
    assert state(session, failing) == before
    if in_transaction:
        session.execute(f"INSERT INTO t VALUES {row_sql(ROWS + 51)}")
        session.execute("COMMIT")
        assert ids(session, f"SELECT id FROM t WHERE id > {ROWS}") == [ROWS + 50, ROWS + 51]


@pytest.mark.parametrize("in_transaction", [False, True], ids=["autocommit", "transaction"])
def test_a_multi_row_insert_that_an_index_rejects_leaves_no_row(
    session: Session, failing: str, in_transaction: bool, monkeypatch: pytest.MonkeyPatch
):
    inject(monkeypatch, "insert", position_of(failing), INSERT_ROWS[2])
    values = ", ".join(row_sql(key) for key in INSERT_ROWS)

    assert_failure_leaves_nothing(session, failing, f"INSERT INTO t VALUES {values}", in_transaction)


@pytest.mark.parametrize("in_transaction", [False, True], ids=["autocommit", "transaction"])
def test_an_update_that_an_index_rejects_restores_every_row(
    session: Session, failing: str, in_transaction: bool, monkeypatch: pytest.MonkeyPatch
):
    if failing == "id":
        pytest.skip("un UPDATE que conserva la clave no toca su índice")
    inject(monkeypatch, "insert", position_of(failing), VICTIM)

    assert_failure_leaves_nothing(session, failing, ALL_ROWS_UPDATE, in_transaction)


@pytest.mark.parametrize("in_transaction", [False, True], ids=["autocommit", "transaction"])
def test_an_update_of_the_key_that_an_index_rejects_restores_every_row(
    session: Session, failing: str, in_transaction: bool, monkeypatch: pytest.MonkeyPatch
):
    inject(monkeypatch, "insert", position_of(failing), VICTIM + 100)

    assert_failure_leaves_nothing(session, failing, KEY_UPDATE, in_transaction)


@pytest.mark.parametrize("in_transaction", [False, True], ids=["autocommit", "transaction"])
def test_a_delete_that_an_index_rejects_restores_every_row(
    session: Session, failing: str, in_transaction: bool, monkeypatch: pytest.MonkeyPatch
):
    inject(monkeypatch, "delete", position_of(failing), VICTIM)
    statement = f"DELETE FROM t WHERE id >= {UPDATED.start} AND id < {UPDATED.stop}"

    assert_failure_leaves_nothing(session, failing, statement, in_transaction)


def test_a_single_row_is_atomic_even_without_a_session(
    config: EngineConfig, monkeypatch: pytest.MonkeyPatch
):
    with Engine(config) as engine:
        engine.execute("CREATE TABLE s (id INT PRIMARY KEY, v INT)")
        engine.execute("CREATE INDEX iv ON s USING HASH (v)")
        engine.execute("INSERT INTO s VALUES (1, 10)")
        inject(monkeypatch, "insert", 1, 2)

        with pytest.raises(InjectedError):
            engine.execute("INSERT INTO s VALUES (2, 20)")

        monkeypatch.undo()
        assert engine.execute("SELECT * FROM s").rows == ((1, 10),)
        assert engine.execute("SELECT id FROM s WHERE id = 2").rows == ()
        assert engine.execute("SELECT id FROM s WHERE v = 20").rows == ()
        engine.execute("INSERT INTO s VALUES (2, 20)")
        assert engine.execute("SELECT id FROM s WHERE v = 20").rows == ((2,),)
