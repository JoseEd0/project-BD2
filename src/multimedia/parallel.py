"""La extracción de muchos archivos, en este proceso o repartida en un pool de procesos.

SIFT y MFCC son cálculo en Python y C que no suelta el GIL de forma fiable, así que el
paralelismo es de procesos. Los procesos se crean siempre con `spawn`, también en Linux:
hacer `fork` de un servidor con hilos (el API, los de OpenCV) puede heredar un candado
tomado.
"""

from __future__ import annotations

import multiprocessing
from collections import deque
from collections.abc import Iterable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from config import ConfigurationError, EngineConfig

from .descriptors import Descriptors
from .errors import MediaError
from .extraction import extract_features
from .image import confine_opencv_to_one_thread

# Con dos lotes por proceso, cada uno tiene el siguiente esperando mientras el anterior
# vuelve, y nunca hay más de esos lotes de descriptores en memoria sin entregar.
IN_FLIGHT_CHUNKS_PER_WORKER = 2
WORKER_START_METHOD = "spawn"
MIN_WORKERS = 1


@dataclass(frozen=True, slots=True)
class ExtractedFeatures:
    """Los descriptores de un archivo, como los da `extract_features`."""

    path: Path
    descriptors: Descriptors


@dataclass(frozen=True, slots=True)
class FailedExtraction:
    """Un archivo que no se pudo describir y por qué."""

    path: Path
    error: MediaError


ExtractionOutcome = ExtractedFeatures | FailedExtraction


def extract_each(paths: Iterable[Path], config: EngineConfig) -> Iterator[ExtractionOutcome]:
    """Extrae los archivos de `paths` uno a uno en este proceso, a medida que se piden.

    Un archivo que no se puede describir no corta el flujo: da un `FailedExtraction`.
    Cualquier otro error es un fallo del motor y se propaga.
    """
    for path in paths:
        try:
            yield ExtractedFeatures(path, extract_features(path, config))
        except MediaError as error:
            yield FailedExtraction(path, error)


def extract_in_parallel(
    paths: Iterable[Path], config: EngineConfig, workers: int
) -> Iterator[ExtractionOutcome]:
    """Lo mismo que `extract_each`, en el mismo orden, repartido en `workers` procesos.

    `paths` se consume de a poco, en lotes de `config.feature_chunk_files`, y nunca hay
    más de `workers * IN_FLIGHT_CHUNKS_PER_WORKER` lotes pedidos y sin entregar: la memoria
    no crece con el número de archivos. Si quien itera cierra el iterador antes del
    final, los lotes que no empezaron se cancelan.

    Raises:
        ConfigurationError: al llamarla, si `workers` es menor que 1.
    """
    if not workers >= MIN_WORKERS:
        raise ConfigurationError(f"workers tiene que ser al menos {MIN_WORKERS}, no {workers}")
    return _stream_in_pool(paths, config, workers)


def _stream_in_pool(
    paths: Iterable[Path], config: EngineConfig, workers: int
) -> Iterator[ExtractionOutcome]:
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context(WORKER_START_METHOD),
        initializer=confine_opencv_to_one_thread,
    )
    max_in_flight = workers * IN_FLIGHT_CHUNKS_PER_WORKER
    pending: deque[Future[list[ExtractionOutcome]]] = deque()
    try:
        for chunk in _chunks(paths, config.feature_chunk_files):
            if len(pending) == max_in_flight:
                yield from pending.popleft().result()
            pending.append(pool.submit(_extract_chunk, chunk, config))
        while pending:
            yield from pending.popleft().result()
    finally:
        pool.shutdown(cancel_futures=True)


def _extract_chunk(paths: tuple[Path, ...], config: EngineConfig) -> list[ExtractionOutcome]:
    return list(extract_each(paths, config))


def _chunks(paths: Iterable[Path], size: int) -> Iterator[tuple[Path, ...]]:
    remaining = iter(paths)
    while chunk := tuple(islice(remaining, size)):
        yield chunk
