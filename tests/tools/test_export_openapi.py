import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from tools.export_openapi import CONTRACTS, REPOSITORY_ROOT, Contract, export_contracts, main


@contextmanager
def tiny_app() -> Iterator[FastAPI]:
    app = FastAPI(title="tiny")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    yield app


def test_the_engine_contract_lives_next_to_its_python_client() -> None:
    assert next(iter(CONTRACTS)) == "engine"
    assert CONTRACTS["engine"].path == Path("apps/client/openapi/engine.json")


def test_every_contract_lives_under_apps() -> None:
    for contract in CONTRACTS.values():
        assert not contract.path.is_absolute()
        assert contract.path.parts[0] == "apps"
        assert contract.path.suffix == ".json"


def test_the_engine_contract_describes_its_health_route(tmp_path: Path) -> None:
    [path] = export_contracts(tmp_path)
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "apps" / "client" / "openapi" / "engine.json"
    assert schema["openapi"].startswith("3.")
    assert "get" in schema["paths"]["/health"]


def test_exporting_twice_writes_the_same_bytes(tmp_path: Path) -> None:
    [first] = export_contracts(tmp_path / "first")
    [second] = export_contracts(tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()


def test_the_versioned_engine_contract_is_up_to_date(tmp_path: Path) -> None:
    [exported] = export_contracts(tmp_path)
    versioned = REPOSITORY_ROOT / CONTRACTS["engine"].path
    assert exported.read_text(encoding="utf-8") == versioned.read_text(encoding="utf-8")


def test_each_service_is_written_next_to_its_consumer(tmp_path: Path) -> None:
    contracts = {
        "one": Contract(tiny_app, Path("apps/one/web/openapi/one.json")),
        "two": Contract(tiny_app, Path("apps/two/web/openapi/two.json")),
    }
    written = export_contracts(tmp_path, contracts)
    assert [path.relative_to(tmp_path) for path in written] == [contract.path for contract in contracts.values()]
    assert json.loads(written[1].read_text(encoding="utf-8"))["info"]["title"] == "tiny"


def test_the_command_line_writes_under_the_given_root(tmp_path: Path) -> None:
    main(["--root", str(tmp_path)])
    assert (tmp_path / "apps" / "client" / "openapi" / "engine.json").is_file()
