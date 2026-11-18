"""La extracción de muchos archivos, en este proceso o repartida en un pool de procesos.

SIFT y MFCC son cálculo en Python y C que no suelta el GIL de forma fiable, así que el
paralelismo es de procesos. Los procesos se crean siempre con `spawn`, también en Linux:
hacer `fork` de un servidor con hilos (el API, los de OpenCV) puede heredar un candado
tomado.

Los tramos seguidos de un mismo audio (`pista.mp3#t=0,10`, `pista.mp3#t=5,15`, ...) se
recortan de una sola decodificación del archivo, y un lote nunca los separa: un archivo se
decodifica una vez aunque dé muchas filas.
"""

from __future__ import annotations

import multiprocessing
from collections import deque
from collections.abc import Iterable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from itertools import groupby, islice
from pathlib import Path

from config import ConfigurationError, EngineConfig

from .audio import decode_audio, describe_fragment
from .descriptors import Descriptors
from .errors import MediaError
from .extraction import extract_features
from .fragments import MediaReference
from .image import confine_opencv_to_one_thread
from .media import MediaKind, media_kind_of

# Con dos lotes por proceso, cada uno tiene el siguiente esperando mientras el anterior
# vuelve, y nunca hay más de esos lotes de descriptores en memoria sin entregar.
IN_FLIGHT_CHUNKS_PER_WORKER = 2
WORKER_START_METHOD = "spawn"
MIN_WORKERS = 1


@dataclass(frozen=True, slots=True)
class ExtractedFeatures:
    """Los descriptores de un archivo o de su tramo, como los da `extract_features`."""

    reference: MediaReference
    descriptors: Descriptors


@dataclass(frozen=True, slots=True)
class FailedExtraction:
    """Un archivo o un tramo que no se pudo describir y por qué."""

    reference: MediaReference
    error: MediaError


ExtractionOutcome = ExtractedFeatures | FailedExtraction


def extract_each(
    references: Iterable[MediaReference], config: EngineConfig
) -> Iterator[ExtractionOutcome]:
    """Extrae los archivos o tramos de `references` uno a uno en este proceso, a medida que
    se piden. Varios tramos seguidos de un mismo audio salen de una sola decodificación.

    Un archivo que no se puede describir no corta el flujo: da un `FailedExtraction`.
    Cualquier otro error es un fallo del motor y se propaga.
    """
    for _, same_file in groupby(references, key=_file_of):
        yield from _extract_file(list(same_file), config)


def extract_in_parallel(
    references: Iterable[MediaReference], config: EngineConfig, workers: int
) -> Iterator[ExtractionOutcome]:
    """Lo mismo que `extract_each`, en el mismo orden, repartido en `workers` procesos.

    `references` se consume de a poco, en lotes de `config.feature_chunk_files` archivos
    (con todos sus tramos seguidos), y nunca hay más de
    `workers * IN_FLIGHT_CHUNKS_PER_WORKER` lotes pedidos y sin entregar: la memoria no
    crece con el número de archivos. Si quien itera cierra el iterador antes del final, los
    lotes que no empezaron se cancelan.

    Raises:
        ConfigurationError: al llamarla, si `workers` es menor que 1.
    """
    if not workers >= MIN_WORKERS:
        raise ConfigurationError(f"workers tiene que ser al menos {MIN_WORKERS}, no {workers}")
    return _stream_in_pool(references, config, workers)


def _stream_in_pool(
    references: Iterable[MediaReference], config: EngineConfig, workers: int
) -> Iterator[ExtractionOutcome]:
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context(WORKER_START_METHOD),
        initializer=confine_opencv_to_one_thread,
    )
    max_in_flight = workers * IN_FLIGHT_CHUNKS_PER_WORKER
    pending: deque[Future[list[ExtractionOutcome]]] = deque()
    try:
        for chunk in _chunks(references, config.feature_chunk_files):
            if len(pending) == max_in_flight:
                yield from pending.popleft().result()
            pending.append(pool.submit(_extract_chunk, chunk, config))
        while pending:
            yield from pending.popleft().result()
    finally:
        pool.shutdown(cancel_futures=True)


def _extract_chunk(
    references: tuple[MediaReference, ...], config: EngineConfig
) -> list[ExtractionOutcome]:
    return list(extract_each(references, config))


def _chunks(
    references: Iterable[MediaReference], files: int
) -> Iterator[tuple[MediaReference, ...]]:
    """Lotes de `files` archivos, cada uno con todos sus tramos seguidos."""
    same_file = (tuple(group) for _, group in groupby(references, key=_file_of))
    while chunk := tuple(islice(same_file, files)):
        yield tuple(reference for group in chunk for reference in group)


def _extract_file(
    references: list[MediaReference], config: EngineConfig
) -> Iterator[ExtractionOutcome]:
    """Los tramos de un mismo archivo: de una sola decodificación si son varios de un audio;
    si no, cada uno por su cuenta, que lee solo su tramo."""
    if not _shares_one_decode(references):
        yield from (_extracted(reference, config) for reference in references)
        return
    try:
        audio = decode_audio(references[0].path)
    except MediaError as error:
        yield from (FailedExtraction(reference, error) for reference in references)
        return
    for reference in references:
        try:
            yield ExtractedFeatures(reference, describe_fragment(audio, reference.fragment, config))
        except MediaError as error:
            yield FailedExtraction(reference, error)


def _extracted(reference: MediaReference, config: EngineConfig) -> ExtractionOutcome:
    try:
        return ExtractedFeatures(
            reference, extract_features(reference.path, config, reference.fragment)
        )
    except MediaError as error:
        return FailedExtraction(reference, error)


def _shares_one_decode(references: list[MediaReference]) -> bool:
    """Varios tramos de un audio. Un archivo entero repetido se sigue leyendo cada vez: es
    raro, y así su camino es el mismo de siempre."""
    if len(references) < 2 or all(reference.fragment is None for reference in references):
        return False
    try:
        return media_kind_of(references[0].path) is MediaKind.AUDIO
    except MediaError:
        return False


def _file_of(reference: MediaReference) -> Path:
    return reference.path
