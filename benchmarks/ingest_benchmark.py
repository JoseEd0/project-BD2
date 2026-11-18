"""Carga masiva por NDJSON contra `INSERT` con parámetros, por HTTP (issue #69).

    uv run --no-sync python -m benchmarks.ingest_benchmark --sizes 1000 10000 50000

Es lo que paga un servicio que llena sus tablas en el gestor —el RAG con decenas de miles
de fragmentos y su embedding de 384 dimensiones— según cómo le mande las filas.

**Contra un servidor de verdad:** uvicorn en otro proceso, arrancado con
`app_from_environment` como en Docker, y un cliente `httpx` con la conexión abierta. El
tiempo es el del cliente, de la primera petición a la última respuesta: incluye convertir
cada fila a JSON, la red local y el servidor. Con el cliente de pruebas de FastAPI no se
mediría la carga por partes, que lee el cuerpo entero antes de entregarlo.

Técnicas:

- **INSERT con parámetros, una fila por petición:** `INSERT INTO t VALUES ($1, $2, …)`, lo
  que haría un servicio que no sabe de lotes. Cada petición es una transacción.
- **INSERT con parámetros, un lote por petición:** `VALUES ($1, …), ($4, …), …` con
  `--batch-rows` filas: la mejor forma de cargar sin la ruta nueva.
- **NDJSON:** `POST /tables/{name}/rows` con el cuerpo enviado por partes, en lotes de
  `--batch-rows` filas, los mismos que el INSERT por lotes.

Escenarios, cada uno sobre una tabla recién creada:

- **sin vector:** `filas (id INT PRIMARY KEY, nombre VARCHAR(24), ciudad VARCHAR(16),
  nota FLOAT)`, las filas de los experimentos de la Parte 1;
- **VECTOR(384), sin índice:** `vectores (id INT PRIMARY KEY, parte INT, emb
  VECTOR(384))`, con los embeddings sintéticos del experimento de columnas VECTOR (ver
  `benchmarks.vectors.mixture`);
- **HNSW creado antes, tabla vacía:** la misma tabla con `CREATE INDEX ... USING HNSW` antes
  de cargar. La carga NDJSON lo construye al final; los INSERT lo mantienen fila a fila;
- **HNSW creado antes, tabla con filas:** con una fila previa, la carga NDJSON también lo
  mantiene fila a fila;
- **HNSW creado después:** la carga NDJSON sin índice más el `CREATE INDEX` al terminar;
- **POINT con un R-Tree,** creado antes (tabla vacía o con una fila) o después: `lugares (id
  INT PRIMARY KEY, lugar POINT)` con puntos al azar en Lima, solo por NDJSON. Con la tabla
  vacía la carga lo construye al final con STR; con filas, inserta cada punto con Guttman.

El INSERT de una fila por petición se mide hasta `--single-row-limit` filas: más allá tarda
minutos y solo confirma lo que ya se ve. FAISS usa `--threads` hilos (uno por omisión, como
el experimento de columnas VECTOR).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from api.settings import (
    CORS_ORIGINS_VARIABLE,
    DATA_DIRECTORY_VARIABLE,
    INGEST_BATCH_ROWS_VARIABLE,
    LOG_LEVEL_VARIABLE,
    MAX_REQUEST_BYTES_VARIABLE,
    SERVICE_KEYS_VARIABLE,
    STATEMENT_TIMEOUT_VARIABLE,
)
from benchmarks.common import (
    CITY_LENGTH,
    NAME_LENGTH,
    Report,
    build_rows,
    load_average,
    machine_description,
    random_stream,
    results_directory,
)
from benchmarks.ingest.names import (
    HNSW_AFTER,
    HNSW_EMPTY,
    HNSW_ROWS,
    INSERT_BATCH,
    INSERT_ROW,
    NDJSON,
    PLAIN,
    REPORT,
    RTREE_AFTER,
    RTREE_EMPTY,
    RTREE_ROWS,
    THROUGHPUT_FIELD,
    VECTOR,
)
from benchmarks.vector_benchmark import (
    DEFAULT_CLUSTERS,
    DEFAULT_INTRINSIC_DIMENSION,
    DEFAULT_NOISE,
    DEFAULT_SPREAD,
)
from benchmarks.vectors.mixture import Mixture
from index.vector.metrics import Vectors
from query.plan import MILLISECONDS_PER_SECOND

DEFAULT_SIZES = (1_000, 10_000, 50_000)
DEFAULT_BATCH_ROWS = 1_000
DEFAULT_SINGLE_ROW_LIMIT = 10_000
DEFAULT_DIMENSION = 384
DEFAULT_REPETITIONS = 1
DEFAULT_THREADS = 1
DEFAULT_SEED = 20261118
LOOPBACK = "127.0.0.1"
ANY_FREE_PORT = 0
CORS_ORIGIN = "http://localhost"
SERVER_LOG_LEVEL = "WARNING"
OPENMP_THREADS_VARIABLE = "OMP_NUM_THREADS"
# Un lote de mil filas con VECTOR(384) viaja en el JSON de un INSERT en unos 8 MiB, justo el
# límite por omisión: el experimento lo sube para que el INSERT por lotes use los mismos
# lotes que la carga NDJSON.
REQUEST_BYTES_LIMIT = 256 * 1024 * 1024
# Un CREATE INDEX sobre 50 000 vectores no se corta, pero el INSERT de un lote grande sí.
STATEMENT_TIMEOUT_SECONDS = 600
SERVER_START_SECONDS = 60.0
SERVER_POLL_SECONDS = 0.05
HEALTH_PATH = "/health"
QUERY_PATH = "/query"
NDJSON_MEDIA_TYPE = "application/x-ndjson"
# Filas por trozo del cuerpo NDJSON: el cliente las convierte a JSON mientras el servidor
# carga las anteriores, como haría un servicio que lee su archivo de a poco.
NDJSON_CHUNK_ROWS = 100
HNSW_INDEX = "idx_vectores_emb"
HNSW_OPTIONS = "WITH (metric = 'cosine')"
FILTER_PARTS = 10
FIRST_ID = 1
SEED_ROW_ID = 0
PLAIN_TABLE = "filas"
VECTOR_TABLE = "vectores"
POINT_TABLE = "lugares"
RTREE_INDEX = "idx_lugares_lugar"
POINTS_STREAM = "puntos"
# El recuadro de Lima del ejemplo de `demos/descargar_lugares.py`.
LIMA_SOUTH, LIMA_NORTH = -12.52, -11.57
LIMA_WEST, LIMA_EAST = -77.20, -76.62


@dataclass(frozen=True, slots=True)
class Settings:
    sizes: tuple[int, ...]
    batch_rows: int
    single_row_limit: int
    repetitions: int
    threads: int
    mixture: Mixture
    seed: int


@dataclass(frozen=True, slots=True)
class Workload:
    """Una tabla del experimento y sus filas: cómo se crea, cómo se llaman sus columnas, la
    fila número `n` como lista de valores JSON y el índice que se crea antes o después."""

    table: str
    create_sql: str
    columns: tuple[str, ...]
    size: int
    row: Callable[[int], list[Any]]
    index_sql: str = ""

    def json_row(self, number: int) -> dict[str, Any]:
        return dict(zip(self.columns, self.row(number), strict=True))


class EngineClient:
    """El gestor por HTTP, con una conexión que se mantiene abierta."""

    def __init__(self, base_url: str) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=None)

    def sql(self, statement: str, parameters: Sequence[Any] = ()) -> dict[str, Any]:
        response = self._http.post(QUERY_PATH, json={"sql": statement, "params": list(parameters)})
        return _successful(response)

    def load(self, table: str, chunks: Iterator[bytes]) -> dict[str, Any]:
        response = self._http.post(
            f"/tables/{table}/rows",
            content=chunks,
            headers={"Content-Type": NDJSON_MEDIA_TYPE},
        )
        return _successful(response)

    def close(self) -> None:
        self._http.close()


def run(settings: Settings, workspace: Path) -> Report:
    """Mide cada tamaño contra un servidor propio cuyos datos viven en `workspace`."""
    report = Report(REPORT, _parameters(settings))
    largest = max(settings.sizes)
    vectors, _ = settings.mixture.collection(largest)
    with _engine_server(workspace, settings) as base_url:
        client = EngineClient(base_url)
        try:
            for size in sorted(settings.sizes):
                print(f"{size} filas")
                _measure_size(report, client, settings, vectors[:size], size)
        finally:
            client.close()
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


def _measure_size(
    report: Report, client: EngineClient, settings: Settings, vectors: Vectors, size: int
) -> None:
    plain = _plain_workload(size, settings.seed)
    embedded = _vector_workload(vectors)
    single_row = size <= settings.single_row_limit
    techniques = (INSERT_ROW, INSERT_BATCH, NDJSON) if single_row else (INSERT_BATCH, NDJSON)
    for technique in techniques:
        record = partial(_record, report, technique, size=size, settings=settings)
        record(PLAIN, partial(_load, client, plain, technique, settings))
        record(VECTOR, partial(_load, client, embedded, technique, settings))
        record(HNSW_EMPTY, partial(_load, client, embedded, technique, settings, index_first=True))
    ndjson = partial(_record, report, NDJSON, size=size, settings=settings)
    ndjson(
        HNSW_ROWS,
        partial(_load, client, embedded, NDJSON, settings, index_first=True, seed_row=True),
    )
    ndjson(HNSW_AFTER, partial(_load_then_index, client, embedded, settings))
    located = _point_workload(size, settings.seed)
    ndjson(RTREE_EMPTY, partial(_load, client, located, NDJSON, settings, index_first=True))
    ndjson(
        RTREE_ROWS,
        partial(_load, client, located, NDJSON, settings, index_first=True, seed_row=True),
    )
    ndjson(RTREE_AFTER, partial(_load_then_index, client, located, settings))


def _record(
    report: Report,
    technique: str,
    scenario: str,
    measure: Callable[[], dict[str, Any]],
    size: int,
    settings: Settings,
) -> None:
    """La mediana de `--repetitions` cargas, con el detalle de la más cercana a ella."""
    outcomes = [measure() for _ in range(settings.repetitions)]
    milliseconds = statistics.median(outcome["milliseconds"] for outcome in outcomes)
    closest = min(outcomes, key=lambda outcome: abs(outcome["milliseconds"] - milliseconds))
    detail = {key: value for key, value in closest.items() if key != "milliseconds"}
    seconds = milliseconds / MILLISECONDS_PER_SECOND
    report.add(
        technique,
        scenario,
        size,
        round(milliseconds, 1),
        **{THROUGHPUT_FIELD: round(size / seconds, 1)},
        **detail,
    )
    print(f"  {scenario} | {technique}: {size / seconds:,.0f} filas/s")


def _load(
    client: EngineClient,
    workload: Workload,
    technique: str,
    settings: Settings,
    index_first: bool = False,
    seed_row: bool = False,
) -> dict[str, Any]:
    _fresh_table(client, workload, index_first, seed_row)
    started = time.perf_counter()
    detail = _send(client, workload, technique, settings.batch_rows)
    milliseconds = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    _require_rows(client, workload, workload.size + int(seed_row))
    return {"milliseconds": milliseconds, **detail}


def _load_then_index(
    client: EngineClient, workload: Workload, settings: Settings
) -> dict[str, Any]:
    _fresh_table(client, workload, index_first=False, seed_row=False)
    started = time.perf_counter()
    detail = _send(client, workload, NDJSON, settings.batch_rows)
    loaded = time.perf_counter()
    client.sql(workload.index_sql)
    finished = time.perf_counter()
    _require_rows(client, workload, workload.size)
    return {
        "milliseconds": (finished - started) * MILLISECONDS_PER_SECOND,
        "carga_ms": round((loaded - started) * MILLISECONDS_PER_SECOND, 1),
        "indice_ms": round((finished - loaded) * MILLISECONDS_PER_SECOND, 1),
        **detail,
    }


def _send(
    client: EngineClient, workload: Workload, technique: str, batch_rows: int
) -> dict[str, Any]:
    if technique == INSERT_ROW:
        statement = _insert_sql(workload, rows=1)
        for number in range(workload.size):
            client.sql(statement, workload.row(number))
        return {"peticiones": workload.size}
    if technique == INSERT_BATCH:
        requests = 0
        for start in range(0, workload.size, batch_rows):
            numbers = range(start, min(start + batch_rows, workload.size))
            values = [value for number in numbers for value in workload.row(number)]
            client.sql(_insert_sql(workload, rows=len(numbers)), values)
            requests += 1
        return {"peticiones": requests}
    summary = client.load(workload.table, _ndjson_chunks(workload))
    if summary["rows_loaded"] != workload.size:
        raise RuntimeError(f"la carga NDJSON dejó {summary['rows_loaded']} filas: {summary}")
    return {
        "peticiones": 1,
        "lotes": summary["batches_committed"],
        "servidor_ms": round(summary["elapsed_ms"], 1),
        "indices_diferidos": len(summary["deferred_indexes"]),
    }


def _ndjson_chunks(workload: Workload) -> Iterator[bytes]:
    for start in range(0, workload.size, NDJSON_CHUNK_ROWS):
        numbers = range(start, min(start + NDJSON_CHUNK_ROWS, workload.size))
        yield "".join(json.dumps(workload.json_row(number)) + "\n" for number in numbers).encode()


def _fresh_table(
    client: EngineClient, workload: Workload, index_first: bool, seed_row: bool
) -> None:
    client.sql(f"DROP TABLE IF EXISTS {workload.table}")
    client.sql(workload.create_sql)
    if index_first:
        client.sql(workload.index_sql)
    if seed_row:
        seed = workload.row(0)
        seed[0] = SEED_ROW_ID
        client.sql(_insert_sql(workload, rows=1), seed)


def _require_rows(client: EngineClient, workload: Workload, expected: int) -> None:
    rows = client.sql(f"SELECT COUNT(*) FROM {workload.table}")["rows"]
    if rows != [[expected]]:
        raise RuntimeError(f"'{workload.table}' quedó con {rows} filas y se esperaban {expected}")


def _insert_sql(workload: Workload, rows: int) -> str:
    width = len(workload.columns)
    groups = (
        "(" + ", ".join(f"${row * width + column + 1}" for column in range(width)) + ")"
        for row in range(rows)
    )
    return f"INSERT INTO {workload.table} VALUES {', '.join(groups)}"


def _plain_workload(size: int, seed: int) -> Workload:
    rows = build_rows(size, seed)
    return Workload(
        table=PLAIN_TABLE,
        create_sql=(
            f"CREATE TABLE {PLAIN_TABLE} (id INT PRIMARY KEY, nombre VARCHAR({NAME_LENGTH}), "
            f"ciudad VARCHAR({CITY_LENGTH}), nota FLOAT)"
        ),
        columns=("id", "nombre", "ciudad", "nota"),
        size=size,
        row=lambda number: [rows[number][0] + FIRST_ID, *rows[number][1:]],
    )


def _vector_workload(vectors: Vectors) -> Workload:
    def row(number: int) -> list[Any]:
        key = FIRST_ID + number
        return [key, key % FILTER_PARTS, vectors[number].tolist()]

    return Workload(
        table=VECTOR_TABLE,
        create_sql=(
            f"CREATE TABLE {VECTOR_TABLE} (id INT PRIMARY KEY, parte INT, "
            f"emb VECTOR({vectors.shape[1]}))"
        ),
        columns=("id", "parte", "emb"),
        size=len(vectors),
        row=row,
        index_sql=f"CREATE INDEX {HNSW_INDEX} ON {VECTOR_TABLE} USING HNSW (emb) {HNSW_OPTIONS}",
    )


def _point_workload(size: int, seed: int) -> Workload:
    generator = random_stream(seed, POINTS_STREAM)
    points = [
        {
            "lat": generator.uniform(LIMA_SOUTH, LIMA_NORTH),
            "lon": generator.uniform(LIMA_WEST, LIMA_EAST),
        }
        for _ in range(size)
    ]
    return Workload(
        table=POINT_TABLE,
        create_sql=f"CREATE TABLE {POINT_TABLE} (id INT PRIMARY KEY, lugar POINT)",
        columns=("id", "lugar"),
        size=size,
        row=lambda number: [FIRST_ID + number, points[number]],
        index_sql=f"CREATE INDEX {RTREE_INDEX} ON {POINT_TABLE} USING RTREE (lugar)",
    )


@contextmanager
def _engine_server(directory: Path, settings: Settings) -> Iterator[str]:
    """Un uvicorn en otro proceso, sobre `directory`, hasta salir del bloque."""
    port = _free_port()
    environment = {key: value for key, value in os.environ.items() if key != SERVICE_KEYS_VARIABLE}
    environment.update(
        {
            DATA_DIRECTORY_VARIABLE: str(directory),
            CORS_ORIGINS_VARIABLE: CORS_ORIGIN,
            STATEMENT_TIMEOUT_VARIABLE: str(STATEMENT_TIMEOUT_SECONDS),
            MAX_REQUEST_BYTES_VARIABLE: str(REQUEST_BYTES_LIMIT),
            INGEST_BATCH_ROWS_VARIABLE: str(settings.batch_rows),
            LOG_LEVEL_VARIABLE: SERVER_LOG_LEVEL,
            OPENMP_THREADS_VARIABLE: str(settings.threads),
        }
    )
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "--factory",
        "api.main:app_from_environment",
        "--host",
        LOOPBACK,
        "--port",
        str(port),
        "--log-level",
        SERVER_LOG_LEVEL.lower(),
    ]
    server = subprocess.Popen(command, env=environment)
    base_url = f"http://{LOOPBACK}:{port}"
    try:
        _wait_until_healthy(base_url, server)
        yield base_url
    finally:
        server.terminate()
        server.wait()


def _wait_until_healthy(base_url: str, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + SERVER_START_SECONDS
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"el servidor terminó al arrancar con código {server.returncode}")
        try:
            if httpx.get(base_url + HEALTH_PATH).status_code == httpx.codes.OK:
                return
        except httpx.TransportError:
            pass
        time.sleep(SERVER_POLL_SECONDS)
    raise RuntimeError(f"el servidor no respondió en {SERVER_START_SECONDS:g} s")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, ANY_FREE_PORT))
        port: int = probe.getsockname()[1]
        return port


def _successful(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != httpx.codes.OK:
        raise RuntimeError(
            f"{response.request.url.path} respondió {response.status_code}: {response.text}"
        )
    body: dict[str, Any] = response.json()
    return body


def _parameters(settings: Settings) -> dict[str, Any]:
    mixture = settings.mixture
    return {
        "tamanos": list(settings.sizes),
        "filas_por_lote": settings.batch_rows,
        "una_fila_por_peticion_hasta": settings.single_row_limit,
        "repeticiones": settings.repetitions,
        "hilos_faiss": settings.threads,
        "servidor": "uvicorn en otro proceso, cliente httpx con conexión persistente",
        "dimension": mixture.dimension,
        "mezcla": {
            "grupos": mixture.clusters,
            "dimension_intrinseca": mixture.intrinsic_dimension,
            "dispersion": mixture.spread,
            "ruido": mixture.noise,
            "semilla": mixture.seed,
        },
        "hnsw": HNSW_OPTIONS,
        "puntos": "uniformes en el recuadro de Lima, R-Tree sobre la columna POINT",
        "semilla_filas": settings.seed,
        "versiones": {"httpx": httpx.__version__, "numpy": np.__version__},
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--batch-rows", type=int, default=DEFAULT_BATCH_ROWS)
    parser.add_argument("--single-row-limit", type=int, default=DEFAULT_SINGLE_ROW_LIMIT)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--dimension", type=int, default=DEFAULT_DIMENSION)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    settings = Settings(
        sizes=tuple(arguments.sizes),
        batch_rows=arguments.batch_rows,
        single_row_limit=arguments.single_row_limit,
        repetitions=arguments.repetitions,
        threads=arguments.threads,
        mixture=Mixture(
            dimension=arguments.dimension,
            clusters=DEFAULT_CLUSTERS,
            intrinsic_dimension=DEFAULT_INTRINSIC_DIMENSION,
            spread=DEFAULT_SPREAD,
            noise=DEFAULT_NOISE,
            seed=arguments.seed,
        ),
        seed=arguments.seed,
    )
    workspace = Path(tempfile.mkdtemp(prefix="minigestor-ingesta-"))
    try:
        report = run(settings, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    print(report.render())
    print(f"resultados en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
