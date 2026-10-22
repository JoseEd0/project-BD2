"""UPDATE, DELETE y ROLLBACK buscan las filas por el mismo camino que un SELECT.

Si el WHERE se puede resolver con un índice o con la organización de la tabla, recorrer la
tabla entera para encontrar una fila es trabajo de más. Estos tests prohíben el recorrido
y comprueban que la escritura sale igual.
"""

from collections.abc import Iterator

import pytest

from config import EngineConfig
from query.engine import Engine
from query.table import Table
from txn import LockManager, Session, TransactionManager

ORGANIZATIONS = ["", " INDEX HASH", " INDEX BTREE", " INDEX SEQ"]
ROWS = 60


def forbid_scans(monkeypatch: pytest.MonkeyPatch) -> None:
    def scan(_: Table) -> Iterator[object]:
        raise AssertionError("se recorrió la tabla entera")

    monkeypatch.setattr(Table, "addressed_scan", scan)


@pytest.fixture(params=ORGANIZATIONS)
def keyed(request: pytest.FixtureRequest, engine: Engine) -> Engine:
    engine.execute(f"CREATE TABLE t (id INT PRIMARY KEY{request.param}, v INT)")
    engine.execute("INSERT INTO t VALUES " + ", ".join(f"({i}, {i})" for i in range(ROWS)))
    return engine


def test_delete_by_key_does_not_scan(keyed: Engine, monkeypatch: pytest.MonkeyPatch):
    forbid_scans(monkeypatch)
    assert keyed.execute("DELETE FROM t WHERE id = 7").affected_rows == 1
    assert keyed.execute("DELETE FROM t WHERE id IN (8, 9, 99)").affected_rows == 2
    monkeypatch.undo()
    assert keyed.execute("SELECT COUNT(*) FROM t").rows == ((ROWS - 3,),)


def test_update_by_key_does_not_scan(keyed: Engine, monkeypatch: pytest.MonkeyPatch):
    forbid_scans(monkeypatch)
    assert keyed.execute("UPDATE t SET v = -1 WHERE id = 3 AND v = 3").affected_rows == 1
    monkeypatch.undo()
    assert keyed.execute("SELECT id FROM t WHERE v = -1").rows == ((3,),)


def test_a_range_on_an_ordered_key_does_not_scan(engine: Engine, monkeypatch: pytest.MonkeyPatch):
    engine.execute("CREATE TABLE s (id INT PRIMARY KEY INDEX SEQ, v INT)")
    engine.execute("INSERT INTO s VALUES " + ", ".join(f"({i}, {i})" for i in range(ROWS)))
    forbid_scans(monkeypatch)
    assert engine.execute("DELETE FROM s WHERE id >= 10 AND id < 20").affected_rows == 10
    monkeypatch.undo()
    assert engine.execute("SELECT COUNT(*) FROM s").rows == ((ROWS - 10,),)


def test_a_rollback_finds_each_row_by_its_key(
    config: EngineConfig, keyed: Engine, monkeypatch: pytest.MonkeyPatch
):
    with Session(keyed, LockManager(config), TransactionManager()) as session:
        session.execute("BEGIN")
        session.execute("INSERT INTO t VALUES (100, 100)")
        session.execute("UPDATE t SET v = 0 WHERE id = 1")
        session.execute("DELETE FROM t WHERE id = 2")
        forbid_scans(monkeypatch)
        session.execute("ROLLBACK")
        monkeypatch.undo()
    rows = keyed.execute("SELECT id, v FROM t WHERE id IN (1, 2, 100) ORDER BY id").rows
    assert rows == ((1, 1), (2, 2))


def test_a_condition_without_a_path_still_scans(keyed: Engine):
    assert keyed.execute("DELETE FROM t WHERE v > 50").affected_rows == ROWS - 51
