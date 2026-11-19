"""Siembra el gestor con el corpus: migraciones, embeddings y carga de las dos tablas.

    RAG_ENGINE_URL=http://localhost:8000 RAG_ENGINE_KEY=... RAG_MIGRATION_KEY=... \\
    RAG_MODELS_DIR=data/models/rag uv run python -m rag.seed --data-dir data/datasets

Lee `<data-dir>/rag_papers/` (lo que deja `python -m rag.corpus build`), guarda los
embeddings en `<data-dir>/rag_papers/embeddings/` (o en `--cache-dir`) y carga
`rag_papers` y `rag_chunks`. Es repetible: una segunda corrida no calcula ni carga nada,
y una cortada sigue donde quedó. `--report` deja los conteos y tiempos en un JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from appkit import (
    MigrationError,
    SettingsError,
    configure_logging,
    engine_client,
    load_settings,
    migrate,
)
from minigestor_client import MinigestorError

from .config import RagSettings, load_embedder
from .embedding_cache import EmbeddingCache
from .embeddings import Embedder
from .errors import IngestError, ModelNotFoundError
from .files import write_json
from .ingest import (
    EmbeddingReport,
    TableLoad,
    chunk_rows,
    count_lines,
    embed_missing,
    load_remaining,
    paper_rows,
    table_rows,
)
from .models import ModelFiles
from .pipeline import CorpusPaths
from .schema import CHUNKS_TABLE, PAPERS_TABLE, SCHEMA
from .settings import CORPUS_DIRECTORY

CACHE_DIRECTORY = "embeddings"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SeedReport:
    """Lo que hizo una siembra, con sus tiempos en segundos."""

    model_id: str
    embedding: EmbeddingReport | None
    papers: TableLoad
    chunks: TableLoad
    seconds: float

    def to_json(self) -> dict[str, object]:
        report = asdict(self)
        if self.embedding is not None:
            report["embedding"]["chunks_per_second"] = self.embedding.chunks_per_second
        return report


async def seed(
    settings: RagSettings,
    corpus: CorpusPaths,
    cache_directory: Path,
    embedder: Callable[[], Embedder],
) -> SeedReport:
    """Deja el gestor con el corpus de `corpus`: esquema, `rag_papers` y `rag_chunks`.

    Raises:
        appkit.MigrationError: si el esquema no se puede aplicar.
        IngestError: si el gestor rechaza filas o una tabla no queda como el corpus.
        minigestor_client.MinigestorError: si el gestor no responde o rechaza una consulta.
    """
    started = time.perf_counter()
    migration_key = settings.migration_key or settings.engine_key
    async with engine_client(settings, migration_key) as migrator:
        await migrate(migrator, SCHEMA)
    model_id = ModelFiles.inside(settings.models_dir, settings.embedding_model).model_id
    async with engine_client(settings, settings.engine_key) as engine:
        papers = await load_remaining(
            engine,
            PAPERS_TABLE,
            paper_rows(corpus.papers_file),
            await table_rows(engine, PAPERS_TABLE),
            count_lines(corpus.papers_file),
        )
        expected_chunks = count_lines(corpus.chunks_file)
        present_chunks = await table_rows(engine, CHUNKS_TABLE)
        embedding = None
        cache = EmbeddingCache(cache_directory, model_id)
        if present_chunks != expected_chunks:
            embedding = embed_missing(corpus.chunks_file, cache, embedder)
        chunks = await load_remaining(
            engine,
            CHUNKS_TABLE,
            chunk_rows(corpus.chunks_file, cache),
            present_chunks,
            expected_chunks,
        )
    return SeedReport(model_id, embedding, papers, chunks, time.perf_counter() - started)


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rag.seed", description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, help="carpeta de datasets (o RAG_DATA_DIR)")
    parser.add_argument("--cache-dir", type=Path, help="carpeta de la caché de embeddings")
    parser.add_argument("--report", type=Path, help="JSON con los conteos y tiempos")
    options = parser.parse_args(arguments)
    try:
        settings = load_settings(RagSettings)
    except SettingsError as error:
        parser.exit(1, f"error: {error}\n")
    configure_logging(settings.log_level)
    data_directory = options.data_dir or settings.data_dir
    if data_directory is None:
        parser.exit(1, "error: falta --data-dir o RAG_DATA_DIR\n")
    corpus = CorpusPaths(data_directory / CORPUS_DIRECTORY)
    cache_directory = options.cache_dir or corpus.root / CACHE_DIRECTORY
    try:
        report = asyncio.run(
            seed(settings, corpus, cache_directory, lambda: load_embedder(settings))
        )
    except (IngestError, MigrationError, ModelNotFoundError, MinigestorError) as error:
        parser.exit(1, f"error: {error}\n")
    logger.info("siembra terminada en %.1f s", report.seconds)
    if options.report is not None:
        write_json(options.report, report.to_json())
        logger.info("reporte en %s", options.report)
    else:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
