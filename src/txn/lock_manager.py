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
"""

from __future__ import annotations

import threading
from enum import Enum, unique

from config import EngineConfig

Resource = str
TransactionId = int


class LockError(Exception):
    """Base de los errores de bloqueo."""


class DeadlockError(LockError):
    """Conceder el bloqueo cerraría un ciclo de esperas."""


class LockTimeoutError(LockError):
    """Se agotó la espera por el bloqueo."""


@unique
class LockMode(Enum):
    SHARED = "S"
    EXCLUSIVE = "X"


class LockManager:
    """Concede y libera bloqueos de tabla, abortando a quien provoque un interbloqueo."""

    def __init__(self, config: EngineConfig) -> None:
        self._timeout = config.lock_timeout_seconds
        self._condition = threading.Condition()
        self._holders: dict[Resource, dict[TransactionId, LockMode]] = {}
        self._waiting: dict[TransactionId, Resource] = {}
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
            self._waiting[transaction] = resource
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
        while self._conflicts(transaction, resource, mode):
            if self._closes_a_cycle(transaction):
                self.deadlocks_detected += 1
                raise DeadlockError(
                    f"la transacción {transaction} provocaría un interbloqueo en '{resource}'"
                )
            if not self._condition.wait(timeout=self._timeout):
                raise LockTimeoutError(
                    f"la transacción {transaction} esperó demasiado por '{resource}'"
                )

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

    def _conflicts(self, transaction: TransactionId, resource: Resource, mode: LockMode) -> bool:
        others = {
            holder: held
            for holder, held in self._holders.get(resource, {}).items()
            if holder != transaction
        }
        if not others:
            return False
        if mode is LockMode.EXCLUSIVE:
            return True
        return any(held is LockMode.EXCLUSIVE for held in others.values())

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
        resource = self._waiting.get(transaction)
        if resource is None:
            return []
        return [holder for holder in self._holders.get(resource, {}) if holder != transaction]
