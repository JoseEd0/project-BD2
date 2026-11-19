"""El catálogo del servicio de copyright (issue #75): descargarlo, cargarlo e indexarlo.

    uv run --no-sync python -m benchmarks.copyright_catalog_benchmark \\
        --data-dir data/datasets --sizes 1000 2000 4000 8000

1. **Descarga.** `songcheck.fma.prepare_catalog` deja `fma_small` completo y verificado en
   `<data-dir>/fma_small/` (lo que ya está sano no se vuelve a pedir; el JSON dice cuántas
   pistas se bajaron en esta ejecución).
2. **Carga, por tamaño de catálogo.** Una muestra de `--sizes` canciones (con `--seed`; el
   catálogo entero si el tamaño lo alcanza) se carga en un gestor nuevo con el código del
   trabajo de carga del servicio, `songcheck.seed.load_catalog`: `cr_canciones` y
   `cr_segmentos` por NDJSON y el `CREATE INDEX ... USING HNSW` de los tramos. Después se
   repite, para medir una segunda ejecución, y se mide el disco del gestor.

**El gestor corre en este proceso**, servido por uvicorn en un hilo y consultado por HTTP
con el cliente del servicio, igual que en producción salvo el proceso. Así se puede partir
el `CREATE INDEX`: se envuelven con un cronómetro el entrenamiento del vocabulario
(`Vocabulary.train`, k-means de FAISS), la cuantización (`Vocabulary.word_counts`) y el
llenado y guardado del HNSW (`IndexedVectors.rebuild` y `save`); la extracción de MFCC, que
corre en los procesos de extracción, es el resto.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import socket
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

import uvicorn
from fastapi import FastAPI

from api.main import create_app
from benchmarks.common import Report, load_average, machine_description, results_directory
from config import EngineConfig
from index.similarity.vectors import IndexedVectors
from minigestor_client import NO_RETRIES, AsyncClient, Timeouts
from multimedia.vocabulary import Vocabulary
from query.plan import MILLISECONDS_PER_SECOND
from songcheck.catalog import CatalogSong, dataset_directory, read_catalog
from songcheck.fma.prepare import FmaSources, prepare_catalog
from songcheck.schema import SEGMENT_INDEX_NAME, SEGMENTS_TABLE, SONGS_TABLE
from songcheck.seed import CatalogLoad, load_catalog, segment_count
from songcheck.windows import WINDOWING

REPORT_NAME = "copyright_catalogo"
DOWNLOAD = "descarga"
LOAD = "carga"
INDEX_PARTS = "partes del CREATE INDEX"
SECOND_RUN = "segunda ejecución"
DISK = "disco"
SONGS_LOAD = f"{SONGS_TABLE} (NDJSON)"
SEGMENTS_LOAD = f"{SEGMENTS_TABLE} (NDJSON)"
INDEX_BUILD = "CREATE INDEX HNSW"
TOTAL = "total"
EXTRACTION = "extraer MFCC"
VOCABULARY = "vocabulario (k-means)"
QUANTIZATION = "cuantizar"
HNSW_FILL = "llenar y guardar el HNSW"
TABLES_DISK = "tablas"
FEATURES_DISK = "almacén de características"
HNSW_DISK = "HNSW"
KIB_FIELD = "kib"
DEFAULT_SIZES = (1_000, 2_000, 4_000, 8_000)
DEFAULT_SEED = 20261118
DEFAULT_THREADS = 8
LOCALHOST = "127.0.0.1"
SERVER_START_SECONDS = 30.0
SERVER_POLL_SECONDS = 0.01
# El CREATE INDEX del catálogo entero tarda minutos: el cliente lo espera.
LONG_SECONDS = 3600.0
BYTES_PER_KIB = 1024
FEATURES_MARKER = ".idx.features."
HNSW_SUFFIX = ".idx"
DIGITS = 3

Result = TypeVar("Result")


class StageClock:
    """Segundos acumulados por etapa, de funciones envueltas con `wrap`."""

    def __init__(self) -> None:
        self.seconds: defaultdict[str, float] = defaultdict(float)

    def wrap(self, stage: str, function: Callable[..., Result]) -> Callable[..., Result]:
        @wraps(function)
        def timed(*arguments: Any, **keywords: Any) -> Result:
            started = time.perf_counter()
            try:
                return function(*arguments, **keywords)
            finally:
                self.seconds[stage] += time.perf_counter() - started

        return timed

    def reset(self) -> None:
        self.seconds.clear()


@contextmanager
def stage_clock() -> Iterator[StageClock]:
    """Cronometra las etapas del `CREATE INDEX` mientras dura el bloque."""
    clock = StageClock()
    originals = {
        (Vocabulary, "word_counts"): Vocabulary.word_counts,
        (IndexedVectors, "rebuild"): IndexedVectors.rebuild,
        (IndexedVectors, "save"): IndexedVectors.save,
    }
    train = Vocabulary.__dict__["train"]
    Vocabulary.word_counts = clock.wrap(QUANTIZATION, Vocabulary.word_counts)  # type: ignore[method-assign]
    IndexedVectors.rebuild = clock.wrap(HNSW_FILL, IndexedVectors.rebuild)  # type: ignore[method-assign]
    IndexedVectors.save = clock.wrap(HNSW_FILL, IndexedVectors.save)  # type: ignore[method-assign]
    Vocabulary.train = classmethod(clock.wrap(VOCABULARY, train.__func__))  # type: ignore[assignment]
    try:
        yield clock
    finally:
        for (owner, name), original in originals.items():
            setattr(owner, name, original)
        Vocabulary.train = train  # type: ignore[method-assign]


@contextmanager
def serving(app: FastAPI) -> Iterator[str]:
    """`app` servida por uvicorn en un hilo, en un puerto libre, mientras dura el bloque."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as listener:
        listener.bind((LOCALHOST, 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + SERVER_START_SECONDS
        while not server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("uvicorn no arrancó")
            time.sleep(SERVER_POLL_SECONDS)
        try:
            yield f"http://{LOCALHOST}:{port}"
        finally:
            server.should_exit = True
            thread.join()


def sample(songs: Sequence[CatalogSong], size: int, seed: int) -> list[CatalogSong]:
    """`size` canciones al azar con la semilla, en orden de id; todas si no alcanzan."""
    if size >= len(songs):
        return sorted(songs, key=lambda song: song.track_id)
    chosen = random.Random(seed).sample(list(songs), size)
    return sorted(chosen, key=lambda song: song.track_id)


async def seed_engine(url: str, songs: Sequence[CatalogSong], media_root: str) -> CatalogLoad:
    long = Timeouts(
        read_seconds=LONG_SECONDS, write_seconds=LONG_SECONDS, load_seconds=LONG_SECONDS
    )
    async with (
        AsyncClient(url, timeouts=long) as engine,
        AsyncClient(url, timeouts=long, retry=NO_RETRIES) as indexer,
    ):
        return await load_catalog(engine, indexer, songs, media_root)


def disk_kib(directory: Path) -> dict[str, float]:
    """KiB del directorio del gestor: tablas, almacén de características y HNSW."""
    totals = dict.fromkeys((TABLES_DISK, FEATURES_DISK, HNSW_DISK), 0)
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        if FEATURES_MARKER in path.name:
            group = FEATURES_DISK
        elif path.name.endswith(HNSW_SUFFIX) and SEGMENT_INDEX_NAME in path.name:
            group = HNSW_DISK
        else:
            group = TABLES_DISK
        totals[group] += path.stat().st_size
    return {group: round(size / BYTES_PER_KIB, 1) for group, size in totals.items()}


def measure_download(report: Report, data_directory: Path, threads: int) -> list[CatalogSong]:
    started = time.perf_counter()
    catalog = prepare_catalog(data_directory, FmaSources(), threads)
    elapsed = _milliseconds(started)
    songs = read_catalog(catalog.catalog)
    audio_bytes = sum(song.size_bytes for song in songs)
    report.add(
        DOWNLOAD,
        TOTAL,
        catalog.songs,
        elapsed,
        bajadas=catalog.fetched,
        bajado_kib=round(catalog.fetched_bytes / BYTES_PER_KIB, 1),
        ya_sanas=catalog.already_intact,
        ilegibles=catalog.unreadable,
        audio_kib=round(audio_bytes / BYTES_PER_KIB, 1),
    )
    return songs


def measure_size(
    report: Report, songs: Sequence[CatalogSong], data_directory: Path, workspace: Path
) -> None:
    size = len(songs)
    media_root = str(dataset_directory(data_directory.resolve()))
    engine_directory = workspace / str(size)
    engine_directory.mkdir()
    config = EngineConfig(
        data_directory=engine_directory,
        import_directories=(data_directory.resolve(),),
    )
    app = create_app(config)
    try:
        with stage_clock() as clock, serving(app) as url:
            started = time.perf_counter()
            first = asyncio.run(seed_engine(url, songs, media_root))
            seed_ms = _milliseconds(started)
            parts = dict(clock.seconds)
            started = time.perf_counter()
            second = asyncio.run(seed_engine(url, songs, media_root))
            repeat_ms = _milliseconds(started)
    finally:
        app.state.service.close()
    segments = segment_count(songs)
    report.add(SONGS_LOAD, LOAD, size, first.songs.seconds * MILLISECONDS_PER_SECOND, filas=size)
    report.add(
        SEGMENTS_LOAD, LOAD, size, first.segments.seconds * MILLISECONDS_PER_SECOND, filas=segments
    )
    report.add(
        INDEX_BUILD, LOAD, size, first.index.seconds * MILLISECONDS_PER_SECOND, filas=segments
    )
    report.add(TOTAL, LOAD, size, seed_ms, filas=segments)
    _add_index_parts(report, size, first.index.seconds, parts)
    report.add(
        TOTAL,
        SECOND_RUN,
        size,
        repeat_ms,
        filas_cargadas=second.songs.loaded_rows + second.segments.loaded_rows,
        indice_creado=second.index.created,
    )
    for group, kib in disk_kib(engine_directory).items():
        report.add(group, DISK, size, 0.0, **{KIB_FIELD: kib})


def _add_index_parts(
    report: Report, size: int, index_seconds: float, parts: dict[str, float]
) -> None:
    known = sum(parts.get(stage, 0.0) for stage in (VOCABULARY, QUANTIZATION, HNSW_FILL))
    stages = {
        EXTRACTION: index_seconds - known,
        VOCABULARY: parts.get(VOCABULARY, 0.0),
        QUANTIZATION: parts.get(QUANTIZATION, 0.0),
        HNSW_FILL: parts.get(HNSW_FILL, 0.0),
    }
    for stage, seconds in stages.items():
        report.add(stage, INDEX_PARTS, size, round(seconds * MILLISECONDS_PER_SECOND, DIGITS))


def run(arguments: argparse.Namespace) -> Report:
    report = Report(
        name=REPORT_NAME,
        parameters={
            "sizes": list(arguments.sizes),
            "seed": arguments.seed,
            "threads": arguments.threads,
            "window_seconds": WINDOWING.window_seconds,
            "hop_seconds": WINDOWING.hop_seconds,
            "machine": machine_description(),
        },
    )
    songs = measure_download(report, arguments.data_dir, arguments.threads)
    with tempfile.TemporaryDirectory(dir=arguments.workspace) as workspace:
        for size in sorted(set(arguments.sizes)):
            report.parameters[f"load_average_{size}"] = load_average()
            measure_size(
                report, sample(songs, size, arguments.seed), arguments.data_dir, Path(workspace)
            )
    return report


def _milliseconds(started: float) -> float:
    return round((time.perf_counter() - started) * MILLISECONDS_PER_SECOND, DIGITS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="carpeta de datasets")
    parser.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS, help="descargas a la vez")
    parser.add_argument(
        "--workspace", type=Path, default=None, help="dónde crear los gestores (temporales)"
    )
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    report = run(arguments)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
