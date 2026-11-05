"""La carga de la demo al primer arranque: `--solo-si-vacia` no pisa una base que ya existe."""

from pathlib import Path
from types import ModuleType

from config import EngineConfig
from query.engine import Engine

SMALL_SCALE = "0.01"


def table_names(data_directory: Path) -> list[str]:
    with Engine(EngineConfig(data_directory=data_directory)) as engine:
        return sorted(engine.table_names())


def test_an_empty_database_gets_the_demo(dataset: ModuleType, tmp_path: Path):
    dataset.main(["--data-dir", str(tmp_path), "--escala", SMALL_SCALE, "--solo-si-vacia"])

    expected = sorted([spec.name for spec in dataset.SPECS] + [t.spec.name for t in dataset.ESCI_TABLES])
    assert table_names(tmp_path) == expected


def test_a_database_with_tables_is_left_as_it_is(dataset: ModuleType, tmp_path: Path):
    with Engine(EngineConfig(data_directory=tmp_path)) as engine:
        engine.execute("CREATE TABLE propia (id INT PRIMARY KEY);")
        engine.execute("INSERT INTO propia VALUES (1);")

    dataset.main(["--data-dir", str(tmp_path), "--solo-si-vacia"])

    assert table_names(tmp_path) == ["propia"]
    with Engine(EngineConfig(data_directory=tmp_path)) as engine:
        assert len(engine.execute("SELECT * FROM propia;").rows) == 1


def test_a_database_in_use_by_the_api_is_not_opened_again(dataset: ModuleType, tmp_path: Path):
    with Engine(EngineConfig(data_directory=tmp_path)) as engine:
        engine.execute("CREATE TABLE propia (id INT PRIMARY KEY);")
        dataset.main(["--data-dir", str(tmp_path), "--solo-si-vacia"])

        assert engine.table_names() == ["propia"]
