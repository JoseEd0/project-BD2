"""Carga de la tienda (#80): lo que tarda cada parte y cuánto ocupa en el gestor.

    uv run --no-sync python -m benchmarks.shop_seed_benchmark --data-dir data/datasets \\
        --models data/models/shop --engine-data-dir data/tienda-gestor

Corre `shop.seed` —el mismo trabajo que se despliega— contra un gestor de verdad: uvicorn en
otro proceso, con el directorio de datos `--engine-data-dir` (vacío; por omisión, uno
temporal) y la carpeta de datasets como directorio de importación, para que lea las fotos.
Mide, desde el cliente:

- cada tabla por carga masiva NDJSON, de la primera fila a la respuesta, que llega después
  de los índices que el gestor construye al final (el invertido de los productos, el R-Tree
  de las tiendas, el HNSW de CLIP);
- cada uno de esos índices **por separado**, borrándolo y volviendo a crearlo con su
  migración sobre la tabla llena: `CREATE INDEX` construye con todas las filas por el mismo
  camino que la carga (`Table._build_from_rows`), y el resultado es el mismo índice;
- el `CREATE INDEX` de SIFT: extraer, entrenar el vocabulario, cuantizar y construir el HNSW;
- los embeddings de CLIP de todas las fotos, con la caché en `--embeddings-dir` (por
  omisión, una vacía: se miden todas);
- una **segunda ejecución**, que no debe repetir nada;
- el **espacio** en disco de cada tabla, separado en datos e índices, con el gestor ya
  apagado (el índice de vectores se escribe al cerrar).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime

from api.settings import IMPORT_DIRECTORIES_VARIABLE
from benchmarks.common import (
    Report,
    machine_description,
    repository_path,
    results_directory,
)
from benchmarks.engine_process import engine_server
from query.plan import MILLISECONDS_PER_SECOND
from shop.catalog.schema import (
    CLIP,
    CLIP_INDEX,
    INVENTORY,
    INVENTORY_INDEX,
    PRODUCTS,
    SCHEMA,
    SIFT_INDEX_NAME,
    STORES,
    STORES_INDEX,
    TEXT_INDEX,
)
from shop.clip.models import HUB_REPOSITORY, HUB_REVISION
from shop.seed import SeedReport, run_seed
from shop.seed.__main__ import seed_client
from shop.settings import (
    DEFAULT_CLIP_BATCH_SIZE,
    DEFAULT_PREPROCESS_WORKERS,
    DEFAULT_PROGRESS_ROWS,
    SeedSettings,
)

REPORT_NAME = "tienda_carga"
LOAD = "carga"
SIFT = "SIFT + HNSW"
CLIP_EMBEDDINGS = "CLIP (embeddings)"
MIGRATIONS = "migraciones"
TOTAL = "total"
FIRST_RUN = "primera ejecución"
SECOND_RUN = "segunda ejecución"
SPACE = "espacio"
TIME_FIELD = "segundos"
WORK_FIELD = "calculo_segundos"
INDEX_BUILD = "CREATE INDEX"
DATA_FIELD = "datos_mib"
INDEX_FIELD = "indices_total_mib"
BYTES_PER_MIB = 1024.0 * 1024.0
# El gestor no pide clave si no se le configuró ninguna (ver `engine_process`); el cliente
# la exige igual.
UNUSED_KEY = "sin-clave"
FILE_PART_SEPARATOR = "."
INDEX_SUFFIX = "idx"
DIGITS = 3
REBUILT_INDEXES = (
    (TEXT_INDEX, PRODUCTS),
    (STORES_INDEX, STORES),
    (CLIP_INDEX, CLIP),
    (INVENTORY_INDEX, INVENTORY),
)
LOG_FORMAT = "%(asctime)s %(message)s"
# Una línea por petición taparía el avance de la carga.
HTTP_LOGGER = "httpx"


@dataclass(frozen=True, slots=True)
class Arguments:
    data_dir: Path
    models: Path
    engine_data_dir: Path | None
    embeddings_dir: Path | None
    clip_batch_size: int
    preprocess_workers: int
    output: Path


def run(arguments: Arguments) -> Report:
    report = Report(REPORT_NAME, _parameters(arguments))
    with _directory(arguments.engine_data_dir) as engine_directory, _directory(
        arguments.embeddings_dir
    ) as embeddings:
        environment = {IMPORT_DIRECTORIES_VARIABLE: str(arguments.data_dir.resolve())}
        with engine_server(engine_directory, environment) as url:
            settings = _settings(arguments, url, embeddings)
            first = asyncio.run(_seed(settings))
            second = asyncio.run(_seed(settings))
            builds = asyncio.run(_rebuild_indexes(settings))
        _record_run(report, first)
        for index, rows, seconds in builds:
            report.add(index, INDEX_BUILD, rows, _ms(seconds), **{WORK_FIELD: _rounded(seconds)})
        report.add(TOTAL, SECOND_RUN, 0, second.total_seconds * MILLISECONDS_PER_SECOND)
        record_space(report, engine_directory)
    return report


async def _seed(settings: SeedSettings) -> SeedReport:
    async with seed_client(settings) as engine:
        return await run_seed(engine, settings)


async def _rebuild_indexes(settings: SeedSettings) -> list[tuple[str, int, float]]:
    """`(índice, filas, segundos)` de volver a crear cada índice que la carga construye al
    final, con su misma migración."""
    builds = []
    async with seed_client(settings) as engine:
        counts = {table.name: table.row_count for table in await engine.tables()}
        for index, table in REBUILT_INDEXES:
            (statement,) = [sql for sql in SCHEMA if f" {index} ON {table} " in sql]
            await engine.execute(f"DROP INDEX {index} ON {table}")
            started = time.perf_counter()
            await engine.execute(statement)
            builds.append((index, counts[table], time.perf_counter() - started))
    return builds


def _record_run(report: Report, seed: SeedReport) -> None:
    report.add(MIGRATIONS, FIRST_RUN, 0, _ms(seed.migrations_seconds))
    for load in seed.tables:
        report.add(
            load.table,
            LOAD,
            load.rows,
            _ms(load.seconds),
            **{
                TIME_FIELD: round(load.seconds, DIGITS),
                "filas_por_segundo": round(load.rows / load.seconds, 1) if load.seconds else 0,
                "indices_al_final": list(load.deferred_indexes),
            },
        )
    for index in seed.indexes:
        seconds = round(index.seconds, DIGITS)
        report.add(SIFT, index.index, 0, _ms(index.seconds), **{WORK_FIELD: seconds})
    if seed.embeddings is not None:
        step = seed.embeddings
        report.add(
            CLIP_EMBEDDINGS,
            LOAD,
            step.images,
            _ms(step.seconds),
            **{
                WORK_FIELD: round(step.seconds, DIGITS),
                "calculadas": step.computed,
                "de_la_cache": step.cached,
                "fotos_por_segundo": round(step.computed / step.seconds, 1) if step.seconds else 0,
            },
        )
    report.add(TOTAL, FIRST_RUN, 0, _ms(seed.total_seconds))


def record_space(report: Report, directory: Path) -> None:
    """Bytes de cada tabla en el directorio del gestor: un archivo `<tabla>.<índice>.idx…` es
    de un índice; `<tabla>.heap`, `.seq`, `.text` y los demás, de la tabla."""
    data: dict[str, int] = defaultdict(int)
    indexes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total = 0
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith(FILE_PART_SEPARATOR):
            continue
        size = path.stat().st_size
        total += size
        table, *rest = path.name.split(FILE_PART_SEPARATOR)
        if INDEX_SUFFIX in rest:
            indexes[table][rest[0]] += size
        else:
            data[table] += size
    for table in sorted(data):
        report.add(
            table,
            SPACE,
            0,
            0.0,
            **{
                DATA_FIELD: _mib(data[table]),
                INDEX_FIELD: _mib(sum(indexes[table].values())),
                "indices_mib": {name: _mib(size) for name, size in indexes[table].items()},
            },
        )
    report.add(TOTAL, SPACE, 0, 0.0, mib=_mib(total))


def _mib(size: int) -> float:
    return round(size / BYTES_PER_MIB, DIGITS)


def _settings(arguments: Arguments, url: str, embeddings: Path) -> SeedSettings:
    return SeedSettings(
        engine_url=url,  # type: ignore[arg-type]
        engine_key=UNUSED_KEY,  # type: ignore[arg-type]
        models_dir=arguments.models,
        data_dir=arguments.data_dir,
        embeddings_dir=embeddings,
        clip_batch_size=arguments.clip_batch_size,
        preprocess_workers=arguments.preprocess_workers,
        progress_rows=DEFAULT_PROGRESS_ROWS,
    )


@contextmanager
def _directory(chosen: Path | None) -> Iterator[Path]:
    """`chosen`, que tiene que estar vacío o no existir, o uno temporal que se borra."""
    if chosen is None:
        with tempfile.TemporaryDirectory() as scratch:
            yield Path(scratch)
        return
    chosen.mkdir(parents=True, exist_ok=True)
    if any(chosen.iterdir()):
        raise SystemExit(f"{chosen} no está vacío: la medida empieza con un gestor sin nada")
    yield chosen


def _rounded(seconds: float) -> float:
    return round(seconds, DIGITS)


def _ms(seconds: float) -> float:
    return round(seconds * MILLISECONDS_PER_SECOND, DIGITS)


def _parameters(arguments: Arguments) -> dict[str, Any]:
    return {
        "datos": repository_path(arguments.data_dir),
        "gestor": "uvicorn en otro proceso, directorio de datos vacío",
        "cache_de_embeddings": "vacía" if arguments.embeddings_dir is None else "la indicada",
        "fotos_por_lote": arguments.clip_batch_size,
        "hilos_de_preprocesado": arguments.preprocess_workers,
        "modelo": f"{HUB_REPOSITORY}@{HUB_REVISION[:7]} (float32)",
        "indice_sift": SIFT_INDEX_NAME,
        "versiones": {"onnxruntime": onnxruntime.__version__, "numpy": np.__version__},
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--engine-data-dir", type=Path)
    parser.add_argument("--embeddings-dir", type=Path)
    parser.add_argument("--clip-batch-size", type=int, default=DEFAULT_CLIP_BATCH_SIZE)
    parser.add_argument("--preprocess-workers", type=int, default=DEFAULT_PREPROCESS_WORKERS)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    parsed = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.getLogger(HTTP_LOGGER).setLevel(logging.WARNING)
    report = run(Arguments(**vars(parsed)))
    print(report.render())
    print(f"resultados en {report.save(parsed.output)}")


if __name__ == "__main__":
    main()
