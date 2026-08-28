"""Sesión: la puerta por la que un usuario ejecuta SQL con transacciones y bloqueos.

Separación de responsabilidades:

* el **motor** ejecuta sentencias y no sabe qué es una transacción;
* la **sesión** decide qué tablas hay que bloquear, en qué modo, y qué hacer si algo falla.

Fuera de una transacción explícita cada sentencia va en *autocommit*: se bloquea, se
ejecuta y se suelta. Dentro de `BEGIN ... COMMIT`, los bloqueos se conservan hasta el final
—**bloqueo en dos fases**—, que es lo que impide que otra transacción vea a medias lo que
esta está cambiando.
"""

from __future__ import annotations

from types import TracebackType

from query.engine import Engine, QueryResult
from query.table import Table
from sql import parse_script
from sql.nodes import (
    BeginTransactionStatement,
    CommitTransactionStatement,
    CreateIndexStatement,
    CreateTableFromFileStatement,
    CreateTableStatement,
    DeleteStatement,
    DropIndexStatement,
    DropTableStatement,
    InsertStatement,
    RollbackTransactionStatement,
    SelectStatement,
    Statement,
    UpdateStatement,
)
from txn.lock_manager import LockError, LockManager, LockMode
from txn.transaction import (
    Transaction,
    TransactionManager,
    TransactionState,
    undo,
)

WRITE_STATEMENTS = (
    InsertStatement,
    UpdateStatement,
    DeleteStatement,
    CreateTableStatement,
    CreateTableFromFileStatement,
    CreateIndexStatement,
    DropTableStatement,
    DropIndexStatement,
)


class SessionError(Exception):
    """La sentencia no encaja con el estado de la sesión."""


class Session:
    """Ejecuta SQL bajo control de transacciones y de bloqueos."""

    def __init__(self, engine: Engine, locks: LockManager, manager: TransactionManager) -> None:
        self._engine = engine
        self._locks = locks
        self._manager = manager
        self._current: Transaction | None = None

    @property
    def in_transaction(self) -> bool:
        return self._current is not None

    @property
    def transaction(self) -> Transaction | None:
        return self._current

    def execute(self, sql: str) -> QueryResult:
        """Ejecuta una sentencia, atendiendo también BEGIN, COMMIT y ROLLBACK.

        Raises:
            SessionError: si la sentencia no encaja con el estado de la sesión.
            DeadlockError: si esperar un bloqueo cerraría un ciclo; la transacción se aborta.
        """
        statements = parse_script(sql)
        if len(statements) != 1:
            raise SessionError(f"se esperaba una sentencia y llegaron {len(statements)}")
        return self.run(statements[0])

    def run(self, statement: Statement) -> QueryResult:
        if isinstance(statement, BeginTransactionStatement):
            return self._begin()
        if isinstance(statement, CommitTransactionStatement):
            return self._commit()
        if isinstance(statement, RollbackTransactionStatement):
            return self._rollback()
        return self._run_in_transaction(statement)

    def close(self) -> None:
        """Aborta lo que quede abierto y suelta los bloqueos."""
        if self._current is not None:
            self._rollback()

    def __enter__(self) -> Session:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _begin(self) -> QueryResult:
        if self._current is not None:
            raise SessionError("ya hay una transacción abierta en esta sesión")
        self._current = self._manager.begin()
        return QueryResult(message=f"transacción {self._current.identifier} iniciada")

    def _commit(self) -> QueryResult:
        transaction = self._require_transaction()
        self._manager.finish(transaction, TransactionState.COMMITTED)
        self._locks.release_all(transaction.identifier)
        self._current = None
        return QueryResult(
            message=f"transacción {transaction.identifier} confirmada "
            f"({len(transaction.changes)} cambio(s))"
        )

    def _rollback(self) -> QueryResult:
        transaction = self._require_transaction()
        undone = undo(transaction, self._tables_of(transaction))
        self._manager.finish(transaction, TransactionState.ABORTED)
        self._locks.release_all(transaction.identifier)
        self._current = None
        return QueryResult(
            message=f"transacción {transaction.identifier} abortada ({undone} cambio(s) deshechos)"
        )

    def _run_in_transaction(self, statement: Statement) -> QueryResult:
        autocommit = self._current is None
        transaction = self._current or self._manager.begin()
        try:
            self._lock_for(statement, transaction)
            result = self._engine.run(statement, journal=transaction)
        except LockError:
            self._abort(transaction)
            raise
        except Exception:
            if autocommit:
                self._abort(transaction)
            raise
        if autocommit:
            self._manager.finish(transaction, TransactionState.COMMITTED)
            self._locks.release_all(transaction.identifier)
        else:
            self._current = transaction
        return result

    def _abort(self, transaction: Transaction) -> None:
        if transaction.is_active:
            undo(transaction, self._tables_of(transaction))
            self._manager.finish(transaction, TransactionState.ABORTED)
        self._locks.release_all(transaction.identifier)
        self._current = None

    def _lock_for(self, statement: Statement, transaction: Transaction) -> None:
        mode = LockMode.EXCLUSIVE if isinstance(statement, WRITE_STATEMENTS) else LockMode.SHARED
        for resource in sorted(_resources_of(statement)):
            self._locks.acquire(transaction.identifier, resource, mode)

    def _tables_of(self, transaction: Transaction) -> dict[str, Table]:
        return {
            name.lower(): self._engine.table(name) for name in transaction.tables_touched()
        }

    def _require_transaction(self) -> Transaction:
        if self._current is None:
            raise SessionError("no hay ninguna transacción abierta")
        return self._current


def _resources_of(statement: Statement) -> set[str]:
    """Tablas que la sentencia toca, que son los recursos que hay que bloquear."""
    if isinstance(statement, SelectStatement):
        return {statement.source.name.lower(), *(j.table.name.lower() for j in statement.joins)}
    for attribute in ("table", "name"):
        value = getattr(statement, attribute, None)
        if isinstance(value, str):
            return {value.lower()}
    return set()
