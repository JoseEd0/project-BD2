import pytest

from config import EngineConfig
from query.engine import Engine


@pytest.fixture
def engine(config: EngineConfig):
    with Engine(config) as instance:
        yield instance


@pytest.fixture
def alumnos(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE alumnos ("
        "  id INT PRIMARY KEY,"
        "  nombre VARCHAR(16),"
        "  ciudad VARCHAR(12) INDEX HASH,"
        "  nota FLOAT"
        ")"
    )
    cities = ["lima", "cusco", "piura"]
    for number in range(30):
        engine.execute(
            f"INSERT INTO alumnos VALUES "
            f"({number}, 'n{number}', '{cities[number % 3]}', {number / 2})"
        )
    return engine
