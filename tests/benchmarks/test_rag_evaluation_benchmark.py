"""Lo que el experimento de la evaluación del RAG calcula sin el gestor ni la API: la
estimación del costo y las preguntas aprobadas del lote que entran al conjunto."""

from __future__ import annotations

from pathlib import Path

import pytest
from apps.rag.tests.rag_llm_fakes import sources
from benchmarks.rag_evaluation_benchmark import estimate_cost
from benchmarks.rag_questions import batch_questions

from rag.answering import PreparedAnswer
from rag.config import RagSettings
from rag.evaluation.dataset import EvalQuestion, Origin
from rag.evaluation.generation import Candidate, Review
from rag.pricing import worst_case_cost
from rag.prompt import build_prompt

PAPERS = [{"paper_id": "2601.05347", "primary_category": "cs.DB", "year": 2026}]
SETTINGS = RagSettings.model_validate(
    {"engine_url": "http://gestor", "engine_key": "k", "models_dir": Path("modelos")}
)


def prepared(question: str, *texts: str) -> PreparedAnswer:
    found = tuple(sources(*texts))
    return PreparedAnswer(question, found, build_prompt(question, found), None)


def test_the_estimate_skips_questions_without_sources_and_bounds_the_expected_cost():
    asked = prepared("¿Qué es un B+?", "A B+ tree links its leaves.")
    estimate = estimate_cost(SETTINGS, [asked, prepared("¿Nada?")], judge=True)
    assert estimate["preguntas_con_fragmentos"] == 1
    assert estimate["tokens_de_prompt_estimados"] == asked.prompt.estimated_tokens
    assert estimate["respuestas_cota"] == pytest.approx(
        worst_case_cost(SETTINGS.answer_model, asked.prompt.estimated_tokens, 2048), abs=1e-4
    )
    assert 0 < estimate["respuestas_esperado"] <= estimate["respuestas_cota"]
    assert 0 < estimate["juez_esperado"] <= estimate["juez_cota"]


def test_without_the_judge_its_cost_is_zero_but_still_reported():
    estimate = estimate_cost(SETTINGS, [prepared("¿Qué?", "texto")], judge=False)
    assert estimate["juez_cota"] == 0.0
    assert estimate["juez_esperado_si_se_pide"] > 0


def known(question_id: str, chunk_id: str) -> EvalQuestion:
    return EvalQuestion(
        question_id=question_id, origin=Origin.BATCH, question=f"¿{chunk_id}?",
        answerable=True, chunk_id=chunk_id, paper_id=chunk_id.split("#")[0],
        category="cs.DB", year=2026,
    )  # fmt: skip


def test_approved_candidates_are_numbered_after_the_batch_questions_already_there():
    approved = [
        Candidate(chunk_id="2601.05347#3", question="¿Una?", evidence="x", review=Review.OK),
        Candidate(chunk_id="2601.05347#1", question="¿Repetida?", evidence="x", review=Review.OK),
    ]
    added = batch_questions(approved, PAPERS, [known("b001", "2601.05347#1")])
    assert [(q.question_id, q.chunk_id, q.origin) for q in added] == [
        ("b002", "2601.05347#3", Origin.BATCH)
    ]
    assert added[0].category == "cs.DB" and added[0].year == 2026
