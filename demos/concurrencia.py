"""Demostración de concurrencia: varias transacciones compitiendo por las mismas filas.

Ejecutar con (requiere `pip install -e .`):

    .venv/bin/python demos/concurrencia.py

Muestra tres escenas:

1. **Sin control**: varios hilos leen y escriben el mismo saldo sin transacción. El
   resultado final es incorrecto porque las lecturas se pisan (*race condition*).
2. **Con transacciones**: los mismos hilos, ahora dentro de `BEGIN … COMMIT`. Los bloqueos
   serializan los accesos y el total se conserva.
3. **Interbloqueo**: dos transacciones se piden mutuamente lo que la otra tiene. El gestor
   detecta el ciclo y aborta a una para que la otra pueda terminar.
"""

from __future__ import annotations

import argparse
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from config import EngineConfig
from query.engine import Engine
from txn import DeadlockError, LockManager, Session, TransactionManager

DEFAULT_THREADS = 4
DEFAULT_ROUNDS = 25
INITIAL_BALANCE = 1000
DEADLOCK_PAUSE_SECONDS = 0.2
RACE_PAUSE_SECONDS = 0.001


def build_engine(directory: Path) -> tuple[Engine, LockManager, TransactionManager]:
    config = EngineConfig(data_directory=directory)
    return Engine(config), LockManager(config), TransactionManager()


def prepare(session: Session) -> None:
    session.execute("CREATE TABLE cuentas (id INT PRIMARY KEY, saldo INT)")
    session.execute(f"INSERT INTO cuentas VALUES (1, {INITIAL_BALANCE}), (2, 0)")


def total_of(session: Session) -> int:
    return int(session.execute("SELECT SUM(saldo) as total FROM cuentas").rows[0][0])


def scene_race_condition(engine: Engine, locks: LockManager, manager: TransactionManager,
                         threads: int, rounds: int) -> None:
    """Cada hilo lee el saldo, espera un poco y escribe lo que había leído menos uno."""
    print("\n1) Sin transacción: lectura y escritura separadas")

    def worker() -> None:
        session = Session(engine, locks, manager)
        for _ in range(rounds):
            current = session.execute("SELECT saldo FROM cuentas WHERE id = 1").rows[0][0]
            time.sleep(RACE_PAUSE_SECONDS)
            session.execute(f"UPDATE cuentas SET saldo = {int(current) - 1} WHERE id = 1")

    run_threads(worker, threads)
    session = Session(engine, locks, manager)
    final = session.execute("SELECT saldo FROM cuentas WHERE id = 1").rows[0][0]
    expected = INITIAL_BALANCE - threads * rounds
    print(f"   esperado {expected}, obtenido {final} → "
          f"{'correcto' if final == expected else 'PERDIDAS por race condition'}")


def scene_transactions(engine: Engine, locks: LockManager, manager: TransactionManager,
                       threads: int, rounds: int) -> None:
    """Los mismos hilos, ahora con la lectura y la escritura dentro de una transacción."""
    print("\n2) Con transacción: BEGIN … COMMIT")
    session = Session(engine, locks, manager)
    session.execute(f"UPDATE cuentas SET saldo = {INITIAL_BALANCE} WHERE id = 1")
    session.execute("UPDATE cuentas SET saldo = 0 WHERE id = 2")

    def worker() -> None:
        worker_session = Session(engine, locks, manager)
        for _ in range(rounds):
            worker_session.execute("BEGIN TRANSACTION")
            worker_session.execute("UPDATE cuentas SET saldo = saldo - 1 WHERE id = 1")
            worker_session.execute("UPDATE cuentas SET saldo = saldo + 1 WHERE id = 2")
            worker_session.execute("END TRANSACTION")

    run_threads(worker, threads)
    balances = session.execute("SELECT id, saldo FROM cuentas ORDER BY id").rows
    print(f"   saldos {balances}, total {total_of(session)} "
          f"(esperado {INITIAL_BALANCE})")


def scene_deadlock(engine: Engine, locks: LockManager, manager: TransactionManager) -> None:
    """Dos transacciones bloquean dos tablas en orden inverso."""
    print("\n3) Interbloqueo: dos transacciones en orden cruzado")
    session = Session(engine, locks, manager)
    session.execute("CREATE TABLE otra (id INT PRIMARY KEY, saldo INT)")
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
            outcomes.append(f"   {name}: confirmada")
        except DeadlockError as error:
            worker_session.close()
            outcomes.append(f"   {name}: abortada por interbloqueo ({error})")

    threads = [
        threading.Thread(target=worker, args=("A", "cuentas", "otra")),
        threading.Thread(target=worker, args=("B", "otra", "cuentas")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for line in sorted(outcomes):
        print(line)
    print(f"   interbloqueos detectados: {locks.deadlocks_detected}")


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
