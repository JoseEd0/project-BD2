"""Los atajos de consulta del frontend, contra el dataset de demostración.

Son el guion de la exposición: si uno falla, falla en vivo. Cada atajo se ejecuta por el
API, en el orden de la interfaz y dos veces, porque prometen poder lanzarse de nuevo sin
error. Los que cierran una transacción (`COMMIT`, `ROLLBACK`) se prueban con una abierta,
que es como se usan en la demostración.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config import EngineConfig
from query.engine import Engine

REPOSITORY = Path(__file__).resolve().parents[2]
SNIPPETS = REPOSITORY / "frontend" / "src" / "snippets.ts"
SAMPLES = REPOSITORY / "demos" / "samples"
SESSION = "atajos"
ROUNDS = 2
# Una fracción del dataset basta: el test comprueba que cada atajo corre sin error sobre las
# tablas de la demostración, no cuántas filas devuelve, y así tarda segundos y no medio minuto.
SNIPPET_SCALE = 0.2
GROUP_PATTERN = re.compile(r'name: "([^"]+)",\s*snippets: \[(.*?)\n    \],', re.S)
SNIPPET_PATTERN = re.compile(r'label: "([^"]+)",.*?sql: (?:`(.*?)`|"((?:[^"\\]|\\.)*)")', re.S)
ENDS_A_TRANSACTION = re.compile(r"^\s*(COMMIT|ROLLBACK)\b", re.I)


def snippets() -> list[tuple[str, str, str]]:
    """Grupo, etiqueta y SQL de cada atajo, en el orden de la interfaz."""
    source = SNIPPETS.read_text(encoding="utf-8")
    found = []
    for group, body in GROUP_PATTERN.findall(source):
        for label, template, quoted in SNIPPET_PATTERN.findall(body):
            found.append((group, label, template or quoted))
    return found


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory, dataset: ModuleType) -> Iterator[TestClient]:
    directory = tmp_path_factory.mktemp("demo")
    config = EngineConfig(data_directory=directory, import_directories=(SAMPLES,))
    generator = dataset.Generator(SNIPPET_SCALE, dataset.DEFAULT_SEED)
    with Engine(config) as engine:
        for spec in dataset.SPECS:
            path, _ = dataset.write_csv(directory / "csv", spec, generator.rows_of(spec.name))
            dataset.load(engine, spec, path)
    with TestClient(create_app(config)) as instance:
        yield instance


def test_the_snippet_file_can_be_read():
    found = snippets()
    assert found, "no se encontró ningún atajo en snippets.ts"
    assert len({group for group, _, _ in found}) > 1


def test_every_snippet_runs_twice_on_the_demo_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(REPOSITORY)
    for round_number in range(ROUNDS):
        for group, label, sql in snippets():
            if ENDS_A_TRANSACTION.match(sql) and not in_transaction(client):
                run(client, "BEGIN TRANSACTION;")
            response = client.post("/query", json={"sql": sql, "session_id": SESSION})
            assert response.status_code == 200, (
                f"vuelta {round_number + 1}, [{group}] {label}: {response.json()}"
            )
    if in_transaction(client):
        run(client, "ROLLBACK;")


def in_transaction(client: TestClient) -> bool:
    return bool(run(client, "SELECT COUNT(*) FROM categorias;")["in_transaction"])


def run(client: TestClient, sql: str) -> dict:
    response = client.post("/query", json={"sql": sql, "session_id": SESSION})
    assert response.status_code == 200, response.json()
    return response.json()
