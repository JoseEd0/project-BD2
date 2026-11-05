"""Los atajos de consulta del frontend, contra el dataset de demostración.

Son el guion de la exposición: si uno falla, falla en vivo. Cada atajo se ejecuta por el
API, en el orden de la interfaz y dos veces, porque prometen poder lanzarse de nuevo sin
error. Los que cierran una transacción (`COMMIT`, `ROLLBACK`) se prueban con una abierta,
que es como se usan en la demostración.

Las tablas multimedia de la demo salen de fotos y pistas reales que no viven en el
repositorio. En su lugar, el test genera las que nombran los atajos —en las mismas rutas— y
unas cuantas más, con los CSV de esas tablas.
"""

import csv
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from tests.multimedia.media_support import TEXTURE_SIDE, chirp, textured_pixels, write_audio

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
MEDIA_PATH = re.compile(r"'(data/datasets/[^']+\.(?:jpg|mp3))'")
FILLER_FILES = 12
STAND_IN_CATEGORIES = ("CELLULAR_PHONE_CASE", "SHOES", "HOME")
STAND_IN_GENRES = ("Folk", "Rock", "Electronic")
CLIP_SECONDS = 1.0
# Unas pocas fotos y pistas no dan descriptores para las 256 palabras por omisión.
STAND_IN_VOCABULARY = 16


def snippets() -> list[tuple[str, str, str]]:
    """Grupo, etiqueta y SQL de cada atajo, en el orden de la interfaz."""
    source = SNIPPETS.read_text(encoding="utf-8")
    found = []
    for group, body in GROUP_PATTERN.findall(source):
        for label, template, quoted in SNIPPET_PATTERN.findall(body):
            found.append((group, label, template or quoted))
    return found


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory, dataset: ModuleType) -> Path:
    """Un directorio de trabajo como la raíz del repositorio: `demos/` enlazado y los
    datasets multimedia sustituidos por archivos generados."""
    root = tmp_path_factory.mktemp("raiz")
    (root / "demos").symlink_to(REPOSITORY / "demos", target_is_directory=True)
    write_stand_in_datasets(root, dataset)
    return root


@pytest.fixture(scope="module")
def client(
    tmp_path_factory: pytest.TempPathFactory, dataset: ModuleType, workspace: Path
) -> Iterator[TestClient]:
    directory = tmp_path_factory.mktemp("demo")
    config = EngineConfig(
        data_directory=directory,
        import_directories=(SAMPLES, workspace / dataset.DATASETS_DIRECTORY),
        vocabulary_size=STAND_IN_VOCABULARY,
        feature_workers=1,
    )
    generator = dataset.Generator(SNIPPET_SCALE, dataset.DEFAULT_SEED)
    with pytest.MonkeyPatch.context() as patch, Engine(config) as engine:
        patch.chdir(workspace)
        for spec in dataset.SPECS:
            path, _ = dataset.write_csv(directory / "csv", spec, generator.rows_of(spec.name))
            dataset.load(engine, spec, path)
        for sample in dataset.ESCI_TABLES:
            dataset.load(engine, sample.spec, dataset.ESCI_SAMPLE_DIRECTORY / sample.file_name)
        for sample in dataset.MEDIA_TABLES:
            dataset.load(engine, sample.spec, dataset.DATASETS_DIRECTORY / sample.file_name)
    with TestClient(create_app(config)) as instance:
        yield instance


def write_stand_in_datasets(root: Path, dataset: ModuleType) -> None:
    """Las fotos y pistas que nombran los atajos, más algunas de relleno, y el CSV de cada
    tabla multimedia de la demo apuntando a ellas."""
    named = {path for _, _, sql in snippets() for path in MEDIA_PATH.findall(sql)}
    images = sorted(path for path in named if path.endswith(".jpg"))
    tracks = sorted(path for path in named if path.endswith(".mp3"))
    images += [f"data/datasets/abo/imagenes/relleno/{number}.jpg" for number in range(FILLER_FILES)]
    tracks += [f"data/datasets/fma_small/pistas/relleno/{number}.mp3" for number in range(FILLER_FILES)]
    for seed, relative in enumerate(images):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(target), textured_pixels(TEXTURE_SIDE, TEXTURE_SIDE, seed=seed))
    for seed, relative in enumerate(tracks):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        write_audio(target, np.roll(chirp(CLIP_SECONDS), seed * 1000))
    images_table, songs_table = dataset.MEDIA_TABLES
    write_rows(
        root / dataset.DATASETS_DIRECTORY / images_table.file_name,
        images_table.spec.header,
        [
            (key, path, f"Foto {key}", STAND_IN_CATEGORIES[key % len(STAND_IN_CATEGORIES)])
            for key, path in enumerate(images, start=1)
        ],
    )
    write_rows(
        root / dataset.DATASETS_DIRECTORY / songs_table.file_name,
        songs_table.spec.header,
        [
            (key, path, f"Pista {key}", STAND_IN_GENRES[key % len(STAND_IN_GENRES)], "Artista")
            for key, path in enumerate(tracks, start=1)
        ],
    )


def write_rows(path: Path, header: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def test_the_snippet_file_can_be_read():
    found = snippets()
    assert found, "no se encontró ningún atajo en snippets.ts"
    assert len({group for group, _, _ in found}) > 1


def test_every_snippet_runs_twice_on_the_demo_dataset(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(workspace)
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
