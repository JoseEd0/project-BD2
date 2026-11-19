"""La ingesta del corpus en el gestor: embeddings de los fragmentos y carga de las dos tablas.

Es repetible y se reanuda. Los embeddings ya calculados están en la caché y no se vuelven
a calcular. Una tabla con todas sus filas no se toca; una a medias (una carga cortada)
sigue desde la fila siguiente a las que tiene, porque la carga masiva confirma lotes
enteros y en el orden de los archivos del corpus, que es estable. Una tabla vacía se carga
con sus índices diferidos, que el gestor construye de una vez al final.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from minigestor_client import AsyncClient, LoadReport

from .embedding_cache import EmbeddingCache
from .embeddings import Embedder
from .errors import IngestError
from .files import read_jsonl
from .schema import AUTHOR_SEPARATOR

# Fragmentos que se leen, ordenan por largo y pasan juntos por el modelo. Acota la memoria
# (unos 1 000 textos de 1,5 KB y sus tokens) y deja lotes de largo parecido.
EMBEDDING_WINDOW = 1024
# Filas que se piden juntas a la caché al cargar `rag_chunks`.
LOAD_WINDOW = 1000
PROGRESS_EVERY_ROWS = 10_000
SECONDS_PER_MINUTE = 60
COUNT_SQL = "SELECT COUNT(*) FROM {table}"

logger = logging.getLogger(__name__)

Row = dict[str, object]
Item = TypeVar("Item")


@dataclass(frozen=True, slots=True)
class EmbeddingReport:
    """Los embeddings que faltaban: cuántos se calcularon, cuántos ya estaban en la caché,
    cuántos fragmentos pasaban de 512 tokens y cuánto tardó."""

    model_id: str
    computed: int
    cached: int
    truncated: int
    seconds: float

    @property
    def chunks_per_second(self) -> float:
        return self.computed / self.seconds if self.seconds else 0.0


@dataclass(frozen=True, slots=True)
class TableLoad:
    """La carga de una tabla: las filas que ya tenía, las que entraron, lo que tardó visto
    desde aquí y desde el gestor, y los índices que el gestor difirió."""

    table: str
    rows_before: int
    rows_loaded: int
    seconds: float
    engine_elapsed_ms: float
    deferred_indexes: tuple[str, ...]

    @classmethod
    def skipped(cls, table: str, rows: int) -> TableLoad:
        return cls(table, rows, 0, 0.0, 0.0, ())


def count_lines(path: Path) -> int:
    with path.open("rb") as lines:
        return sum(1 for line in lines if line.strip())


def embed_missing(
    chunks_file: Path, cache: EmbeddingCache, embedder: Callable[[], Embedder]
) -> EmbeddingReport:
    """Calcula y guarda en `cache` los embeddings de los fragmentos que no tiene. El modelo
    (`embedder`) solo se carga si falta alguno."""
    pending = cache.missing(str(chunk["sha256"]) for chunk in read_jsonl(chunks_file))
    cached = count_lines(chunks_file) - len(pending)
    if not pending:
        return EmbeddingReport("", 0, cached, 0, 0.0)
    model = embedder()
    logger.info("embeddings: %d en la caché, faltan %d (%s)", cached, len(pending), model.model_id)
    started = time.perf_counter()
    computed = truncated = 0
    for window in _windows(_pending_chunks(chunks_file, set(pending)), EMBEDDING_WINDOW):
        embedded = model.embed_passages([text for _, text in window])
        cache.add([key for key, _ in window], embedded.vectors)
        computed += len(window)
        truncated += embedded.truncated
        _log_embedding_progress(computed, len(pending), time.perf_counter() - started)
    seconds = time.perf_counter() - started
    return EmbeddingReport(model.model_id, computed, cached, truncated, seconds)


async def table_rows(engine: AsyncClient, table: str) -> int:
    rows = await engine.scalar(COUNT_SQL.format(table=table))
    return int(rows or 0)


async def load_remaining(
    engine: AsyncClient, table: str, rows: Iterable[Row], present: int, expected: int
) -> TableLoad:
    """Carga en `table` las filas de `rows` que siguen a las `present` que ya tiene.

    Raises:
        IngestError: si la tabla tiene más filas que el corpus, si el gestor rechaza algún
            lote o si al terminar no tiene `expected` filas.
    """
    if present == expected:
        logger.info("%s ya tiene sus %d filas", table, expected)
        return TableLoad.skipped(table, present)
    if present > expected:
        raise IngestError(
            f"{table} tiene {present} filas y el corpus {expected}: el corpus cambió; "
            "borra la tabla para volver a cargarla"
        )
    logger.info("%s: cargando %d filas desde la %d", table, expected - present, present + 1)
    started = time.perf_counter()
    remaining = itertools.islice(rows, present, None)
    report = await engine.bulk_insert(table, _logged(remaining, table, present, expected))
    seconds = time.perf_counter() - started
    _check_loaded(report)
    final = await table_rows(engine, table)
    if final != expected:
        raise IngestError(f"{table} quedó con {final} filas y el corpus tiene {expected}")
    logger.info("%s: %d filas en %.1f s", table, report.rows_loaded, seconds)
    return TableLoad(
        table,
        present,
        report.rows_loaded,
        seconds,
        report.elapsed_ms,
        tuple(report.deferred_indexes),
    )


def paper_rows(papers_file: Path) -> Iterator[Row]:
    """Las filas de `rag_papers`, en el orden de `papers.jsonl`."""
    for paper in read_jsonl(papers_file):
        authors = paper["authors"]
        yield {
            "paper_id": paper["paper_id"],
            "source": paper["source"],
            "title": paper["title"],
            "authors": AUTHOR_SEPARATOR.join(authors) if isinstance(authors, list) else "",
            "year": paper["year"],
            "abstract": paper["abstract"],
            "primary_category": paper["primary_category"],
            "license": paper["license"],
            "url": paper["url"],
            "file_path": paper["file_path"],
            "media_type": paper["media_type"],
            "sha256": paper["sha256"],
            "pages": paper["pages"],
        }


def chunk_rows(chunks_file: Path, cache: EmbeddingCache) -> Iterator[Row]:
    """Las filas de `rag_chunks` con su embedding, en el orden de `fragmentos.jsonl`.

    Raises:
        KeyError: si la caché no tiene el embedding de algún fragmento.
    """
    for window in _windows(read_jsonl(chunks_file), LOAD_WINDOW):
        vectors = cache.vectors([str(chunk["sha256"]) for chunk in window])
        for chunk, vector in zip(window, vectors, strict=True):
            yield {
                "chunk_id": chunk["chunk_id"],
                "paper_id": chunk["paper_id"],
                "ordinal": chunk["ordinal"],
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
                "words": chunk["words"],
                "content": chunk["text"],
                "embedding": vector,
            }


def _pending_chunks(chunks_file: Path, pending: set[str]) -> Iterator[tuple[str, str]]:
    for chunk in read_jsonl(chunks_file):
        key = str(chunk["sha256"])
        if key in pending:
            pending.discard(key)
            yield key, str(chunk["text"])


def _windows(items: Iterable[Item], size: int) -> Iterator[list[Item]]:
    iterator = iter(items)
    while window := list(itertools.islice(iterator, size)):
        yield window


def _log_embedding_progress(done: int, total: int, seconds: float) -> None:
    rate = done / seconds if seconds else 0.0
    remaining_minutes = (total - done) / rate / SECONDS_PER_MINUTE if rate else 0.0
    logger.info(
        "embeddings: %d/%d (%.0f %%), %.1f fragmentos/s, faltan ~%.0f min",
        done,
        total,
        100 * done / total,
        rate,
        remaining_minutes,
    )


def _logged(rows: Iterable[Row], table: str, present: int, expected: int) -> Iterator[Row]:
    for sent, row in enumerate(rows, start=present + 1):
        yield row
        if sent % PROGRESS_EVERY_ROWS == 0:
            logger.info("%s: %d/%d filas enviadas", table, sent, expected)
    logger.info("%s: filas enviadas; el gestor termina la carga y sus índices", table)


def _check_loaded(report: LoadReport) -> None:
    if report.interrupted is not None:
        raise IngestError(f"la carga de {report.table} se interrumpió: {report.interrupted}")
    if report.rejected:
        first = report.rejected[0]
        raise IngestError(
            f"el gestor rechazó {report.rows_rejected} filas de {report.table}; la primera, "
            f"en la línea {first.line}: {first.reason}"
        )
