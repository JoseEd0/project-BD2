"""`POST /search` y `POST /answers` con filtros por año y categoría, el SQL que devuelve cada
búsqueda y `GET /facets`, contra el gestor real sembrado con el corpus diminuto.

En el corpus diminuto todos los papers son de 2025 y de cs.DB salvo el material del curso;
cada test mueve uno a 2026 y cs.IR para que el filtro tenga algo que separar."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from apps.rag.tests.rag_sse import data_of, parse_sse
from fastapi.testclient import TestClient
from tests.rag.rag_corpus import COURSE_PAPER, HashEmbedder, chunk_count

from rag.api import create_app
from rag.config import RagSettings
from rag.pipeline import CorpusPaths
from rag.schema import PAPERS_TABLE
from rag.search import (
    BM25_STAGE,
    FILTER_STAGE,
    MAX_FILTER_CATEGORIES,
    PAPERS_STAGE,
    VECTOR_STAGE,
)
from rag.seed import seed
from rag.simulated import SimulatedModel

MOVED_PAPER = "2501.00003"
MOVED_YEAR = 2026
MOVED_CATEGORY = "cs.IR"
ALL_CHUNKS = chunk_count()


@pytest.fixture
def service(
    settings: RagSettings, corpus: CorpusPaths, tmp_path: Path, hash_embedder: HashEmbedder
) -> Iterator[TestClient]:
    asyncio.run(seed(settings, corpus, tmp_path / "cache", lambda: hash_embedder))
    app = create_app(
        settings,
        embedder_factory=lambda _: hash_embedder,
        answer_model_factory=lambda _: nullcontext(SimulatedModel()),
    )
    with TestClient(app) as client:
        engine = client.app.state.engine  # type: ignore[attr-defined]
        client.portal.call(  # type: ignore[union-attr]
            engine.execute,
            f"UPDATE {PAPERS_TABLE} SET year = $1, primary_category = $2 WHERE paper_id = $3",
            [MOVED_YEAR, MOVED_CATEGORY, MOVED_PAPER],
        )
        yield client


def search(service: TestClient, mode: str, filters: dict[str, Any] | None) -> dict[str, Any]:
    body: dict[str, Any] = {"query": "index tree search query", "mode": mode, "k": ALL_CHUNKS}
    if filters is not None:
        body["filters"] = filters
    response = service.post("/search", json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def papers_of(result: dict[str, Any]) -> set[str]:
    return {hit["paper"]["paper_id"] for hit in result["results"]}


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid"])
def test_a_category_filter_keeps_only_that_category(service: TestClient, mode: str):
    result = search(service, mode, {"categories": [MOVED_CATEGORY]})
    assert papers_of(result) == {MOVED_PAPER}


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid"])
def test_a_year_range_keeps_its_years_and_drops_documents_without_one(
    service: TestClient, mode: str
):
    result = search(service, mode, {"year_from": 2020, "year_to": 2025})
    assert result["results"]
    assert MOVED_PAPER not in papers_of(result)
    assert COURSE_PAPER not in papers_of(result)
    assert {hit["paper"]["year"] for hit in result["results"]} == {2025}


def test_both_filters_combine_with_and(service: TestClient):
    within = search(service, "hybrid", {"year_from": 2026, "categories": ["cs.IR", "cs.DB"]})
    assert papers_of(within) == {MOVED_PAPER}
    assert search(service, "hybrid", {"year_to": 2025, "categories": ["cs.IR"]})["results"] == []


def test_a_filter_no_paper_meets_finds_nothing(service: TestClient):
    for mode in ("bm25", "vector", "hybrid"):
        assert search(service, mode, {"categories": ["math.CO"]})["results"] == []


def test_empty_filters_search_the_whole_corpus(service: TestClient):
    unfiltered = search(service, "vector", None)
    assert len(unfiltered["results"]) == ALL_CHUNKS
    assert search(service, "vector", {"categories": []})["results"] == unfiltered["results"]
    assert FILTER_STAGE not in {query["stage"] for query in unfiltered["queries"]}


def test_each_search_returns_the_sql_it_ran_with_its_parameters(service: TestClient):
    queries = search(service, "hybrid", None)["queries"]
    by_stage = {query["stage"]: query for query in queries}
    assert set(by_stage) == {BM25_STAGE, VECTOR_STAGE, PAPERS_STAGE}
    assert "MATCH(content, $1)" in by_stage[BM25_STAGE]["sql"]
    assert by_stage[BM25_STAGE]["parameters"] == ['"index tree search query"', "50"]
    assert "SIMILAR_TO(embedding, $1" in by_stage[VECTOR_STAGE]["sql"]
    assert by_stage[VECTOR_STAGE]["parameters"][0] == "VECTOR(384)"


def test_filter_values_travel_as_parameters_never_inside_the_sql(service: TestClient):
    hostile = "cs.IR') OR 1=1 --"
    result = search(service, "hybrid", {"categories": [hostile], "year_from": 2025})
    assert result["results"] == []
    by_stage = {query["stage"]: query for query in result["queries"]}
    for query in by_stage.values():
        assert hostile not in query["sql"]
    assert by_stage[FILTER_STAGE]["sql"] == (
        f"SELECT paper_id FROM {PAPERS_TABLE} WHERE year >= $1 AND primary_category IN ($2)"
    )
    assert by_stage[FILTER_STAGE]["parameters"] == ["2025", '"cs.IR\') OR 1=1 --"']
    assert "p.year >= $4 AND p.primary_category IN ($5)" in by_stage[VECTOR_STAGE]["sql"]


def test_the_vector_side_of_a_filter_joins_the_papers(service: TestClient):
    result = search(service, "vector", {"categories": [MOVED_CATEGORY]})
    (vector,) = [query for query in result["queries"] if query["stage"] == VECTOR_STAGE]
    assert "JOIN rag_papers p ON c.paper_id = p.paper_id" in vector["sql"]
    assert FILTER_STAGE not in {query["stage"] for query in result["queries"]}


@pytest.mark.parametrize(
    "filters",
    [
        {"year_from": 2026, "year_to": 2025},
        {"year_from": 1500},
        {"year_to": 3000},
        {"categories": [""]},
        {"categories": ["x" * 33]},
        {"categories": ["cs.DB"] * (MAX_FILTER_CATEGORIES + 1)},
        {"authors": ["Codd"]},
    ],
)
def test_an_invalid_filter_is_refused(service: TestClient, filters: dict[str, object]):
    response = service.post("/search", json={"query": "b+ tree", "filters": filters})
    assert (response.status_code, response.json()["kind"]) == (422, "RequestValidationError")


def test_an_answer_only_cites_the_filtered_papers(service: TestClient):
    response = service.post(
        "/answers",
        json={"question": "How does a graph index search?", "filters": {"categories": ["cs.IR"]}},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    sources = data_of(events, "sources")["sources"]
    assert sources
    assert {source["paper"]["paper_id"] for source in sources} == {MOVED_PAPER}
    assert "filter" in response.headers["server-timing"]


def test_facets_count_papers_per_year_and_category(service: TestClient):
    response = service.get("/facets")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "years": [{"year": 2025, "papers": 2}, {"year": MOVED_YEAR, "papers": 1}],
        "categories": [
            {"category": "cs.DB", "papers": 2},
            {"category": MOVED_CATEGORY, "papers": 1},
            {"category": "curso", "papers": 1},
        ],
    }
