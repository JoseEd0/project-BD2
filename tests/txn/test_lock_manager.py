import threading

import pytest

from config import EngineConfig
from txn.lock_manager import (
    DeadlockError,
    LockError,
    LockManager,
    LockMode,
    LockTimeoutError,
)

FIRST = 1
SECOND = 2
THIRD = 3
TABLE = "cuentas"
OTHER = "otra"


def test_two_shared_locks_coexist(locks: LockManager):
    locks.acquire(FIRST, TABLE, LockMode.SHARED)
    locks.acquire(SECOND, TABLE, LockMode.SHARED)
    assert set(locks.holders_of(TABLE)) == {FIRST, SECOND}


def test_acquiring_twice_is_harmless(locks: LockManager):
    locks.acquire(FIRST, TABLE, LockMode.SHARED)
    locks.acquire(FIRST, TABLE, LockMode.SHARED)
    assert locks.holders_of(TABLE) == {FIRST: LockMode.SHARED}


def test_an_exclusive_lock_upgrades_a_shared_one(locks: LockManager):
    locks.acquire(FIRST, TABLE, LockMode.SHARED)
    locks.acquire(FIRST, TABLE, LockMode.EXCLUSIVE)
    assert locks.holders_of(TABLE) == {FIRST: LockMode.EXCLUSIVE}


def test_releasing_frees_the_resource(locks: LockManager):
    locks.acquire(FIRST, TABLE, LockMode.EXCLUSIVE)
    locks.release_all(FIRST)
    locks.acquire(SECOND, TABLE, LockMode.EXCLUSIVE)
    assert locks.holders_of(TABLE) == {SECOND: LockMode.EXCLUSIVE}


def test_an_exclusive_lock_makes_others_wait(locks: LockManager):
    locks.acquire(FIRST, TABLE, LockMode.EXCLUSIVE)
    started = threading.Event()
    acquired = threading.Event()

    def waiter() -> None:
        started.set()
        locks.acquire(SECOND, TABLE, LockMode.SHARED)
        acquired.set()

    thread = threading.Thread(target=waiter)
    thread.start()
    started.wait()
    assert not acquired.wait(timeout=0.1)
    locks.release_all(FIRST)
    assert acquired.wait(timeout=2)
    thread.join()


def test_timeout_when_nobody_releases(quick_config: EngineConfig):
    impatient = LockManager(
        EngineConfig(lock_timeout_seconds=0.05, data_directory=quick_config.data_directory)
    )
    impatient.acquire(FIRST, TABLE, LockMode.EXCLUSIVE)
    with pytest.raises(LockTimeoutError):
        impatient.acquire(SECOND, TABLE, LockMode.EXCLUSIVE)


def test_a_cycle_is_detected_as_a_deadlock(locks: LockManager):
    locks.acquire(FIRST, TABLE, LockMode.EXCLUSIVE)
    locks.acquire(SECOND, OTHER, LockMode.EXCLUSIVE)
    blocked = threading.Event()

    def first_waits() -> None:
        try:
            locks.acquire(FIRST, OTHER, LockMode.EXCLUSIVE)
        except DeadlockError:
            pass
        finally:
            blocked.set()

    thread = threading.Thread(target=first_waits)
    thread.start()
    threading.Event().wait(0.1)
    with pytest.raises(DeadlockError):
        locks.acquire(SECOND, TABLE, LockMode.EXCLUSIVE)
    assert locks.deadlocks_detected >= 1
    locks.release_all(FIRST)
    locks.release_all(SECOND)
    blocked.wait(timeout=2)
    thread.join()


def test_a_shared_wait_graph_without_a_cycle_is_not_a_deadlock(locks: LockManager):
    """Dos transacciones comparten una tabla y las dos esperan a una tercera. Quien espere
    a esas dos llega a la tercera por dos caminos, pero no vuelve a sí misma: tiene que
    esperar, no abortar."""
    fourth = 4
    locks.acquire(FIRST, TABLE, LockMode.SHARED)
    locks.acquire(SECOND, TABLE, LockMode.SHARED)
    locks.acquire(THIRD, OTHER, LockMode.EXCLUSIVE)
    outcomes: dict[int, str] = {}

    def attempt(transaction: int, resource: str, mode: LockMode) -> None:
        try:
            locks.acquire(transaction, resource, mode)
            outcomes[transaction] = "acquired"
        except LockError as error:
            outcomes[transaction] = type(error).__name__

    readers = [
        threading.Thread(target=attempt, args=(holder, OTHER, LockMode.SHARED))
        for holder in (FIRST, SECOND)
    ]
    for reader in readers:
        reader.start()
    threading.Event().wait(0.05)
    writer = threading.Thread(target=attempt, args=(fourth, TABLE, LockMode.EXCLUSIVE))
    writer.start()
    threading.Event().wait(0.1)
    assert outcomes == {}
    assert locks.deadlocks_detected == 0
    locks.release_all(THIRD)
    for reader in readers:
        reader.join()
    locks.release_all(FIRST)
    locks.release_all(SECOND)
    writer.join()
    assert outcomes == {FIRST: "acquired", SECOND: "acquired", fourth: "acquired"}


def test_the_timeout_is_a_deadline_that_unrelated_releases_do_not_extend(
    quick_config: EngineConfig,
):
    """Cada liberación despierta a todos los que esperan. Si al despertar se volviera a
    esperar el plazo entero, una transacción que nunca consigue su bloqueo esperaría para
    siempre mientras otras sigan soltando los suyos."""
    timeout = 0.3
    impatient = LockManager(
        EngineConfig(lock_timeout_seconds=timeout, data_directory=quick_config.data_directory)
    )
    impatient.acquire(FIRST, TABLE, LockMode.EXCLUSIVE)
    stop = threading.Event()
    outcome: list[type[Exception]] = []

    def churn_another_resource() -> None:
        while not stop.is_set():
            impatient.acquire(THIRD, OTHER, LockMode.EXCLUSIVE)
            impatient.release_all(THIRD)
            stop.wait(timeout / 10)

    def wait_for_the_table() -> None:
        try:
            impatient.acquire(SECOND, TABLE, LockMode.EXCLUSIVE)
        except LockError as error:
            outcome.append(type(error))

    churner = threading.Thread(target=churn_another_resource)
    waiter = threading.Thread(target=wait_for_the_table)
    churner.start()
    waiter.start()
    waiter.join(timeout=timeout * 5)
    stop.set()
    churner.join()
    still_waiting = waiter.is_alive()
    impatient.release_all(FIRST)
    waiter.join()
    assert not still_waiting
    assert outcome == [LockTimeoutError]
