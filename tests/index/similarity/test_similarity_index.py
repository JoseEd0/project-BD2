"""El índice de similitud: vectores cero fuera del índice y el IVF entrenado con todo."""

from pathlib import Path

import numpy as np
import pytest
from tests.multimedia.media_support import FLAT_GRAY, TEXTURE_SIDE, textured_pixels, write_image

import multimedia.feature_store as feature_store
from config import EngineConfig
from index.ivf import default_list_count
from query.engine import Engine

PHOTOS = 12
VOCABULARY = 8
FLAT_ID = 99


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "medios"
    directory.mkdir()
    for number in range(PHOTOS):
        write_image(directory / f"f{number}.png", textured_pixels(TEXTURE_SIDE, TEXTURE_SIDE, seed=number))
    flat = np.full((TEXTURE_SIDE, TEXTURE_SIDE), FLAT_GRAY, dtype=np.uint8)
    write_image(directory / "lisa.png", flat)
    return directory


@pytest.fixture
def config(tmp_path: Path, media: Path) -> EngineConfig:
    return EngineConfig(data_directory=tmp_path / "datos", import_directories=(media,), feature_workers=1)


def gallery(config: EngineConfig, media: Path, index_sql: str) -> Engine:
    engine = Engine(config)
    engine.execute("CREATE TABLE fotos (id INT PRIMARY KEY, ruta VARCHAR(200))")
    rows = [f"({number}, '{media / f'f{number}.png'}')" for number in range(PHOTOS)]
    rows.append(f"({FLAT_ID}, '{media / 'lisa.png'}')")
    engine.execute(f"INSERT INTO fotos VALUES {', '.join(rows)}")
    engine.execute(index_sql)
    return engine


def search(engine: Engine, query: Path, k: int, tail: str = "") -> list[tuple[int, float]]:
    sql = f"SELECT id, SIMILARITY_SCORE() FROM fotos WHERE SIMILAR_TO('{query}', k = {k}) {tail}"
    return [(row[0], row[1]) for row in engine.execute(sql).rows]


def zero_vectors(engine: Engine) -> int:
    return engine.table("fotos").similarity_index("i").describe()["zero_vectors"]


@pytest.mark.parametrize(
    ("metric", "expected"), [("euclidean", 1.0), ("cosine", 0.0), ("dot", 0.0)]
)
def test_a_zero_vector_is_answered_with_its_exact_score(config, media, metric, expected):
    with gallery(
        config, media, f"CREATE INDEX i ON fotos USING HNSW (ruta) WITH (METRIC = {metric}, vocabulary = {VOCABULARY})"
    ) as engine:
        found = dict(search(engine, media / "lisa.png", PHOTOS + 1))

        assert zero_vectors(engine) == 1
        assert found[FLAT_ID] == pytest.approx(expected)
        assert engine.table("fotos").similarity_index("i").describe()["vectors"] == PHOTOS


def test_the_flat_photo_is_its_own_nearest_with_euclidean(config, media):
    with gallery(
        config, media, f"CREATE INDEX i ON fotos USING HNSW (ruta) WITH (METRIC = euclidean, vocabulary = {VOCABULARY})"
    ) as engine:
        assert search(engine, media / "lisa.png", 1) == [(FLAT_ID, pytest.approx(1.0))]


def test_a_filter_also_applies_to_zero_vectors(config, media):
    with gallery(
        config, media, f"CREATE INDEX i ON fotos USING HNSW (ruta) WITH (METRIC = euclidean, vocabulary = {VOCABULARY})"
    ) as engine:
        found = [key for key, _ in search(engine, media / "lisa.png", 3, f"AND id <> {FLAT_ID}")]

        assert FLAT_ID not in found
        assert len(found) == 3


def test_zero_vectors_follow_inserts_deletes_and_reopening(config, media):
    sql = f"CREATE INDEX i ON fotos USING HNSW (ruta) WITH (METRIC = euclidean, vocabulary = {VOCABULARY})"
    with gallery(config, media, sql) as engine:
        engine.execute(f"INSERT INTO fotos VALUES (100, '{media / 'lisa.png'}')")
        assert zero_vectors(engine) == 2
        engine.execute(f"DELETE FROM fotos WHERE id = {FLAT_ID}")
        assert zero_vectors(engine) == 1
        assert FLAT_ID not in [key for key, _ in search(engine, media / "lisa.png", PHOTOS + 2)]

    with Engine(config) as reopened:
        assert zero_vectors(reopened) == 1
        assert search(reopened, media / "lisa.png", 1) == [(100, pytest.approx(1.0))]


def test_a_lost_zero_list_is_rebuilt_from_the_store(config, media):
    sql = f"CREATE INDEX i ON fotos USING HNSW (ruta) WITH (METRIC = euclidean, vocabulary = {VOCABULARY})"
    gallery(config, media, sql).close()
    next(Path(config.data_directory).glob("fotos.i.idx.ceros.npy")).unlink()

    with Engine(config) as reopened:
        assert zero_vectors(reopened) == 1


def test_ivf_trains_its_lists_with_every_vector(config, media, monkeypatch):
    monkeypatch.setattr(feature_store, "VECTOR_BATCH_ROWS", 3)
    lists = 4

    with gallery(
        config, media, f"CREATE INDEX i ON fotos USING IVF (ruta) WITH (lists = {lists}, vocabulary = {VOCABULARY})"
    ) as engine:
        shape = engine.table("fotos").similarity_index("i").describe()

    assert shape["lists"] == lists
    assert shape["trained_on"] == PHOTOS


def test_ivf_default_lists_follow_the_rule_for_every_vector(config, media, monkeypatch):
    monkeypatch.setattr(feature_store, "VECTOR_BATCH_ROWS", 2)

    with gallery(
        config, media, f"CREATE INDEX i ON fotos USING IVF (ruta) WITH (vocabulary = {VOCABULARY})"
    ) as engine:
        assert engine.table("fotos").similarity_index("i").describe()["trained_on"] == PHOTOS
        assert engine.table("fotos").similarity_index("i").describe()["lists"] == default_list_count(PHOTOS)
