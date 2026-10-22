"""Sesión: la puerta por la que un usuario ejecuta SQL con transacciones y bloqueos.

Separación de responsabilidades:

* el **motor** ejecuta sentencias y no sabe qué es una transacción;
* la **sesión** decide qué tablas hay que bloquear, en qué modo, y qué hacer si algo falla.

Fuera de una transacción explícita cada sentencia va en *autocommit*: se bloquea, se
ejecuta y se suelta. Dentro de `BEGIN ... COMMIT`, los bloqueos se conservan hasta el final
—**bloqueo en dos fases**—, que es lo que impide que otra transacción vea a medias lo que
esta está cambiando.

Al confirmar, las páginas que la transacción modificó se escriben en disco antes de soltar
los bloqueos (política *force*): lo confirmado sobrevive a que el proceso termine sin
cerrar el motor. No hay recuperación ante una caída a mitad de transacción; eso pediría un
log de escritura anticipada.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from types import TracebackType
from typing import TypeVar

from errors import DomainError
from query.engine import Engine, QueryResult
from query.table import Table
from sql import parse_script
from sql.nodes import (
    BeginTransactionStatement,
    CommitTransactionStatement,
    DefinitionStatement,
    DeleteStatement,
    DropIndexStatement,
    ExplainStatement,
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
    undo_statement,
)

WRITE_STATEMENTS = (InsertStatement, UpdateStatement, DeleteStatement, DefinitionStatement)
Read = TypeVar("Read")


class SessionError(DomainError):
    """La sentencia no encaja con el estado de la sesión."""


class DefinitionInTransactionError(SessionError):
    """Se pidió un CREATE o un DROP dentro de una transacción explícita.

    El registro de deshacer guarda filas, no archivos: un ROLLBACK no podría devolver una
    tabla borrada ni quitar un índice creado. Como en Oracle o MySQL, el DDL va fuera de
    las transacciones; la transacción abierta sigue intacta.
    """


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
            DefinitionInTransactionError: si es DDL y hay una transacción abierta.
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
        if self._current is not None and isinstance(statement, DefinitionStatement):
            raise DefinitionInTransactionError(
                "CREATE y DROP no se admiten dentro de una transacción: confírmala o "
                "abórtala antes; la transacción sigue abierta"
            )
        return self._run_in_transaction(statement)

    def inspect(self, tables: Iterable[str], read: Callable[[], Read]) -> Read:
        """Hace una lectura que no es SQL —la estructura física de una tabla, los puntos
        que dibuja el mapa— con los mismos bloqueos compartidos que tomaría un SELECT.

        Recorrer las páginas de una tabla mientras otra transacción las modifica daría
        una estructura a medio cambiar; con el bloqueo, la lectura espera a que termine.
        Dentro de una transacción los bloqueos se conservan hasta el final, como siempre.

        Raises:
            LockError: si el bloqueo no llega; una transacción abierta queda abortada.
        """
        autocommit = self._current is None
        transaction = self._current or self._manager.begin()
        try:
            for resource in sorted({name.lower() for name in tables}):
                self._locks.acquire(transaction.identifier, resource, LockMode.SHARED)
            return read()
        except LockError:
            self._abort(transaction)
            raise
        finally:
            if autocommit:
                self._end(transaction, TransactionState.COMMITTED)

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
        self._confirm(transaction)
        return QueryResult(
            message=f"transacción {transaction.identifier} confirmada "
            f"({len(transaction.changes)} cambio(s))"
        )

    def _rollback(self) -> QueryResult:
        transaction = self._require_transaction()
        try:
            undone = undo(transaction, self._tables_of(transaction))
        finally:
            self._end(transaction, TransactionState.ABORTED)
        return QueryResult(
            message=f"transacción {transaction.identifier} abortada ({undone} cambio(s) deshechos)"
        )

    def _run_in_transaction(self, statement: Statement) -> QueryResult:
        autocommit = self._current is None
        transaction = self._current or self._manager.begin()
        first_change = len(transaction.changes)
        try:
            self._lock_for(statement, transaction)
            result = self._engine.run(statement, journal=transaction)
        except LockError:
            self._abort(transaction)
            raise
        except Exception:
            if autocommit:
                self._abort(transaction)
            else:
                self._undo_failed_statement(transaction, first_change)
            raise
        if autocommit:
            self._confirm(transaction)
        else:
            self._current = transaction
        return result

    def _undo_failed_statement(self, transaction: Transaction, first_change: int) -> None:
        """Deshace solo la sentencia que falló; si ni eso se puede, aborta la transacción."""
        try:
            undo_statement(transaction, self._tables_of(transaction), first_change)
        except Exception:
            self._abort(transaction)
            raise

    def _confirm(self, transaction: Transaction) -> None:
        """Escribe en disco lo que la transacción cambió y solo entonces la da por confirmada.

        Si la escritura falla, la transacción se aborta: confirmarla prometería algo que el
        disco no tiene.
        """
        try:
            for table in self._tables_of(transaction).values():
                table.flush()
        except Exception:
            self._abort(transaction)
            raise
        self._end(transaction, TransactionState.COMMITTED)

    def _abort(self, transaction: Transaction) -> None:
        try:
            if transaction.is_active:
                undo(transaction, self._tables_of(transaction))
        finally:
            self._end(transaction, TransactionState.ABORTED)

    def _end(self, transaction: Transaction, state: TransactionState) -> None:
        """Cierra la transacción y suelta sus bloqueos pase lo que pase: un deshacer que
        falla no puede dejar tablas bloqueadas para siempre."""
        try:
            if transaction.is_active:
                self._manager.finish(transaction, state)
        finally:
            self._locks.release_all(transaction.identifier)
            self._current = None

    def _lock_for(self, statement: Statement, transaction: Transaction) -> None:
        mode = LockMode.EXCLUSIVE if isinstance(statement, WRITE_STATEMENTS) else LockMode.SHARED
        for resource in sorted(self._resources_of(statement)):
            self._locks.acquire(transaction.identifier, resource, mode)

    def _resources_of(self, statement: Statement) -> set[str]:
        """Tablas que la sentencia toca, que son los recursos que hay que bloquear."""
        if isinstance(statement, ExplainStatement):
            return self._resources_of(statement.query)
        if isinstance(statement, SelectStatement):
            joined = (join.table.name.lower() for join in statement.joins)
            return {statement.source.name.lower(), *joined}
        if isinstance(statement, DropIndexStatement):
            return self._table_of_index(statement)
        for attribute in ("table", "name"):
            value = getattr(statement, attribute, None)
            if isinstance(value, str):
                return {value.lower()}
        return set()

    def _table_of_index(self, statement: DropIndexStatement) -> set[str]:
        """La tabla dueña del índice: borrarlo cambia esa tabla, y es ella la que hay que
        bloquear. Si el índice no existe no hay nada que proteger; el motor dará el error."""
        table = statement.table or self._engine.table_of_index(statement.name)
        return set() if table is None else {table.lower()}

    def _tables_of(self, transaction: Transaction) -> dict[str, Table]:
        return {
            name.lower(): self._engine.table(name) for name in transaction.tables_touched()
        }

    def _require_transaction(self) -> Transaction:
        if self._current is None:
            raise SessionError("no hay ninguna transacción abierta")
        return self._current
