"""Transacciones: agrupar sentencias y poder deshacerlas.

Cada transacción lleva un **registro de deshacer**: la lista de cambios que hizo, en orden.
Confirmar es tirar esa lista; abortar es recorrerla al revés aplicando el cambio inverso.

| Cambio original | Cómo se deshace |
|---|---|
| INSERT de una fila | se borra esa fila |
| DELETE de una fila | se vuelve a insertar |
| UPDATE de una fila | se restaura el valor anterior |
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum, unique

from query.table import Table
from storage.record import Record


class TransactionError(Exception):
    """Operación inválida sobre una transacción."""


@unique
class TransactionState(Enum):
    ACTIVE = "activa"
    COMMITTED = "confirmada"
    ABORTED = "abortada"


@unique
class ChangeKind(Enum):
    INSERT = "insert"
    DELETE = "delete"
    UPDATE = "update"


@dataclass(frozen=True, slots=True)
class Change:
    """Un cambio anotado en el registro de deshacer."""

    kind: ChangeKind
    table: str
    before: Record | None
    after: Record | None


@dataclass
class Transaction:
    """Una transacción activa y lo que ha cambiado hasta ahora.

    Implementa el protocolo `Journal` del motor: el motor le va contando cada fila que
    toca, sin saber que del otro lado hay una transacción.
    """

    identifier: int
    state: TransactionState = TransactionState.ACTIVE
    changes: list[Change] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.state is TransactionState.ACTIVE

    def record_insert(self, table: str, row: Record) -> None:
        self._append(Change(ChangeKind.INSERT, table, before=None, after=row))

    def record_delete(self, table: str, row: Record) -> None:
        self._append(Change(ChangeKind.DELETE, table, before=row, after=None))

    def record_update(self, table: str, before: Record, after: Record) -> None:
        self._append(Change(ChangeKind.UPDATE, table, before=before, after=after))

    def tables_touched(self) -> set[str]:
        return {change.table for change in self.changes}

    def _append(self, change: Change) -> None:
        if not self.is_active:
            raise TransactionError(f"la transacción {self.identifier} ya no está activa")
        self.changes.append(change)


def undo(transaction: Transaction, tables: dict[str, Table]) -> int:
    """Aplica los cambios inversos en orden inverso. Devuelve cuántos deshizo.

    Raises:
        TransactionError: si falta alguna de las tablas afectadas.
    """
    undone = 0
    for change in reversed(transaction.changes):
        table = tables.get(change.table.lower())
        if table is None:
            raise TransactionError(f"no se puede deshacer: falta la tabla '{change.table}'")
        undone += int(_revert(change, table))
    return undone


def _revert(change: Change, table: Table) -> bool:
    if change.kind is ChangeKind.INSERT and change.after is not None:
        return table.delete_first(change.after)
    if change.kind is ChangeKind.DELETE and change.before is not None:
        table.insert(change.before)
        return True
    if change.after is not None and change.before is not None:
        return table.update_first(change.after, change.before)
    return False


class TransactionManager:
    """Reparte identificadores y lleva la cuenta de las transacciones abiertas."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._active: dict[int, Transaction] = {}

    @property
    def active_count(self) -> int:
        return len(self._active)

    def begin(self) -> Transaction:
        transaction = Transaction(identifier=next(self._counter))
        self._active[transaction.identifier] = transaction
        return transaction

    def finish(self, transaction: Transaction, state: TransactionState) -> None:
        """Cierra la transacción dejándola confirmada o abortada.

        Raises:
            TransactionError: si ya estaba cerrada.
        """
        if not transaction.is_active:
            raise TransactionError(f"la transacción {transaction.identifier} ya está cerrada")
        transaction.state = state
        self._active.pop(transaction.identifier, None)
