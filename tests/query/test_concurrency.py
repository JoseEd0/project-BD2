"""Varias sesiones sobre un mismo motor: cada hilo debe ver solo lo suyo.

Los bloqueos de tabla dejan que dos lecturas corran a la vez, así que el motor no puede
compartir entre consultas nada que no sea de solo lectura: ni los archivos temporales del
ordenamiento y del hashing externos, ni el catálogo, ni las tablas abiertas.
"""

import sys
import threading
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest

from config import EngineConfig
from query.engine import Engine

ROWS_PER_TABLE = 300
REPETITIONS = 4
DDL_THREADS = 4
TABLES_PER_THREAD = 15
# Un cambio de hilo cada microsegundo hace que una carrera que en la práctica es rara
# aparezca en casi todas las ejecuciones.
AGGRESSIVE_SWITCH_INTERVAL = 1e-6
TEMPORARY_PREFIXES = ("sort-", "join-", "group-", "distinct-")
DISK_BOUND_QUERIES = {
    "sort": "SELECT tag FROM {t} ORDER BY id DESC",
    "join": "SELECT x.tag FROM {t} x JOIN {t} y ON x.id = y.id",
    "group": "SELECT id, COUNT(*) FROM {t} GROUP BY id",
}


@pytest.fixture
def engine(config: EngineConfig) -> Iterator[Engine]:
    with Engine(replace(config, sort_buffer_pages=1)) as instance:
        for table in ("a", "b"):
            instance.execute(f"CREATE TABLE {table} (id INT, tag VARCHAR(4))")
            values = ", ".join(f"({i}, '{table}')" for i in range(ROWS_PER_TABLE))
            instance.execute(f"INSERT INTO {table} VALUES {values}")
        yield instance


@contextmanager
def aggressive_thread_switching() -> Generator[None, None, None]:
    previous = sys.getswitchinterval()
    sys.setswitchinterval(AGGRESSIVE_SWITCH_INTERVAL)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def run_together(tasks: list[Callable[[], None]]) -> None:
    errors: list[Exception] = []
    start = threading.Barrier(len(tasks))

    def guarded(task: Callable[[], None]) -> None:
        start.wait()
        try:
            task()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=guarded, args=(task,)) for task in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


@pytest.mark.parametrize("kind", sorted(DISK_BOUND_QUERIES))
def test_concurrent_disk_bound_queries_see_only_their_own_rows(engine: Engine, kind: str) -> None:
    def reader(table: str) -> Callable[[], None]:
        def read() -> None:
            for _ in range(REPETITIONS):
                rows = engine.execute(DISK_BOUND_QUERIES[kind].format(t=table)).rows
                assert len(rows) == ROWS_PER_TABLE
                if kind != "group":
                    assert {row[0] for row in rows} == {table}

        return read

    with aggressive_thread_switching():
        run_together([reader("a"), reader("b"), reader("a"), reader("b")])


def test_disk_bound_queries_leave_no_temporary_directories(engine: Engine) -> None:
    for query in DISK_BOUND_QUERIES.values():
        engine.execute(query.format(t="a"))

    leftovers = [
        path.name
        for path in engine.config.data_directory.iterdir()
        if path.name.startswith(TEMPORARY_PREFIXES)
    ]
    assert leftovers == []


def test_concurrent_ddl_keeps_every_table_in_the_catalog(config: EngineConfig) -> None:
    def creator(thread: int, engine: Engine) -> Callable[[], None]:
        def create() -> None:
            for number in range(TABLES_PER_THREAD):
                engine.execute(f"CREATE TABLE t{thread}_{number} (id INT)")

        return create

    expected = {
        f"t{thread}_{number}" for thread in range(DDL_THREADS) for number in range(TABLES_PER_THREAD)
    }
    with Engine(config) as engine, aggressive_thread_switching():
        run_together([creator(thread, engine) for thread in range(DDL_THREADS)])
        assert set(engine.table_names()) == expected

    with Engine(config) as reopened:
        assert set(reopened.table_names()) == expected


def test_concurrent_first_use_opens_a_table_only_once(config: EngineConfig) -> None:
    with Engine(config) as engine:
        engine.execute("CREATE TABLE t (id INT)")

    opened = []
    with Engine(config) as engine, aggressive_thread_switching():
        run_together([lambda: opened.append(engine.table("t")) for _ in range(DDL_THREADS)])

    assert all(table is opened[0] for table in opened)
