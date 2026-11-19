"""Índices diferidos al cargar una tabla vacía: se construyen de una vez al final y
responden lo mismo que mantenidos fila a fila."""

import random
from collections.abc import Iterator
from pathlib import Path

import pytest

from config import EngineConfig
from query.engine import Engine
from query.index_methods import method_named
from query.ingest import JsonRow, LoadRowsStatement
from query.similarity_search import SimilaritySearchError
from query.table import STALE_SUFFIX
from query.text_search import TextSearchError
from sql.nodes import IndexType
from storage.schema import Field
from storage.types import FieldType

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
# Sin LIMIT: en una tabla vaciada las filas nuevas caen en otros huecos del heap, y un empate
# de puntaje en el corte se resolvería distinto. Con k = ROWS, cada índice de vectores
# devuelve todas sus filas, sin entradas viejas ni repetidas.
EVERY_ROW_QUERIES = (
    "SELECT id FROM t WHERE distancia(lugar, POINT(-12.0, -77.0)) < 40000",
    "SELECT id FROM t ORDER BY distancia(lugar, POINT(-12.1, -77.1)) LIMIT 15",
    "SELECT id, SCORE() FROM t WHERE MATCH(texto, 'mesa azul') USING BM25",
    "SELECT id, SCORE() FROM t WHERE MATCH(texto, 'silla cafe') USING TF_IDF",
    "SELECT id FROM t WHERE MATCH(texto, 'libro AND NOT radio')",
    f"SELECT id FROM t WHERE SIMILAR_TO(emb, [0.3, -0.2, 0.9, 0.1], k = {ROWS}) USING HNSW",
    f"SELECT id FROM t WHERE SIMILAR_TO(emb, [0.3, -0.2, 0.9, 0.1], k = {ROWS}) USING IVF",
)


def rows(count: int, seed: int = SEED) -> list[JsonRow]:
    generator = random.Random(seed)
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


def every_row_answers(engine: Engine) -> list[list[tuple]]:
    return [sorted(engine.execute(sql).rows) for sql in EVERY_ROW_QUERIES]


def test_only_the_indexes_with_a_bulk_build_are_deferred(make_engine):
    engine = make_engine("diferida")
    assert sorted(engine.table("t").defer_bulk_indexes()) == list(BULK_INDEXES)
    engine.table("t").build_deferred_indexes()


def load_one_by_one(engine: Engine, loaded: list[JsonRow]) -> None:
    for batch in batches(loaded):
        engine.run(LoadRowsStatement("t", batch))


def load_in_bulk(engine: Engine, loaded: list[JsonRow]) -> None:
    table = engine.table("t")
    table.defer_bulk_indexes()
    load_one_by_one(engine, loaded)
    table.build_deferred_indexes()


def test_a_bulk_build_answers_like_rows_indexed_one_by_one(make_engine):
    loaded = rows(ROWS)
    incremental, bulk = make_engine("fila_a_fila"), make_engine("de_una_vez")
    load_one_by_one(incremental, loaded)
    load_in_bulk(bulk, loaded)
    expected = answers(incremental)
    assert all(expected), "cada consulta tiene que devolver algo para que la comparación diga algo"
    assert answers(bulk) == expected


def test_a_table_emptied_by_delete_builds_its_indexes_like_a_new_one(make_engine):
    """Las filas borradas dejan lápidas en el invertido y huecos en el heap que las nuevas
    reutilizan con otro contenido: nada de eso llega a los índices construidos de nuevo,
    ni a los puntajes de BM25."""
    loaded = rows(ROWS)
    new, emptied = make_engine("nueva"), make_engine("vaciada")
    load_one_by_one(emptied, rows(ROWS, seed=SEED + 1))
    emptied.execute("DELETE FROM t")
    load_in_bulk(new, loaded)
    load_in_bulk(emptied, loaded)
    expected = every_row_answers(new)
    assert all(expected)
    assert every_row_answers(emptied) == expected


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


def stale_markers(engine: Engine) -> list[str]:
    return sorted(path.name for path in engine.config.data_directory.glob(f"*{STALE_SUFFIX}"))


def test_a_deferred_index_is_marked_stale_on_disk_until_it_is_built(make_engine):
    engine = make_engine("marcas")
    table = engine.table("t")
    table.defer_bulk_indexes()
    assert stale_markers(engine) == [f"t.{name}.idx{STALE_SUFFIX}" for name in BULK_INDEXES]
    engine.run(LoadRowsStatement("t", tuple(rows(BATCH_ROWS))))
    table.build_deferred_indexes()
    assert stale_markers(engine) == []


def test_the_planner_never_uses_a_deferred_index(make_engine):
    """Con los índices diferidos, una condición espacial se responde recorriendo la tabla
    —con todas sus filas—, y MATCH y SIMILAR_TO, que no tienen recorrido, se niegan."""
    engine = make_engine("planificador")
    table = engine.table("t")
    table.defer_bulk_indexes()
    engine.run(LoadRowsStatement("t", tuple(rows(ROWS))))
    radius = "SELECT id FROM t WHERE distancia(lugar, POINT(-12.0, -77.0)) < 40000 ORDER BY id"
    plan = str(engine.execute(f"EXPLAIN {radius}").plan.to_dict())
    assert "SpatialRangeScan" not in plan
    scanned = engine.execute(radius).rows
    assert scanned
    with pytest.raises(TextSearchError, match="no tiene índice invertido"):
        engine.execute("SELECT id FROM t WHERE MATCH(texto, 'mesa')")
    with pytest.raises(SimilaritySearchError):
        engine.execute("SELECT id FROM t WHERE SIMILAR_TO(emb, [1, 0, 0, 0], k = 3)")
    table.build_deferred_indexes()
    assert "SpatialRangeScan" in str(engine.execute(f"EXPLAIN {radius}").plan.to_dict())
    assert engine.execute(radius).rows == scanned


def test_a_similarity_index_over_file_paths_is_not_deferred():
    """Construirlo de nuevo extraería y entrenaría su vocabulario: no se difiere."""
    paths = Field("ruta", FieldType.STRING, 40)
    vectors = Field("emb", FieldType.VECTOR, 4)
    for method in (IndexType.HNSW, IndexType.IVF):
        known = method_named(method)
        assert known is not None
        assert not known.builds_in_bulk(paths)
        assert known.builds_in_bulk(vectors)
