"""Un proceso que muere a mitad de una carga con índices diferidos: al abrir de nuevo, cada
índice marcado como viejo se reconstruye con las filas confirmadas, y responde lo mismo que
recorrer la tabla."""

import json
import random
import shutil
from dataclasses import replace
from pathlib import Path

from api.ingest import Line, RowLoad
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


def crashed_after_committed_batches(config: EngineConfig, crashed: Path) -> EngineConfig:
    """La carga confirma sus primeros lotes y el proceso «muere»: se copia el disco tal como
    está, sin construir los índices diferidos ni cerrar nada, y la copia es lo que queda."""
    service = Service(config)
    try:
        for sql in (f"CREATE TABLE t ({SCHEMA})", *INDEXES):
            service.engine.execute(sql)
        with service.session("cargador", None) as session:
            load = RowLoad(service, session, "t")
            load.start()
            for batch in batches()[:COMMITTED_BATCHES]:
                load.load(batch)
            shutil.copytree(config.data_directory, crashed)
            load.finish()
    finally:
        service.close()
    return replace(config, data_directory=crashed)


def test_a_load_that_dies_mid_way_leaves_indexes_that_rebuild_on_open(
    config: EngineConfig, tmp_path: Path
):
    crashed = crashed_after_committed_batches(config, tmp_path / "tras_el_corte")
    assert len(list(crashed.data_directory.glob(f"*{STALE_SUFFIX}"))) == len(INDEXES)
    loaded = list(range(1, BATCH_ROWS * COMMITTED_BATCHES + 1))
    with Engine(crashed) as reopened:
        assert ids(reopened, "SELECT id FROM t") == loaded
        assert list(crashed.data_directory.glob(f"*{STALE_SUFFIX}")) == []
        radius = f"SELECT id FROM t WHERE distancia(lugar, {CENTER}) < {RADIUS_METERS}"
        nearest = f"SELECT id FROM t ORDER BY distancia(lugar, {CENTER}) LIMIT {NEAREST}"
        assert "SpatialRangeScan" in plan_of(reopened, radius)
        through_rtree = (ids(reopened, radius), ids(reopened, nearest))
        everything = len(loaded)
        hnsw = f"SELECT id FROM t WHERE SIMILAR_TO(emb, [1, 0, 0, 0], k = {everything}) USING HNSW"
        ivf = f"SELECT id FROM t WHERE SIMILAR_TO(emb, [1, 0, 0, 0], k = {everything}) USING IVF"
        assert ids(reopened, hnsw) == ids(reopened, ivf) == loaded
        word = f"SELECT id FROM t WHERE MATCH(texto, '{SEARCHED_WORD}')"
        scanned_word = ids(reopened, f"SELECT id FROM t WHERE texto LIKE '%{SEARCHED_WORD}%'")
        assert scanned_word
        assert ids(reopened, word) == scanned_word
        reopened.execute("DROP INDEX idx_t_lugar")
        assert "SpatialRangeScan" not in plan_of(reopened, radius)
        assert (ids(reopened, radius), ids(reopened, nearest)) == through_rtree
        assert through_rtree[0]
