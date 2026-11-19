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
    path = export_contracts(tmp_path)[0]
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "apps" / "client" / "openapi" / "engine.json"
    assert schema["openapi"].startswith("3.")
    assert "get" in schema["paths"]["/health"]


def test_the_copyright_contract_lives_next_to_its_interface(tmp_path: Path) -> None:
    export_contracts(tmp_path)
    path = tmp_path / CONTRACTS["copyright"].path
    assert CONTRACTS["copyright"].path == Path("apps/copyright/web/openapi/copyright.json")
    paths = json.loads(path.read_text(encoding="utf-8"))["paths"]
    assert {"/detections", "/songs/{song_id}/audio"} <= set(paths)


def test_exporting_twice_writes_the_same_bytes(tmp_path: Path) -> None:
    first = export_contracts(tmp_path / "first")
    second = export_contracts(tmp_path / "second")
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]


def test_the_versioned_contracts_are_up_to_date(tmp_path: Path) -> None:
    export_contracts(tmp_path)
    for contract in CONTRACTS.values():
        exported = (tmp_path / contract.path).read_text(encoding="utf-8")
        assert exported == (REPOSITORY_ROOT / contract.path).read_text(encoding="utf-8")


def test_the_faces_contract_lives_next_to_its_interface(tmp_path: Path) -> None:
    [path] = export_contracts(tmp_path, {"faces": CONTRACTS["faces"]})
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert CONTRACTS["faces"].path == Path("apps/faces/web/openapi/faces.json")
    assert {"/consent", "/enrollments/{enrollment_id}/shots", "/person"} <= set(schema["paths"])


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
