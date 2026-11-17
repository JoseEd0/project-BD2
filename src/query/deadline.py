"""Plazo de una sentencia: el ejecutor lo mira entre fila y fila y corta la que se pasa.

Un hilo de Python no se puede matar desde fuera sin dejar páginas y bloqueos a medias, así
que la cancelación es cooperativa, como el `CHECK_FOR_INTERRUPTS` de PostgreSQL: quien
ejecuta la sentencia instala un `Deadline` con `statement_deadline`, y cada operador
Volcano lo compara con el reloj que ya lee para medirse. Vencido, lanza la excepción y la
sesión deshace la sentencia como la de cualquier otro error.

El plazo viaja en una variable de contexto y no como argumento: así ni el planificador ni
los veinte operadores tienen que pasárselo de mano en mano, y el motor usado sin API
(pruebas, experimentos) no tiene plazo.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar

from errors import DomainError

Item = TypeVar("Item")


class StatementTimeoutError(DomainError):
    """La sentencia pasó de su tiempo máximo y se cortó; lo que hubiera cambiado se deshizo."""


class StatementCancelledError(Exception):
    """Alguien de fuera canceló la sentencia, por ejemplo el servidor al apagarse.

    No deriva de `DomainError`: no es culpa de lo que se pidió.
    """


class Deadline:
    """Instante en que vence una sentencia, en el reloj de `time.perf_counter`.

    `expires_at` es un atributo y no un método porque se compara por cada fila de cada
    operador: una llamada ahí se notaría en el tiempo de las consultas.
    """

    def __init__(self, limit_seconds: float | None) -> None:
        self.limit_seconds = limit_seconds
        self.expires_at = (
            math.inf if limit_seconds is None else time.perf_counter() + limit_seconds
        )
        self._cancel_reason: str | None = None

    def cancel(self, reason: str) -> None:
        """Hace que la sentencia se corte en la próxima fila; se puede llamar desde otro hilo."""
        self._cancel_reason = reason
        self.expires_at = -math.inf

    def check(self) -> None:
        """Raises: lo mismo que `expired`, si el plazo ya venció."""
        if time.perf_counter() > self.expires_at:
            raise self.expired()

    def expired(self) -> StatementTimeoutError | StatementCancelledError:
        """El error que explica por qué se cortó la sentencia."""
        if self._cancel_reason is not None:
            return StatementCancelledError(f"sentencia cancelada: {self._cancel_reason}")
        return StatementTimeoutError(
            f"la sentencia pasó de statement_timeout ({self.limit_seconds:g} s) y se canceló"
        )


_UNLIMITED = Deadline(None)
_current: ContextVar[Deadline] = ContextVar("statement_deadline", default=_UNLIMITED)


def current_deadline() -> Deadline:
    """El plazo de la sentencia en curso; sin `statement_deadline`, uno que nunca vence."""
    return _current.get()


@contextmanager
def statement_deadline(deadline: Deadline) -> Iterator[Deadline]:
    """Instala `deadline` para lo que se ejecute dentro del bloque, en este hilo."""
    token = _current.set(deadline)
    try:
        yield deadline
    finally:
        _current.reset(token)


def within_deadline(items: Iterable[Item]) -> Iterator[Item]:
    """`items`, comprobando el plazo antes de cada uno: para los recorridos que no pasan
    por un operador, como el que busca las filas de un UPDATE o un DELETE."""
    deadline = current_deadline()
    for item in items:
        deadline.check()
        yield item
