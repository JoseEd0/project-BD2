"""La demostración de concurrencia que exige el enunciado, ejecutada en pequeño."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
THREADS = 3
ROUNDS = 6


@pytest.fixture(scope="module")
def demo() -> ModuleType:
    path = REPOSITORY / "demos" / "concurrencia.py"
    spec = importlib.util.spec_from_file_location("concurrencia", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_transactions_keep_every_withdrawal_that_the_race_loses(demo: ModuleType, tmp_path: Path):
    engine, locks, manager = demo.build_engine(tmp_path)
    with engine:
        demo.prepare(demo.Session(engine, locks, manager))
        demo.scene_race_condition(engine, locks, manager, THREADS, ROUNDS)
        protected = demo.scene_transactions(engine, locks, manager, THREADS, ROUNDS)
        assert protected.correct
        outcomes = demo.scene_deadlock(engine, locks, manager)
    verdicts = sorted(line.split(":")[1].split()[0] for line in outcomes)
    assert verdicts == ["abortada", "confirmada"]
    assert locks.deadlocks_detected >= 1
