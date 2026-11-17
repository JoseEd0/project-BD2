"""Los servicios de `apps/` hablan con el gestor por HTTP y nunca importan su código."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parent.parent
ENGINE_ROOT = REPOSITORY / "src"
SERVICES_ROOT = REPOSITORY / "apps"


def engine_modules() -> frozenset[str]:
    packages = {path.name for path in ENGINE_ROOT.iterdir() if (path / "__init__.py").is_file()}
    modules = {path.stem for path in ENGINE_ROOT.glob("*.py")}
    return frozenset(packages | modules)


def imported_roots(source: str) -> Iterator[str]:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def engine_imports(source: str) -> list[str]:
    forbidden = engine_modules()
    return sorted({root for root in imported_roots(source) if root in forbidden})


def service_sources() -> list[Path]:
    ignored = {"node_modules", ".venv"}
    return [
        path
        for path in SERVICES_ROOT.rglob("*.py")
        if not ignored.intersection(path.relative_to(SERVICES_ROOT).parts)
    ]


def test_the_engine_exposes_its_packages() -> None:
    assert {"sql", "storage", "query", "api", "config"} <= engine_modules()


def test_a_service_importing_the_engine_is_caught() -> None:
    source = "import numpy\nfrom query.planner import plan\nimport storage.page as page\n"
    assert engine_imports(source) == ["query", "storage"]


def test_relative_imports_inside_a_service_are_allowed() -> None:
    assert engine_imports("from .config import Settings\nfrom . import api\n") == []


@pytest.mark.parametrize(
    "path", service_sources(), ids=lambda path: str(path.relative_to(SERVICES_ROOT))
)
def test_services_do_not_import_the_engine(path: Path) -> None:
    assert engine_imports(path.read_text(encoding="utf-8")) == []
