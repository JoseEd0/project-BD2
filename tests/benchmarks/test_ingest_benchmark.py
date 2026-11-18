"""El experimento de la carga masiva, en pequeño y contra un uvicorn de verdad: no mide
nada, comprueba que cada técnica carga todas sus filas en cada escenario."""

from pathlib import Path

from benchmarks.ingest.names import (
    HNSW_AFTER,
    HNSW_EMPTY,
    HNSW_ROWS,
    INSERT_BATCH,
    INSERT_ROW,
    NDJSON,
    PLAIN,
    RTREE_AFTER,
    RTREE_EMPTY,
    RTREE_ROWS,
    THROUGHPUT_FIELD,
    VECTOR,
)
from benchmarks.ingest_benchmark import Settings, run
from benchmarks.vectors.mixture import Mixture

MIXTURE = Mixture(dimension=8, clusters=4, intrinsic_dimension=2, spread=1.0, noise=0.5, seed=3)
SETTINGS = Settings(
    sizes=(12, 30),
    batch_rows=8,
    single_row_limit=12,
    repetitions=1,
    threads=1,
    mixture=MIXTURE,
    seed=5,
)
EVERY_TECHNIQUE = {PLAIN, VECTOR, HNSW_EMPTY}
NDJSON_ONLY = {HNSW_ROWS, HNSW_AFTER, RTREE_EMPTY, RTREE_ROWS, RTREE_AFTER}


def test_every_technique_loads_every_row_of_every_scenario(tmp_path: Path):
    report = run(SETTINGS, tmp_path)
    measured = {(item.technique, item.operation, item.dataset_size) for item in report.measurements}
    expected = {
        *(
            (technique, scenario, size)
            for technique in (INSERT_BATCH, NDJSON)
            for scenario in EVERY_TECHNIQUE
            for size in SETTINGS.sizes
        ),
        *((INSERT_ROW, scenario, 12) for scenario in EVERY_TECHNIQUE),
        *((NDJSON, scenario, size) for scenario in NDJSON_ONLY for size in SETTINGS.sizes),
    }
    assert measured == expected
    assert all(item.extra[THROUGHPUT_FIELD] > 0 for item in report.measurements)
    deferred = {
        item.operation: item.extra["indices_diferidos"]
        for item in report.measurements
        if item.technique == NDJSON and item.dataset_size == 30
    }
    assert deferred[HNSW_EMPTY] == deferred[RTREE_EMPTY] == 1
    assert deferred[HNSW_ROWS] == deferred[RTREE_ROWS] == deferred[VECTOR] == 0
    after = next(item for item in report.measurements if item.operation == HNSW_AFTER)
    assert after.extra["carga_ms"] + after.extra["indice_ms"] <= after.milliseconds + 1
