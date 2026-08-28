import pytest

from config import EngineConfig
from query.engine import Engine
from txn import LockManager, Session, TransactionManager

TEST_LOCK_TIMEOUT = 2.0


@pytest.fixture
def quick_config(config: EngineConfig) -> EngineConfig:
    return EngineConfig(
        page_size=config.page_size,
        lock_timeout_seconds=TEST_LOCK_TIMEOUT,
        data_directory=config.data_directory,
    )


@pytest.fixture
def locks(quick_config: EngineConfig) -> LockManager:
    return LockManager(quick_config)


@pytest.fixture
def manager() -> TransactionManager:
    return TransactionManager()


@pytest.fixture
def engine(quick_config: EngineConfig):
    with Engine(quick_config) as instance:
        yield instance


@pytest.fixture
def session(engine: Engine, locks: LockManager, manager: TransactionManager):
    with Session(engine, locks, manager) as instance:
        yield instance


@pytest.fixture
def cuentas(session: Session) -> Session:
    session.execute("CREATE TABLE cuentas (id INT PRIMARY KEY, saldo INT)")
    session.execute("INSERT INTO cuentas VALUES (1, 100), (2, 50)")
    return session
