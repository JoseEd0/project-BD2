"""Contrato para enterarse de qué filas cambia una sentencia.

El motor no sabe qué son las transacciones: solo avisa de cada cambio a quien se lo pida.
El gestor de transacciones implementa este protocolo para construir su registro de deshacer.
"""

from __future__ import annotations

from typing import Protocol

from storage.record import Record


class Journal(Protocol):
    """Recibe una notificación por cada fila que una sentencia crea, borra o modifica."""

    def record_insert(self, table: str, row: Record) -> None: ...

    def record_delete(self, table: str, row: Record) -> None: ...

    def record_update(self, table: str, before: Record, after: Record) -> None: ...
