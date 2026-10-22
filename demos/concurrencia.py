"""Demostración de concurrencia: varias transacciones compitiendo por las mismas filas.

Ejecutar con (requiere `pip install -e .`):

    .venv/bin/python demos/concurrencia.py

Muestra tres escenas:

1. **Sin control**: varios hilos leen el mismo saldo, esperan un poco y escriben lo que
   leyeron menos uno. Las lecturas se pisan y se pierden descuentos (*race condition*).
2. **Con transacciones**: los mismos hilos y la misma carga, ahora dentro de
   `BEGIN … COMMIT`. El bloqueo en dos fases impide que dos hilos escriban sobre una
   lectura vieja: cuando dos leen a la vez y los dos quieren escribir, el gestor detecta el
   interbloqueo y aborta a uno, que lo vuelve a intentar. El total se conserva.
3. **Interbloqueo**: dos transacciones se piden mutuamente lo que la otra tiene. El gestor
   detecta el ciclo y aborta a una para que la otra pueda terminar.
"""

from __future__ import annotations

import argparse
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from query.engine import Engine
from storage.types import Value
from txn import DeadlockError, LockManager, Session, TransactionManager

DEFAULT_THREADS = 4
DEFAULT_ROUNDS = 25
INITIAL_BALANCE = 1000
DEADLOCK_PAUSE_SECONDS = 0.2
RACE_PAUSE_SECONDS = 0.001
READ_BALANCE = "SELECT saldo FROM cuentas WHERE id = 1"


@dataclass(frozen=True, slots=True)
class Balance:
    """Cómo terminó el saldo de una escena: lo esperado, lo obtenido y los reintentos."""

    expected: int
    obtained: int
    retries: int = 0

    @property
    def correct(self) -> bool:
        return self.obtained == self.expected


def build_engine(directory: Path) -> tuple[Engine, LockManager, TransactionManager]:
    config = EngineConfig(data_directory=directory)
    return Engine(config), LockManager(config), TransactionManager()


def prepare(session: Session) -> None:
    """Deja las cuentas en su estado inicial, aunque `--data-dir` sea de una ejecución
    anterior."""
    session.execute("CREATE TABLE IF NOT EXISTS cuentas (id INT PRIMARY KEY, saldo INT)")
    session.execute("DELETE FROM cuentas")
    session.execute(f"INSERT INTO cuentas VALUES (1, {INITIAL_BALANCE}), (2, 0)")


def balance_of(value: Value) -> int:
    """Un saldo leído de la tabla, que siempre es un entero."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"se esperaba un saldo entero y llegó {value!r}")
    return value


def read_balance(session: Session) -> int:
    return balance_of(session.execute(READ_BALANCE).rows[0][0])


def withdraw_one(session: Session) -> None:
    """La carga de las dos primeras escenas: leer, pensar un instante y escribir."""
    current = read_balance(session)
    time.sleep(RACE_PAUSE_SECONDS)
    session.execute(f"UPDATE cuentas SET saldo = {current - 1} WHERE id = 1")


def reset_balance(session: Session) -> None:
    session.execute(f"UPDATE cuentas SET saldo = {INITIAL_BALANCE} WHERE id = 1")


def scene_race_condition(
    engine: Engine, locks: LockManager, manager: TransactionManager, threads: int, rounds: int
) -> Balance:
    """Cada hilo lee el saldo, espera un poco y escribe lo que había leído menos uno."""
    print("\n1) Sin transacción: lectura y escritura separadas")
    reset_balance(Session(engine, locks, manager))

    def worker() -> None:
        session = Session(engine, locks, manager)
        for _ in range(rounds):
            withdraw_one(session)

    run_threads(worker, threads)
    obtained = read_balance(Session(engine, locks, manager))
    result = Balance(INITIAL_BALANCE - threads * rounds, obtained)
    verdict = "correcto" if result.correct else "PERDIDAS por race condition"
    print(f"   esperado {result.expected}, obtenido {result.obtained} → {verdict}")
    return result


def scene_transactions(
    engine: Engine, locks: LockManager, manager: TransactionManager, threads: int, rounds: int
) -> Balance:
    """La misma carga, cada descuento dentro de una transacción que se reintenta si el gestor
    la aborta por interbloqueo."""
    print("\n2) Con transacción: la misma lectura y escritura dentro de BEGIN … COMMIT")
    reset_balance(Session(engine, locks, manager))
    retries: list[int] = []

    def worker() -> None:
        session = Session(engine, locks, manager)
        for _ in range(rounds):
            retries.append(withdraw_in_transaction(session))

    run_threads(worker, threads)
    result = Balance(
        INITIAL_BALANCE - threads * rounds,
        read_balance(Session(engine, locks, manager)),
        sum(retries),
    )
    verdict = "correcto" if result.correct else "PERDIDAS"
    print(
        f"   esperado {result.expected}, obtenido {result.obtained} → {verdict}; "
        f"{result.retries} transacción(es) abortadas por interbloqueo y reintentadas"
    )
    return result


def withdraw_in_transaction(session: Session) -> int:
    """Un descuento como transacción. Devuelve cuántas veces hubo que reintentarlo.

    Dos hilos que leyeron el saldo a la vez tienen los dos un bloqueo compartido, y los dos
    quieren pasarlo a exclusivo para escribir: cada uno espera al otro. El gestor lo detecta
    y aborta a uno —con lo que suelta su bloqueo—, y ese uno lo vuelve a intentar.
    """
    attempts = 0
    while True:
        try:
            session.execute("BEGIN TRANSACTION")
            withdraw_one(session)
            session.execute("COMMIT")
            return attempts
        except DeadlockError:
            attempts += 1


def scene_deadlock(engine: Engine, locks: LockManager, manager: TransactionManager) -> list[str]:
    """Dos transacciones bloquean dos tablas en orden inverso."""
    print("\n3) Interbloqueo: dos transacciones en orden cruzado")
    session = Session(engine, locks, manager)
    session.execute("CREATE TABLE IF NOT EXISTS otra (id INT PRIMARY KEY, saldo INT)")
    session.execute("DELETE FROM otra")
    session.execute("INSERT INTO otra VALUES (1, 0)")
    outcomes: list[str] = []

    def worker(name: str, first: str, second: str) -> None:
        worker_session = Session(engine, locks, manager)
        try:
            worker_session.execute("BEGIN")
            worker_session.execute(f"UPDATE {first} SET saldo = saldo + 1 WHERE id = 1")
            time.sleep(DEADLOCK_PAUSE_SECONDS)
            worker_session.execute(f"UPDATE {second} SET saldo = saldo + 1 WHERE id = 1")
            worker_session.execute("COMMIT")
            outcomes.append(f"{name}: confirmada")
        except DeadlockError as error:
            worker_session.close()
            outcomes.append(f"{name}: abortada por interbloqueo ({error})")

    threads = [
        threading.Thread(target=worker, args=("A", "cuentas", "otra")),
        threading.Thread(target=worker, args=("B", "otra", "cuentas")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for line in sorted(outcomes):
        print(f"   {line}")
    print(f"   interbloqueos detectados en total: {locks.deadlocks_detected}")
    return sorted(outcomes)


def run_threads(worker: Callable[[], None], count: int) -> None:
    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--data-dir", type=Path, default=None)
    arguments = parser.parse_args()
    directory = arguments.data_dir or Path(tempfile.mkdtemp(prefix="minigestor-demo-"))
    engine, locks, manager = build_engine(directory)
    print(f"Datos en {directory}")
    with engine:
        prepare(Session(engine, locks, manager))
        scene_race_condition(engine, locks, manager, arguments.threads, arguments.rounds)
        scene_transactions(engine, locks, manager, arguments.threads, arguments.rounds)
        scene_deadlock(engine, locks, manager)


if __name__ == "__main__":
    main()
