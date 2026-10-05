import threading

import pytest

from query.engine import Engine
from query.expressions import ExpressionError
from query.table import DuplicatePrimaryKeyError
from txn import DeadlockError, LockManager, Session, SessionError, TransactionManager
from txn.transaction import TransactionState

TRANSFER_ROUNDS = 40


def rows_of(session: Session, sql: str) -> tuple:
    return session.execute(sql).rows


def test_autocommit_persists_without_begin(cuentas: Session):
    cuentas.execute("UPDATE cuentas SET saldo = 1 WHERE id = 1")
    assert rows_of(cuentas, "SELECT saldo FROM cuentas WHERE id = 1") == ((1,),)
    assert not cuentas.in_transaction


def test_begin_opens_a_transaction(cuentas: Session):
    cuentas.execute("BEGIN TRANSACTION")
    assert cuentas.in_transaction


def test_two_begins_are_rejected(cuentas: Session):
    cuentas.execute("BEGIN")
    with pytest.raises(SessionError):
        cuentas.execute("BEGIN")


def test_commit_without_transaction_is_rejected(cuentas: Session):
    with pytest.raises(SessionError):
        cuentas.execute("COMMIT")


def test_commit_keeps_the_changes(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 999 WHERE id = 1")
    cuentas.execute("END TRANSACTION")
    assert rows_of(cuentas, "SELECT saldo FROM cuentas WHERE id = 1") == ((999,),)


def test_rollback_undoes_an_update(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 0 WHERE id = 1")
    cuentas.execute("ROLLBACK")
    assert rows_of(cuentas, "SELECT saldo FROM cuentas WHERE id = 1") == ((100,),)


def test_rollback_undoes_an_insert(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("INSERT INTO cuentas VALUES (9, 1)")
    cuentas.execute("ROLLBACK")
    assert rows_of(cuentas, "SELECT COUNT(*) FROM cuentas") == ((2,),)


def test_rollback_undoes_a_delete(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("DELETE FROM cuentas WHERE id = 2")
    cuentas.execute("ROLLBACK")
    assert rows_of(cuentas, "SELECT COUNT(*) FROM cuentas") == ((2,),)


def test_rollback_undoes_several_changes_in_reverse(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("INSERT INTO cuentas VALUES (3, 7)")
    cuentas.execute("UPDATE cuentas SET saldo = 0 WHERE id = 3")
    cuentas.execute("DELETE FROM cuentas WHERE id = 1")
    cuentas.execute("ROLLBACK")
    assert rows_of(cuentas, "SELECT id, saldo FROM cuentas ORDER BY id") == ((1, 100), (2, 50))


def test_the_transaction_records_its_changes(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 1 WHERE id = 1")
    transaction = cuentas.transaction
    assert transaction is not None
    assert len(transaction.changes) == 1
    cuentas.execute("COMMIT")
    assert transaction.state is TransactionState.COMMITTED


def test_closing_the_session_rolls_back(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 0 WHERE id = 1")
    cuentas.close()
    assert rows_of(cuentas, "SELECT saldo FROM cuentas WHERE id = 1") == ((100,),)


def test_locks_are_released_after_commit(
    cuentas: Session, locks: LockManager, engine: Engine, manager: TransactionManager
):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 1 WHERE id = 1")
    assert locks.holders_of("cuentas")
    cuentas.execute("COMMIT")
    assert locks.holders_of("cuentas") == {}


def test_a_writer_blocks_another_writer(
    cuentas: Session, engine: Engine, locks: LockManager, manager: TransactionManager
):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 1 WHERE id = 1")
    blocked = threading.Event()
    finished = threading.Event()

    def rival() -> None:
        other = Session(engine, locks, manager)
        blocked.set()
        other.execute("UPDATE cuentas SET saldo = 2 WHERE id = 2")
        finished.set()

    thread = threading.Thread(target=rival)
    thread.start()
    blocked.wait()
    assert not finished.wait(timeout=0.2)
    cuentas.execute("COMMIT")
    assert finished.wait(timeout=3)
    thread.join()


def test_concurrent_transfers_keep_the_total(
    cuentas: Session, engine: Engine, locks: LockManager, manager: TransactionManager
):
    """La race condition clásica: dos hilos leen, suman y escriben la misma fila."""

    def transfer() -> None:
        session = Session(engine, locks, manager)
        for _ in range(TRANSFER_ROUNDS):
            session.execute("BEGIN")
            session.execute("UPDATE cuentas SET saldo = saldo - 1 WHERE id = 1")
            session.execute("UPDATE cuentas SET saldo = saldo + 1 WHERE id = 2")
            session.execute("COMMIT")

    threads = [threading.Thread(target=transfer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    balances = rows_of(cuentas, "SELECT id, saldo FROM cuentas ORDER BY id")
    assert sum(row[1] for row in balances) == 150


def test_a_deadlock_aborts_one_of_the_two(
    cuentas: Session, engine: Engine, locks: LockManager, manager: TransactionManager
):
    cuentas.execute("CREATE TABLE otra (id INT PRIMARY KEY, saldo INT)")
    cuentas.execute("INSERT INTO otra VALUES (1, 5)")
    failures: list[str] = []

    def worker(first: str, second: str) -> None:
        session = Session(engine, locks, manager)
        try:
            session.execute("BEGIN")
            session.execute(f"UPDATE {first} SET saldo = saldo + 1 WHERE id = 1")
            threading.Event().wait(0.2)
            session.execute(f"UPDATE {second} SET saldo = saldo + 1 WHERE id = 1")
            session.execute("COMMIT")
        except DeadlockError:
            failures.append("deadlock")
            session.close()

    threads = [
        threading.Thread(target=worker, args=("cuentas", "otra")),
        threading.Thread(target=worker, args=("otra", "cuentas")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(failures) == 1
    assert locks.deadlocks_detected >= 1
    assert locks.holders_of("cuentas") == {}


def test_rollback_restores_the_spatial_index(engine, locks, manager):
    """Deshacer pasa por la tabla, así que el R-Tree vuelve a apuntar a la ubicación vieja."""
    session = Session(engine, locks, manager)
    session.execute("CREATE TABLE tiendas (id INT PRIMARY KEY, ubicacion POINT INDEX RTREE)")
    session.execute("INSERT INTO tiendas VALUES (1, POINT(-12.0, -77.0)), (2, POINT(-13.5, -72.0))")
    nearest_to_lima = "SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(-12.0, -77.0)) LIMIT 1"
    session.execute("BEGIN TRANSACTION")
    session.execute("UPDATE tiendas SET ubicacion = POINT(40.0, -3.7) WHERE id = 1")
    session.execute("DELETE FROM tiendas WHERE id = 2")
    session.execute("INSERT INTO tiendas VALUES (3, POINT(-12.0, -77.0))")
    assert session.execute(nearest_to_lima).rows == ((3,),)
    session.execute("ROLLBACK")
    result = session.execute(nearest_to_lima)
    assert result.rows == ((1,),)
    assert "SpatialNearestScan" in result.plan.render()
    everything = session.execute(
        "SELECT id FROM tiendas WHERE distancia(ubicacion, POINT(0.0, 0.0)) < 99999999"
    )
    assert sorted(row[0] for row in everything.rows) == [1, 2]


def test_a_failed_statement_leaves_nothing_inside_a_transaction(cuentas: Session):
    """Dentro de BEGIN, una sentencia que falla a mitad se deshace sola: confirmar después
    no puede dejar escrito un trozo de ella."""
    cuentas.execute("BEGIN")
    cuentas.execute("INSERT INTO cuentas VALUES (3, 7)")
    with pytest.raises(DuplicatePrimaryKeyError):
        cuentas.execute("INSERT INTO cuentas VALUES (4, 1), (5, 1), (1, 1)")
    with pytest.raises(ExpressionError):
        cuentas.execute("UPDATE cuentas SET saldo = 100 / (id - 3)")
    assert cuentas.in_transaction
    cuentas.execute("COMMIT")
    assert rows_of(cuentas, "SELECT id, saldo FROM cuentas ORDER BY id") == (
        (1, 100),
        (2, 50),
        (3, 7),
    )


def test_a_failed_statement_does_not_forget_the_earlier_changes(cuentas: Session):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 1 WHERE id = 1")
    with pytest.raises(DuplicatePrimaryKeyError):
        cuentas.execute("INSERT INTO cuentas VALUES (9, 9), (2, 0)")
    transaction = cuentas.transaction
    assert transaction is not None and len(transaction.changes) == 1
    cuentas.execute("ROLLBACK")
    assert rows_of(cuentas, "SELECT id, saldo FROM cuentas ORDER BY id") == ((1, 100), (2, 50))


def test_a_failed_statement_in_autocommit_leaves_nothing(cuentas: Session):
    with pytest.raises(DuplicatePrimaryKeyError):
        cuentas.execute("UPDATE cuentas SET id = 2 WHERE id = 1")
    with pytest.raises(DuplicatePrimaryKeyError):
        cuentas.execute("INSERT INTO cuentas VALUES (8, 1), (8, 2)")
    assert rows_of(cuentas, "SELECT id, saldo FROM cuentas ORDER BY id") == ((1, 100), (2, 50))
    assert not cuentas.in_transaction


def test_rollback_finds_rows_whose_values_were_converted_on_the_way_in(session: Session):
    """El registro guarda cada fila como quedó en la tabla —la fecha ya como fecha, el
    entero ya como real—, que es como hay que buscarla para deshacer."""
    session.execute("CREATE TABLE pedidos (id INT PRIMARY KEY, fecha DATE, total FLOAT, u POINT)")
    session.execute("INSERT INTO pedidos VALUES (1, '2024-01-05', 3, (1, 2))")
    before = rows_of(session, "SELECT * FROM pedidos")
    session.execute("BEGIN")
    session.execute("INSERT INTO pedidos VALUES (2, '2024-02-01', 7, POINT(3, 4))")
    session.execute("UPDATE pedidos SET fecha = '2025-12-31', total = 9 WHERE id = 1")
    session.execute("DELETE FROM pedidos WHERE id = 1")
    session.execute("ROLLBACK")
    assert rows_of(session, "SELECT * FROM pedidos") == before


@pytest.mark.parametrize("organization", ["", " INDEX SEQ", " INDEX BTREE"])
def test_rollback_undoes_a_change_of_primary_key(session: Session, organization: str):
    session.execute(f"CREATE TABLE t (id INT PRIMARY KEY{organization}, v INT)")
    session.execute("INSERT INTO t VALUES (1, 10), (2, 20), (3, 30)")
    session.execute("BEGIN")
    session.execute("UPDATE t SET id = id + 10")
    assert rows_of(session, "SELECT id FROM t WHERE id = 12") == ((12,),)
    session.execute("ROLLBACK")
    assert sorted(rows_of(session, "SELECT id, v FROM t")) == [(1, 10), (2, 20), (3, 30)]
    assert rows_of(session, "SELECT v FROM t WHERE id = 2") == ((20,),)


@pytest.mark.parametrize("organization", ["", " INDEX SEQ", " INDEX BTREE"])
@pytest.mark.parametrize(
    "update",
    [
        "UPDATE t SET id = id + 1",
        "UPDATE t SET id = 3 - id WHERE id IN (1, 2)",
        "UPDATE t SET id = id + 1, v = v + 1 WHERE id >= 2",
    ],
)
def test_rollback_undoes_keys_that_passed_from_one_row_to_another(
    session: Session, organization: str, update: str
):
    """Las claves se pisan unas a otras dentro de la sentencia; deshacerla tiene que
    devolver cada fila a la suya sin que dos coincidan por el camino."""
    session.execute(f"CREATE TABLE t (id INT PRIMARY KEY{organization}, v INT)")
    session.execute("INSERT INTO t VALUES (1, 10), (2, 20), (3, 30)")
    session.execute("BEGIN")
    session.execute(update)
    session.execute("UPDATE t SET v = v * 2")
    session.execute("ROLLBACK")
    assert sorted(rows_of(session, "SELECT id, v FROM t")) == [(1, 10), (2, 20), (3, 30)]
    for key, value in ((1, 10), (2, 20), (3, 30)):
        assert rows_of(session, f"SELECT v FROM t WHERE id = {key}") == ((value,),)


def waits_for(action, release) -> bool:
    """Si `action`, lanzada en otro hilo, se queda esperando hasta que se llama a `release`."""
    finished = threading.Event()

    def run() -> None:
        action()
        finished.set()

    thread = threading.Thread(target=run)
    thread.start()
    waited = not finished.wait(timeout=0.2)
    release()
    assert finished.wait(timeout=3)
    thread.join()
    return waited


def test_a_script_of_several_statements_is_rejected(cuentas: Session):
    with pytest.raises(SessionError, match="se esperaba una sentencia y llegaron 2"):
        cuentas.execute("SELECT * FROM cuentas; SELECT * FROM cuentas")


def test_explain_reads_the_table_so_it_waits_for_a_writer(
    cuentas: Session, engine: Engine, locks: LockManager, manager: TransactionManager
):
    cuentas.execute("BEGIN")
    cuentas.execute("UPDATE cuentas SET saldo = 0 WHERE id = 1")
    other = Session(engine, locks, manager)
    assert waits_for(
        lambda: other.execute("EXPLAIN ANALYZE SELECT * FROM cuentas"),
        lambda: cuentas.execute("COMMIT"),
    )


def test_dropping_an_index_locks_its_table(
    cuentas: Session, engine: Engine, locks: LockManager, manager: TransactionManager
):
    """El índice se nombra sin su tabla, pero lo que cambia es la tabla: mientras otra
    transacción la esté leyendo, el `DROP INDEX` tiene que esperar."""
    cuentas.execute("CREATE INDEX idx_saldo ON cuentas USING BTREE (saldo)")
    cuentas.execute("BEGIN")
    cuentas.execute("SELECT * FROM cuentas WHERE saldo = 100")
    other = Session(engine, locks, manager)
    assert waits_for(
        lambda: other.execute("DROP INDEX idx_saldo"), lambda: cuentas.execute("COMMIT")
    )
    assert engine.table_of_index("idx_saldo") is None


def test_the_table_of_an_index(cuentas: Session, engine: Engine):
    assert engine.table_of_index("pk_cuentas") == "cuentas"
    assert engine.table_of_index("PK_CUENTAS") == "cuentas"
    assert engine.table_of_index("fantasma") is None


def test_dropping_an_index_that_does_not_exist_needs_no_lock(cuentas: Session):
    assert "no existía" in cuentas.execute("DROP INDEX IF EXISTS fantasma").message
    with pytest.raises(Exception, match="el índice 'fantasma' no existe"):
        cuentas.execute("DROP INDEX fantasma")
    assert not cuentas.in_transaction


def test_transaction_statements_take_no_table_lock(cuentas: Session, locks: LockManager):
    cuentas.execute("BEGIN")
    assert locks.holders_of("cuentas") == {}
    cuentas.execute("ROLLBACK")
