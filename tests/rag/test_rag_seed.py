"""La siembra contra el gestor real: carga todo, una segunda corrida no hace nada, una
cortada sigue donde quedó y una tabla con más filas que el corpus detiene el trabajo."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest
from tests.rag.conftest import ADMIN_KEY
from tests.rag.rag_corpus import DOCUMENTS, HashEmbedder, chunk_count

from minigestor_client import Client
from rag.config import RagSettings
from rag.embedding_cache import EmbeddingCache
from rag.errors import IngestError
from rag.ingest import chunk_rows, embed_missing
from rag.models import ModelFiles
from rag.pipeline import CorpusPaths
from rag.schema import CHUNKS_TABLE, CONTENT_INDEX, EMBEDDING_INDEX, PAPERS_TABLE, SCHEMA
from rag.seed import SeedReport, seed

CUT_AFTER_ROWS = 3


def seeded(
    settings: RagSettings, corpus: CorpusPaths, cache: Path, embedder: HashEmbedder
) -> SeedReport:
    return asyncio.run(seed(settings, corpus, cache, lambda: embedder))


def model_id_of(settings: RagSettings) -> str:
    return ModelFiles.inside(settings.models_dir, settings.embedding_model).model_id


def admin(settings: RagSettings) -> Client:
    return Client(settings.engine_base_url, ADMIN_KEY)


def test_the_first_run_loads_every_paper_and_chunk_with_deferred_indexes(
    settings: RagSettings, corpus: CorpusPaths, tmp_path: Path, hash_embedder: HashEmbedder
):
    report = seeded(settings, corpus, tmp_path / "cache", hash_embedder)
    assert report.embedding is not None
    assert (report.embedding.computed, report.embedding.cached) == (chunk_count(), 0)
    assert (report.papers.rows_loaded, report.chunks.rows_loaded) == (len(DOCUMENTS), chunk_count())
    assert set(report.chunks.deferred_indexes) == {CONTENT_INDEX, EMBEDDING_INDEX}
    with admin(settings) as engine:
        assert engine.scalar(f"SELECT COUNT(*) FROM {CHUNKS_TABLE}") == chunk_count()
        paper = engine.fetch_one(
            f"SELECT title, authors, year FROM {PAPERS_TABLE} WHERE paper_id = $1", ["2501.00002"]
        )
        assert paper == {
            "title": "Ranking Documents with BM25",
            "authors": "Ada Lovelace; Edgar F. Codd",
            "year": 2025,
        }
        course = engine.fetch_one(
            f"SELECT year, license FROM {PAPERS_TABLE} WHERE paper_id = $1", ["curso:informe"]
        )
        assert course == {"year": None, "license": None}


def test_a_second_run_computes_and_loads_nothing(
    settings: RagSettings, corpus: CorpusPaths, tmp_path: Path, hash_embedder: HashEmbedder
):
    seeded(settings, corpus, tmp_path / "cache", hash_embedder)
    again = seeded(settings, corpus, tmp_path / "cache", hash_embedder)
    assert again.embedding is None
    assert (again.papers.rows_loaded, again.chunks.rows_loaded) == (0, 0)
    assert (again.papers.rows_before, again.chunks.rows_before) == (len(DOCUMENTS), chunk_count())
    assert hash_embedder.passages_embedded == chunk_count()


def test_a_new_engine_is_seeded_from_the_cache_without_the_model(
    settings: RagSettings, corpus: CorpusPaths, tmp_path: Path, hash_embedder: HashEmbedder
):
    cache = tmp_path / "cache"
    embed_missing(
        corpus.chunks_file, EmbeddingCache(cache, model_id_of(settings)), lambda: hash_embedder
    )

    def no_model() -> HashEmbedder:
        raise AssertionError("con la caché completa no se carga el modelo")

    report = asyncio.run(seed(settings, corpus, cache, no_model))
    assert report.embedding is not None
    assert (report.embedding.computed, report.embedding.cached) == (0, chunk_count())
    assert report.chunks.rows_loaded == chunk_count()


def test_a_cut_load_continues_after_the_rows_it_kept(
    settings: RagSettings, corpus: CorpusPaths, tmp_path: Path, hash_embedder: HashEmbedder
):
    cache = EmbeddingCache(tmp_path / "cache", model_id_of(settings))
    embed_missing(corpus.chunks_file, cache, lambda: hash_embedder)
    with admin(settings) as engine:
        for statement in SCHEMA:
            engine.execute(statement)
        engine.bulk_insert(
            CHUNKS_TABLE, itertools.islice(chunk_rows(corpus.chunks_file, cache), CUT_AFTER_ROWS)
        )
    report = seeded(settings, corpus, tmp_path / "cache", hash_embedder)
    assert (report.chunks.rows_before, report.chunks.rows_loaded) == (
        CUT_AFTER_ROWS,
        chunk_count() - CUT_AFTER_ROWS,
    )
    assert report.embedding is not None
    assert report.embedding.computed == 0
    with admin(settings) as engine:
        ids = engine.fetch_all(f"SELECT chunk_id FROM {CHUNKS_TABLE}")
    assert len({row["chunk_id"] for row in ids}) == chunk_count()


def test_a_table_with_more_rows_than_the_corpus_stops_the_seed(
    settings: RagSettings, corpus: CorpusPaths, tmp_path: Path, hash_embedder: HashEmbedder
):
    seeded(settings, corpus, tmp_path / "cache", hash_embedder)
    with admin(settings) as engine:
        engine.execute(f"INSERT INTO {PAPERS_TABLE} (paper_id, title) VALUES ($1, $2)", ["x", "y"])
    with pytest.raises(IngestError, match="el corpus cambió"):
        seeded(settings, corpus, tmp_path / "cache", hash_embedder)
