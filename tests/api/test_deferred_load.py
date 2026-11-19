"""Cargas con índices diferidos que no empiezan de cero: una tabla vaciada con `DELETE`, cuyos
índices guardan lápidas, y un proceso que muere a mitad de la carga. En los dos casos cada
índice termina con las filas de la tabla y responde lo mismo que recorrerla."""

import json
import random
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from api.ingest import Line, RowLoad
from api.schemas import RowLoadResponse
from api.service import Service
from config import EngineConfig
from query.engine import Engine
from query.table import STALE_SUFFIX

BATCH_ROWS = 25
COMMITTED_BATCHES = 2
UNSENT_BATCHES = 2
WORDS = ("rojo", "verde", "azul", "mesa", "silla", "libro", "radio")
WORDS_PER_TEXT = 5
SEED = 7
SEARCHED_WORD = "silla"
SCHEMA = "id INT PRIMARY KEY, emb VECTOR(4), lugar POINT, texto TEXT"
INDEXES = (
    "CREATE INDEX idx_t_emb ON t USING HNSW (emb)",
    "CREATE INDEX idx_t_ivf ON t USING IVF (emb) WITH (metric = 'euclidean')",
    "CREATE INDEX idx_t_lugar ON t USING RTREE (lugar)",
    "CREATE INDEX idx_t_texto ON t USING INVERTED (texto)",
)
DEFERRED_INDEXES = ["idx_t_emb", "idx_t_ivf", "idx_t_lugar", "idx_t_texto"]
CENTER = "POINT(-12.0, -77.0)"
RADIUS_METERS = 30_000
NEAREST = 7


def batches() -> list[list[Line]]:
    generator = random.Random(SEED)
    lines = [
        Line(
            key,
            json.dumps(
                {
                    "id": key,
                    "emb": [generator.uniform(-1, 1) for _ in range(4)],
                    "lugar": {
                        "lat": generator.uniform(-12.4, -11.6),
                        "lon": generator.uniform(-77.4, -76.6),
                    },
                    "texto": " ".join(generator.choice(WORDS) for _ in range(WORDS_PER_TEXT)),
                }
            ).encode(),
        )
        for key in range(1, BATCH_ROWS * (COMMITTED_BATCHES + UNSENT_BATCHES) + 1)
    ]
    return [lines[start : start + BATCH_ROWS] for start in range(0, len(lines), BATCH_ROWS)]


def ids(engine: Engine, sql: str) -> list[int]:
    return sorted(row[0] for row in engine.execute(sql).rows)


def plan_of(engine: Engine, sql: str) -> str:
    return str(engine.execute(f"EXPLAIN {sql}").plan.to_dict())


def keys_of(loaded: Sequence[list[Line]]) -> list[int]:
    return [line.number for batch in loaded for line in batch]


@pytest.fixture
def service(config: EngineConfig) -> Iterator[Service]:
    service = Service(config)
    for sql in (f"CREATE TABLE t ({SCHEMA})", *INDEXES):
        service.engine.execute(sql)
    yield service
    service.close()


def load(service: Service, loaded: Sequence[list[Line]]) -> RowLoadResponse:
    with service.session("cargador", None) as session:
        row_load = RowLoad(service, session, "t")
        row_load.start()
        for batch in loaded:
            row_load.load(batch)
        return row_load.finish()


def crashed_after(service: Service, loaded: Sequence[list[Line]], crashed: Path) -> EngineConfig:
    """La carga confirma esos lotes y el proceso «muere»: se copia el disco tal como está,
    sin construir los índices diferidos ni cerrar nada, y la copia es lo que queda."""
    with service.session("cargador", None) as session:
        row_load = RowLoad(service, session, "t")
        row_load.start()
        for batch in loaded:
            row_load.load(batch)
        shutil.copytree(service.config.data_directory, crashed)
        row_load.finish()
    return replace(service.config, data_directory=crashed)


def emptied_by_delete(service: Service) -> None:
    """Llena la tabla con filas que no se van a buscar y las borra todas: sus índices quedan
    con lápidas, y las filas que se carguen después reutilizan sus huecos del heap."""
    load(service, batches()[COMMITTED_BATCHES:])
    service.engine.execute("DELETE FROM t")


def assert_indexes_answer_like_a_scan(engine: Engine, loaded: list[int]) -> None:
    assert ids(engine, "SELECT id FROM t") == loaded
    radius = f"SELECT id FROM t WHERE distancia(lugar, {CENTER}) < {RADIUS_METERS}"
    nearest = f"SELECT id FROM t ORDER BY distancia(lugar, {CENTER}) LIMIT {NEAREST}"
    assert "SpatialRangeScan" in plan_of(engine, radius)
    through_rtree = (ids(engine, radius), ids(engine, nearest))
    everything = len(loaded)
    hnsw = f"SELECT id FROM t WHERE SIMILAR_TO(emb, [1, 0, 0, 0], k = {everything}) USING HNSW"
    ivf = f"SELECT id FROM t WHERE SIMILAR_TO(emb, [1, 0, 0, 0], k = {everything}) USING IVF"
    assert ids(engine, hnsw) == ids(engine, ivf) == loaded
    word = f"SELECT id FROM t WHERE MATCH(texto, '{SEARCHED_WORD}')"
    scanned_word = ids(engine, f"SELECT id FROM t WHERE texto LIKE '%{SEARCHED_WORD}%'")
    assert scanned_word
    assert ids(engine, word) == scanned_word
    engine.execute("DROP INDEX idx_t_lugar")
    assert "SpatialRangeScan" not in plan_of(engine, radius)
    assert (ids(engine, radius), ids(engine, nearest)) == through_rtree
    assert through_rtree[0]


def stale_markers(config: EngineConfig) -> list[Path]:
    return list(config.data_directory.glob(f"*{STALE_SUFFIX}"))


def test_a_load_that_dies_mid_way_leaves_indexes_that_rebuild_on_open(
    service: Service, tmp_path: Path
):
    committed = batches()[:COMMITTED_BATCHES]
    crashed = crashed_after(service, committed, tmp_path / "tras_el_corte")
    assert len(stale_markers(crashed)) == len(INDEXES)
    with Engine(crashed) as reopened:
        reopened.table("t")
        assert stale_markers(crashed) == []
        assert_indexes_answer_like_a_scan(reopened, keys_of(committed))


def test_a_table_emptied_by_delete_loads_with_its_indexes_deferred(service: Service):
    emptied_by_delete(service)
    committed = batches()[:COMMITTED_BATCHES]
    summary = load(service, committed)
    assert summary.deferred_indexes == DEFERRED_INDEXES
    assert (summary.rows_loaded, summary.rejected) == (len(keys_of(committed)), [])
    assert_indexes_answer_like_a_scan(service.engine, keys_of(committed))


def test_a_load_into_an_emptied_table_that_dies_mid_way_rebuilds_on_open(
    service: Service, tmp_path: Path
):
    emptied_by_delete(service)
    committed = batches()[:COMMITTED_BATCHES]
    crashed = crashed_after(service, committed, tmp_path / "tras_el_corte")
    assert len(stale_markers(crashed)) == len(INDEXES)
    with Engine(crashed) as reopened:
        reopened.table("t")
        assert stale_markers(crashed) == []
        assert_indexes_answer_like_a_scan(reopened, keys_of(committed))
