import threading

import pytest

from query.engine import Engine
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
