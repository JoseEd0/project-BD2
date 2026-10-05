"""El registro de deshacer y el ciclo de vida de una transacción, sin sesión de por medio."""

import pytest

from query.engine import Engine
from txn.transaction import (
    Transaction,
    TransactionError,
    TransactionManager,
    TransactionState,
    undo,
    undo_statement,
)


@pytest.fixture
def cuentas(engine: Engine) -> Engine:
    engine.execute("CREATE TABLE cuentas (id INT PRIMARY KEY, saldo INT)")
    engine.execute("INSERT INTO cuentas VALUES (1, 100), (2, 50)")
    return engine


def rows(engine: Engine) -> tuple:
    return engine.execute("SELECT id, saldo FROM cuentas ORDER BY id").rows


def test_the_manager_counts_open_transactions(manager: TransactionManager):
    first, second = manager.begin(), manager.begin()
    assert first.identifier != second.identifier
    assert manager.active_count == 2
    manager.finish(first, TransactionState.COMMITTED)
    assert manager.active_count == 1
    assert first.state is TransactionState.COMMITTED
    assert not first.is_active


def test_a_transaction_is_finished_only_once(manager: TransactionManager):
    transaction = manager.begin()
    manager.finish(transaction, TransactionState.ABORTED)
    with pytest.raises(TransactionError, match="ya está cerrada"):
        manager.finish(transaction, TransactionState.COMMITTED)
    assert transaction.state is TransactionState.ABORTED


def test_a_finished_transaction_records_nothing_more(manager: TransactionManager):
    transaction = manager.begin()
    transaction.record_insert("cuentas", (3, 1))
    manager.finish(transaction, TransactionState.COMMITTED)
    with pytest.raises(TransactionError, match="ya no está activa"):
        transaction.record_delete("cuentas", (3, 1))
    assert len(transaction.changes) == 1


def test_changes_are_undone_in_reverse_order(cuentas: Engine):
    """Insertar una fila y luego modificarla: deshacer en el orden de ida restauraría el
    valor antiguo de una fila que todavía no se ha quitado."""
    transaction = Transaction(identifier=1)
    cuentas.execute("INSERT INTO cuentas VALUES (3, 10)", journal=transaction)
    cuentas.execute("UPDATE cuentas SET saldo = 20 WHERE id = 3", journal=transaction)
    cuentas.execute("DELETE FROM cuentas WHERE id = 1", journal=transaction)
    assert transaction.tables_touched() == {"cuentas"}
    assert undo(transaction, {"cuentas": cuentas.table("cuentas")}) == 3
    assert rows(cuentas) == ((1, 100), (2, 50))


def test_only_the_changes_of_one_statement_can_be_undone(cuentas: Engine):
    transaction = Transaction(identifier=1)
    cuentas.execute("UPDATE cuentas SET saldo = 0 WHERE id = 1", journal=transaction)
    kept = len(transaction.changes)
    cuentas.execute("DELETE FROM cuentas WHERE id = 2", journal=transaction)
    assert undo_statement(transaction, {"cuentas": cuentas.table("cuentas")}, kept) == 1
    assert len(transaction.changes) == kept
    assert rows(cuentas) == ((1, 0), (2, 50))


def test_undoing_needs_every_table_it_touched(cuentas: Engine):
    transaction = Transaction(identifier=1)
    cuentas.execute("DELETE FROM cuentas WHERE id = 2", journal=transaction)
    with pytest.raises(TransactionError, match="falta la tabla 'cuentas'"):
        undo(transaction, {})


def test_a_change_whose_row_is_gone_is_not_counted_as_undone(cuentas: Engine):
    """Deshacer busca la fila tal como la dejó el cambio; si ya no está, no inventa nada."""
    transaction = Transaction(identifier=1)
    transaction.record_insert("cuentas", (9, 9))
    transaction.record_update("cuentas", (8, 8), (8, 80))
    assert undo(transaction, {"cuentas": cuentas.table("cuentas")}) == 0
    assert rows(cuentas) == ((1, 100), (2, 50))
