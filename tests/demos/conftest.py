"""El generador del dataset de demostración, cargado como módulo para los tests."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dataset() -> ModuleType:
    path = REPOSITORY / "demos" / "poblar_ecommerce.py"
    spec = importlib.util.spec_from_file_location("poblar_ecommerce", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Las dataclasses buscan su módulo en `sys.modules` para resolver las anotaciones.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
