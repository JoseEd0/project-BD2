"""El API del servicio contra el gestor real, sembrado con el corpus diminuto: salud,
búsqueda en los tres modos, validación y `Server-Timing`."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.rag.rag_corpus import (
    BM25_CHUNK,
    BTREE_CHUNK,
    COURSE_PAPER,
    HNSW_CHUNK,
    RTREE_CHUNK,
    HashEmbedder,
    chunk_count,
)

from rag.api import MAX_QUERY_CHARACTERS, create_app
from rag.config import RagSettings
from rag.embeddings import E5Embedder, Embedder
from rag.pipeline import CorpusPaths
from rag.search import BM25_STAGE, EMBEDDING_STAGE, MAX_RESULTS, VECTOR_STAGE
from rag.seed import seed


def started(settings: RagSettings, embedder: Embedder) -> TestClient:
    return TestClient(create_app(settings, embedder_factory=lambda _: embedder))


@pytest.fixture
def seeded_settings(
    settings: RagSettings, corpus: CorpusPaths, tmp_path: Path, hash_embedder: HashEmbedder
) -> RagSettings:
    asyncio.run(seed(settings, corpus, tmp_path / "cache", lambda: hash_embedder))
    return settings


@pytest.fixture
def service(seeded_settings: RagSettings, hash_embedder: HashEmbedder) -> Iterator[TestClient]:
    with started(seeded_settings, hash_embedder) as client:
        yield client


def search(service: TestClient, query: str, mode: str, k: int = 3) -> list[dict[str, object]]:
    response = service.post("/search", json={"query": query, "mode": mode, "k": k})
    assert response.status_code == 200, response.text
    results: list[dict[str, object]] = response.json()["results"]
    return results


def test_health_and_ready(service: TestClient):
    assert service.get("/health").json()["status"] == "ok"
    assert service.get("/ready").json() == {"status": "ready"}


def test_bm25_finds_the_chunk_with_the_words_and_cites_its_paper(service: TestClient):
    (best, *_) = search(service, "BM25 term frequency saturation k1", "bm25")
    assert best["chunk_id"] == BM25_CHUNK
    assert best["paper"] == {
        "paper_id": "2501.00002",
        "title": "Ranking Documents with BM25",
        "authors": ["Ada Lovelace", "Edgar F. Codd"],
        "year": 2025,
        "url": "https://arxiv.org/abs/2501.00002",
        "file_path": "pdf/2501.00002.pdf",
        "media_type": "application/pdf",
        "license": "http://creativecommons.org/licenses/by/4.0/",
    }
    assert (best["page_start"], best["page_end"], best["rank"]) == (1, 2, 1)
    assert "BM25 ranks documents" in str(best["text"])
    assert best["vector"] is None
    assert best["bm25"] == {"rank": 1, "score": best["score"]}


def test_bm25_reads_operators_and_parentheses_as_words(service: TestClient):
    results = search(service, "NOT (posting list", "bm25")
    assert results[0]["chunk_id"] == "2501.00002#1"


def test_a_question_without_known_words_finds_nothing_in_bm25(service: TestClient):
    assert search(service, "zzzz qqqq", "bm25") == []
    assert search(service, "((", "bm25") == []


def test_vector_mode_returns_k_chunks_by_cosine(service: TestClient):
    results = search(service, "anything", "vector", k=4)
    assert len(results) == 4
    scores = [float(str(hit["score"])) for hit in results]
    assert scores == sorted(scores, reverse=True)
    assert all(hit["bm25"] is None and hit["vector"] for hit in results)


def test_hybrid_fuses_both_lists_with_rrf(service: TestClient):
    results = search(service, "BM25 term frequency", "hybrid", k=chunk_count())
    assert len(results) == chunk_count()
    by_id = {hit["chunk_id"]: hit for hit in results}
    assert by_id[BM25_CHUNK]["bm25"] is not None
    assert all(hit["vector"] is not None for hit in results)
    scores = [float(str(hit["score"])) for hit in results]
    assert scores == sorted(scores, reverse=True)
    assert [hit["rank"] for hit in results] == list(range(1, chunk_count() + 1))


def test_a_course_document_has_no_year_nor_license(service: TestClient):
    results = search(service, "archivo secuencial heap", "bm25", k=MAX_RESULTS)
    course = [hit for hit in results if hit["paper"]["paper_id"] == COURSE_PAPER]  # type: ignore[index]
    assert course
    assert course[0]["paper"]["year"] is None  # type: ignore[index]


def test_server_timing_has_each_stage(service: TestClient):
    response = service.post("/search", json={"query": "B+ tree", "mode": "hybrid"})
    timing = response.headers["Server-Timing"]
    assert all(stage in timing for stage in (BM25_STAGE, VECTOR_STAGE, EMBEDDING_STAGE))


@pytest.mark.parametrize(
    "body",
    [
        {"query": "   "},
        {"query": "x" * (MAX_QUERY_CHARACTERS + 1)},
        {"query": "b+ tree", "k": 0},
        {"query": "b+ tree", "k": MAX_RESULTS + 1},
        {"query": "b+ tree", "mode": "fuzzy"},
    ],
)
def test_an_invalid_request_is_refused(service: TestClient, body: dict[str, object]):
    response = service.post("/search", json=body)
    assert (response.status_code, response.json()["kind"]) == (422, "RequestValidationError")


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿Qué estructura agrupa puntos cercanos en rectángulos?", RTREE_CHUNK),
        ("búsqueda de vecinos más cercanos con grafos jerárquicos", HNSW_CHUNK),
        ("¿Cómo responde un árbol B+ una consulta por rango?", BTREE_CHUNK),
    ],
)
def test_a_spanish_question_finds_the_english_chunk_by_vector(
    settings: RagSettings,
    corpus: CorpusPaths,
    tmp_path: Path,
    e5: E5Embedder,
    question: str,
    expected: str,
):
    asyncio.run(seed(settings, corpus, tmp_path / "cache", lambda: e5))
    with started(settings, e5) as service:
        assert search(service, question, "vector")[0]["chunk_id"] == expected
