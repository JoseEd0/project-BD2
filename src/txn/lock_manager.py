"""Gestor de bloqueos con detección de interbloqueos.

Los bloqueos son a nivel de tabla: es la granularidad que hace visible el problema sin
convertir la demostración en un ejercicio de contabilidad de cerrojos.

Reglas de compatibilidad:

|            | mantiene S | mantiene X |
|------------|-----------|-----------|
| **pide S** | compatible | espera     |
| **pide X** | espera     | espera     |

Cuando una transacción tendría que esperar, antes se busca un ciclo en el **grafo de
espera**. Si lo hay, esperar sería esperar para siempre, así que se aborta a quien lo cerró.

Un pedido compartido nuevo espera además detrás de los exclusivos que llegaron antes que él
al mismo recurso. Sin esa regla, mientras quede algún lector, un escritor nunca entra: los
lectores que llegan se suman a los que ya están, y el escritor espera hasta agotar su plazo
(*inanición del escritor*). Pasar de compartido a exclusivo no hace cola: quien ya tiene el
recurso no espera detrás de quien todavía no lo tiene.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass
from enum import Enum, unique

from config import EngineConfig
from errors import DomainError

Resource = str
TransactionId = int


class LockError(DomainError):
    """Base de los errores de bloqueo."""


class DeadlockError(LockError):
    """Conceder el bloqueo cerraría un ciclo de esperas."""


class LockTimeoutError(LockError):
    """Se agotó la espera por el bloqueo."""


@unique
class LockMode(Enum):
    SHARED = "S"
    EXCLUSIVE = "X"


@dataclass(frozen=True, slots=True)
class _Request:
    """Un pedido que espera: qué recurso, en qué modo y en qué turno llegó."""

    resource: Resource
    mode: LockMode
    turn: int


class LockManager:
    """Concede y libera bloqueos de tabla, abortando a quien provoque un interbloqueo."""

    def __init__(self, config: EngineConfig) -> None:
        self._timeout = config.lock_timeout_seconds
        self._condition = threading.Condition()
        self._holders: dict[Resource, dict[TransactionId, LockMode]] = {}
        self._waiting: dict[TransactionId, _Request] = {}
        self._turns = itertools.count()
        self.deadlocks_detected = 0

    def acquire(self, transaction: TransactionId, resource: Resource, mode: LockMode) -> None:
        """Bloquea el recurso, esperando si hace falta.

        Raises:
            DeadlockError: si esperar cerraría un ciclo en el grafo de espera.
            LockTimeoutError: si se agota `lock_timeout_seconds`.
        """
        with self._condition:
            if self._already_granted(transaction, resource, mode):
                return
            self._waiting[transaction] = _Request(resource, mode, next(self._turns))
            try:
                self._wait_until_free(transaction, resource, mode)
            finally:
                self._waiting.pop(transaction, None)
            self._holders.setdefault(resource, {})[transaction] = self._merged_mode(
                transaction, resource, mode
            )

    def release_all(self, transaction: TransactionId) -> None:
        """Suelta todos los bloqueos de la transacción y despierta a los que esperan."""
        with self._condition:
            for holders in self._holders.values():
                holders.pop(transaction, None)
            self._holders = {
                resource: holders for resource, holders in self._holders.items() if holders
            }
            self._waiting.pop(transaction, None)
            self._condition.notify_all()

    def holders_of(self, resource: Resource) -> dict[TransactionId, LockMode]:
        with self._condition:
            return dict(self._holders.get(resource, {}))

    def _wait_until_free(
        self, transaction: TransactionId, resource: Resource, mode: LockMode
    ) -> None:
        """Espera hasta que el bloqueo se pueda conceder, con un plazo fijo.

        El plazo se calcula una sola vez: cada liberación, aunque sea de otro recurso,
        despierta a todos los que esperan, y volver a esperar el tiempo completo tras cada
        aviso dejaría esperando para siempre a quien nunca consigue su bloqueo.
        """
        deadline = time.monotonic() + self._timeout
        while self._blockers_of(transaction):
            if self._closes_a_cycle(transaction):
                self.deadlocks_detected += 1
                raise DeadlockError(
                    f"la transacción {transaction} provocaría un interbloqueo en '{resource}'"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LockTimeoutError(
                    f"la transacción {transaction} esperó demasiado por '{resource}'"
                )
            self._condition.wait(timeout=remaining)

    def _already_granted(
        self, transaction: TransactionId, resource: Resource, mode: LockMode
    ) -> bool:
        held = self._holders.get(resource, {}).get(transaction)
        return held is LockMode.EXCLUSIVE or held is mode

    def _merged_mode(
        self, transaction: TransactionId, resource: Resource, mode: LockMode
    ) -> LockMode:
        """Un ascenso de compartido a exclusivo se queda con el más fuerte."""
        held = self._holders.get(resource, {}).get(transaction)
        if held is LockMode.EXCLUSIVE or mode is LockMode.EXCLUSIVE:
            return LockMode.EXCLUSIVE
        return LockMode.SHARED

    def _closes_a_cycle(self, transaction: TransactionId) -> bool:
        """Recorre el grafo de espera desde `transaction` buscando volver a ella."""
        visited: set[TransactionId] = set()
        pending = list(self._blockers_of(transaction))
        while pending:
            current = pending.pop()
            if current == transaction:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(self._blockers_of(current))
        return False

    def _blockers_of(self, transaction: TransactionId) -> list[TransactionId]:
        """Las transacciones por las que espera esta: las que tienen el recurso en un modo
        incompatible y, si pide compartido sin tener nada, los exclusivos que llegaron antes.

        Son las aristas del grafo de espera, así que tienen que ser exactas: una de más
        abortaría a una transacción que no estaba en ningún ciclo.
        """
        request = self._waiting.get(transaction)
        if request is None:
            return []
        holders = self._holders.get(request.resource, {})
        blockers = [
            holder
            for holder, held in holders.items()
            if holder != transaction
            and (request.mode is LockMode.EXCLUSIVE or held is LockMode.EXCLUSIVE)
        ]
        if request.mode is LockMode.SHARED and transaction not in holders:
            blockers.extend(self._exclusive_queue_before(request, transaction))
        return blockers

    def _exclusive_queue_before(
        self, request: _Request, transaction: TransactionId
    ) -> list[TransactionId]:
        return [
            other
            for other, waiting in self._waiting.items()
            if other != transaction
            and waiting.resource == request.resource
            and waiting.mode is LockMode.EXCLUSIVE
            and waiting.turn < request.turn
        ]
