"""El plazo de una sentencia: el ejecutor la corta entre fila y fila y la sesión la deshace."""

import threading

import pytest

from config import EngineConfig
from query.deadline import (
    Deadline,
    StatementCancelledError,
    StatementTimeoutError,
    statement_deadline,
    within_deadline,
)
from query.engine import Engine
from txn import LockManager, Session, TransactionManager

ROWS = 50
ALREADY_EXPIRED = 1e-9


@pytest.fixture
def engine(config: EngineConfig):
    with Engine(config) as instance:
        instance.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        values = ", ".join(f"({value}, 0)" for value in range(ROWS))
        instance.execute(f"INSERT INTO t VALUES {values}")
        yield instance


def test_without_a_deadline_a_statement_runs_to_the_end(engine: Engine):
    assert engine.execute("SELECT COUNT(*) FROM t").rows == ((ROWS,),)


def test_a_generous_deadline_does_not_cut_anything(engine: Engine):
    with statement_deadline(Deadline(60.0)):
        assert engine.execute("SELECT COUNT(*) FROM t").rows == ((ROWS,),)


def test_an_expired_deadline_cuts_a_query(engine: Engine):
    with statement_deadline(Deadline(ALREADY_EXPIRED)), pytest.raises(
        StatementTimeoutError, match="statement_timeout"
    ):
        engine.execute("SELECT * FROM t ORDER BY v")


def test_a_cancelled_deadline_says_why(engine: Engine):
    deadline = Deadline(60.0)
    deadline.cancel("el servidor se apaga")
    with statement_deadline(deadline), pytest.raises(StatementCancelledError, match="apaga"):
        engine.execute("SELECT * FROM t")


def test_a_cut_update_is_undone_by_the_session(engine: Engine, config: EngineConfig):
    with Session(engine, LockManager(config), TransactionManager()) as session:
        with statement_deadline(Deadline(ALREADY_EXPIRED)), pytest.raises(StatementTimeoutError):
            session.execute("UPDATE t SET v = 1")
        assert session.execute("SELECT COUNT(*) FROM t WHERE v = 1").rows == ((0,),)


def test_the_deadline_belongs_to_the_thread_that_installed_it(engine: Engine):
    outcome: list[int] = []
    with statement_deadline(Deadline(ALREADY_EXPIRED)):
        other = threading.Thread(
            target=lambda: outcome.append(len(engine.execute("SELECT * FROM t").rows))
        )
        other.start()
        other.join()
    assert outcome == [ROWS]


def test_within_deadline_checks_before_each_item():
    with statement_deadline(Deadline(ALREADY_EXPIRED)), pytest.raises(StatementTimeoutError):
        list(within_deadline([1, 2, 3]))
    assert list(within_deadline([1, 2, 3])) == [1, 2, 3]
