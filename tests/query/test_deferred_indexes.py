"""Índices diferidos al cargar una tabla vacía: se construyen de una vez al final y
responden lo mismo que mantenidos fila a fila."""

import random
from collections.abc import Iterator
from pathlib import Path

import pytest

from config import EngineConfig
from query.engine import Engine
from query.ingest import JsonRow, LoadRowsStatement

SCHEMA = (
    "id INT PRIMARY KEY, grupo INT INDEX HASH, codigo VARCHAR(8) UNIQUE, emb VECTOR(4), "
    "lugar POINT, texto TEXT"
)
BULK_INDEXES = ("idx_t_emb_hnsw", "idx_t_emb_ivf", "idx_t_lugar", "idx_t_texto")
CREATE_INDEXES = (
    "CREATE INDEX idx_t_emb_hnsw ON t USING HNSW (emb)",
    "CREATE INDEX idx_t_emb_ivf ON t USING IVF (emb) WITH (metric = 'euclidean')",
    "CREATE INDEX idx_t_lugar ON t USING RTREE (lugar)",
    "CREATE INDEX idx_t_texto ON t USING INVERTED (texto)",
)
ROWS = 300
BATCH_ROWS = 64
GROUPS = 7
WORDS = ("rojo", "verde", "azul", "mesa", "silla", "lampara", "cafe", "te", "libro", "radio")
WORDS_PER_TEXT = 6
SEED = 69
QUERIES = (
    "SELECT id FROM t WHERE distancia(lugar, POINT(-12.0, -77.0)) < 40000 ORDER BY id",
    "SELECT id FROM t ORDER BY distancia(lugar, POINT(-12.1, -77.1)) LIMIT 15",
    "SELECT id FROM t WHERE intersecta(lugar, POLYGON((-12.2, -77.2), (-12.2, -76.9), "
    "(-11.9, -76.9), (-11.9, -77.2))) ORDER BY id",
    "SELECT id, SCORE() FROM t WHERE MATCH(texto, 'mesa azul') USING BM25 LIMIT 10",
    "SELECT id FROM t WHERE MATCH(texto, 'libro AND NOT radio') ORDER BY id",
    "SELECT id FROM t WHERE SIMILAR_TO(emb, [0.3, -0.2, 0.9, 0.1], k = 10) USING HNSW",
    "SELECT id FROM t WHERE SIMILAR_TO(emb, [0.3, -0.2, 0.9, 0.1], k = 10) USING IVF",
    "SELECT id FROM t WHERE grupo = 3 ORDER BY id",
    "SELECT COUNT(*) FROM t",
)


def rows(count: int) -> list[JsonRow]:
    generator = random.Random(SEED)
    return [
        {
            "id": key,
            "grupo": key % GROUPS,
            "codigo": f"c{key}",
            "emb": [generator.uniform(-1, 1) for _ in range(4)],
            "lugar": {
                "lat": generator.uniform(-12.5, -11.5),
                "lon": generator.uniform(-77.5, -76.5),
            },
            "texto": " ".join(generator.choice(WORDS) for _ in range(WORDS_PER_TEXT)),
        }
        for key in range(1, count + 1)
    ]


def batches(loaded: list[JsonRow]) -> Iterator[tuple[JsonRow, ...]]:
    for start in range(0, len(loaded), BATCH_ROWS):
        yield tuple(loaded[start : start + BATCH_ROWS])


@pytest.fixture
def make_engine(config: EngineConfig, tmp_path: Path) -> Iterator:
    opened: list[Engine] = []

    def make(name: str) -> Engine:
        directory = tmp_path / name
        engine = Engine(EngineConfig(data_directory=directory, page_size=config.page_size))
        opened.append(engine)
        engine.execute(f"CREATE TABLE t ({SCHEMA})")
        for sql in CREATE_INDEXES:
            engine.execute(sql)
        return engine

    yield make
    for engine in opened:
        engine.close()


def answers(engine: Engine) -> list[tuple]:
    return [engine.execute(sql).rows for sql in QUERIES]


def test_only_the_indexes_with_a_bulk_build_are_deferred(make_engine):
    engine = make_engine("diferida")
    assert sorted(engine.table("t").defer_bulk_indexes()) == list(BULK_INDEXES)
    engine.table("t").build_deferred_indexes()


def test_a_bulk_build_answers_like_rows_indexed_one_by_one(make_engine):
    loaded = rows(ROWS)
    incremental, bulk = make_engine("fila_a_fila"), make_engine("de_una_vez")
    for batch in batches(loaded):
        incremental.run(LoadRowsStatement("t", batch))
    table = bulk.table("t")
    table.defer_bulk_indexes()
    for batch in batches(loaded):
        bulk.run(LoadRowsStatement("t", batch))
    table.build_deferred_indexes()
    expected = answers(incremental)
    assert all(expected), "cada consulta tiene que devolver algo para que la comparación diga algo"
    assert answers(bulk) == expected


def test_deferred_indexes_survive_reopening(make_engine, tmp_path: Path, config: EngineConfig):
    bulk = make_engine("reabierta")
    bulk.table("t").defer_bulk_indexes()
    bulk.run(LoadRowsStatement("t", tuple(rows(ROWS))))
    bulk.table("t").build_deferred_indexes()
    expected = answers(bulk)
    bulk.close()
    directory = tmp_path / "reabierta"
    with Engine(EngineConfig(data_directory=directory, page_size=config.page_size)) as reopened:
        assert answers(reopened) == expected
