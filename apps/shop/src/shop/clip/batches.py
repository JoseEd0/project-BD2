"""Embeddings de muchas fotos por lotes, con memoria acotada y sin repetir trabajo.

Cada foto se lee y se hashea; si su SHA-256 ya está en la caché no se decodifica. Las que
faltan se preprocesan en hilos (Pillow suelta el GIL al decodificar y al redimensionar) y
entran a la torre de imagen de a `batch_size`: en memoria nunca hay más que un lote de
píxeles, unos 600 KB por foto.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Protocol

import numpy as np

from .cache import EmbeddingCache, image_digest
from .encoder import Embeddings, load_image
from .preprocessing import PixelValues, pixel_values

logger = logging.getLogger(__name__)


class PixelEncoder(Protocol):
    """Lo que necesita `fill_cache` de la torre de imagen (`ClipImageEncoder`)."""

    def embed_pixels(self, batch: PixelValues) -> Embeddings: ...


@dataclass(frozen=True, slots=True)
class BatchSettings:
    """Cómo se reparten las fotos.

    Attributes:
        batch_size: fotos por pasada de la torre de imagen.
        preprocess_workers: hilos que decodifican y preprocesan.
        progress_every: cada cuántas fotos se informa el avance.
    """

    batch_size: int
    preprocess_workers: int
    progress_every: int


@dataclass(slots=True)
class EmbeddingRun:
    """Lo que hizo `fill_cache`: fotos distintas vistas, cuántas se calcularon, cuántas ya
    estaban, los segundos que tardó y el digest de cada foto."""

    images: int = 0
    computed: int = 0
    cached: int = 0
    seconds: float = 0.0
    digests: dict[Path, bytes] = field(default_factory=dict)


def fill_cache(
    images: Iterable[Path],
    encoder: PixelEncoder,
    cache: EmbeddingCache,
    settings: BatchSettings,
    clock: Callable[[], float] = time.perf_counter,
) -> EmbeddingRun:
    """Deja en `cache` el embedding de cada foto de `images`; las repetidas se ven una vez.

    Raises:
        UnreadableImageError: si una foto que falta en la caché no se puede decodificar.
    """
    started = clock()
    run = EmbeddingRun()
    with ThreadPoolExecutor(settings.preprocess_workers) as pool:
        for batch in _batches(_missing(images, cache, run), settings.batch_size):
            pixels = np.stack(list(pool.map(_preprocessed, [path for path, _ in batch])))
            embeddings = encoder.embed_pixels(pixels)
            cache.put_all(zip((digest for _, digest in batch), embeddings, strict=True))
            run.computed += len(batch)
            _report(run, settings.progress_every, len(batch), clock() - started)
    run.seconds = clock() - started
    return run


def _missing(
    images: Iterable[Path], cache: EmbeddingCache, run: EmbeddingRun
) -> Iterator[tuple[Path, bytes]]:
    pending: set[bytes] = set()
    for path in images:
        if path in run.digests:
            continue
        digest = image_digest(path.read_bytes())
        run.digests[path] = digest
        run.images += 1
        if digest in cache or digest in pending:
            run.cached += 1
            continue
        pending.add(digest)
        yield path, digest


def _batches(
    items: Iterator[tuple[Path, bytes]], size: int
) -> Iterator[Sequence[tuple[Path, bytes]]]:
    while batch := list(islice(items, size)):
        yield batch


def _preprocessed(path: Path) -> PixelValues:
    return pixel_values(load_image(path))


def _report(run: EmbeddingRun, every: int, added: int, seconds: float) -> None:
    if run.computed // every == (run.computed - added) // every:
        return
    logger.info(
        "CLIP: %d fotos calculadas (%.1f por segundo), %d ya estaban en la caché",
        run.computed,
        run.computed / seconds if seconds else 0.0,
        run.cached,
    )
