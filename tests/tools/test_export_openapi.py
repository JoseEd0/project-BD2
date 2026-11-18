import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from tools.export_openapi import CONTRACTS, export_contracts, main


def test_the_engine_is_the_first_registered_contract() -> None:
    assert next(iter(CONTRACTS)) == "engine"


def test_the_engine_contract_describes_its_health_route(tmp_path: Path) -> None:
    [path] = export_contracts(tmp_path)
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "engine.json"
    assert schema["openapi"].startswith("3.")
    assert "get" in schema["paths"]["/health"]


def test_exporting_twice_writes_the_same_bytes(tmp_path: Path) -> None:
    [first] = export_contracts(tmp_path / "first")
    [second] = export_contracts(tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()


def test_every_registered_service_gets_its_own_file(tmp_path: Path) -> None:
    @contextmanager
    def tiny_app() -> Iterator[FastAPI]:
        app = FastAPI(title="tiny")

        @app.get("/health")
        def health() -> dict[str, str]:
            return {"status": "ok"}

        yield app

    written = export_contracts(tmp_path / "nested", {"one": tiny_app, "two": tiny_app})
    assert [path.name for path in written] == ["one.json", "two.json"]
    assert json.loads(written[1].read_text(encoding="utf-8"))["info"]["title"] == "tiny"


def test_the_command_line_writes_into_the_given_folder(tmp_path: Path) -> None:
    main(["--output", str(tmp_path)])
    assert (tmp_path / "engine.json").is_file()
