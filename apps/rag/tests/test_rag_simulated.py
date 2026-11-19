"""El modelo simulado: cita los documentos que comparten palabras con la pregunta, dice que no
están en los documentos cuando ninguno las comparte y corta en `max_tokens`."""

from __future__ import annotations

import anyio
from rag_llm_fakes import sources

from rag.citations import check_citations
from rag.llm import Completion, ModelEvent, StopReason, TextDelta
from rag.pricing import SIMULATED_MODEL, TokenUsage
from rag.prompt import Prompt, build_prompt
from rag.simulated import NOT_IN_DOCUMENTS, SimulatedModel

DOCUMENTS = sources(
    "A B+ tree links its leaves so a range query scans them.",
    "BM25 saturates term frequency with k1.",
    "Leaves & <tags> are escaped in the prompt.",
)


def run(prompt: Prompt, max_tokens: int = 1000) -> tuple[str, Completion]:
    async def collect() -> list[ModelEvent]:
        return [event async for event in SimulatedModel().stream(prompt, max_tokens)]

    *deltas, end = anyio.run(collect)
    assert all(isinstance(delta, TextDelta) for delta in deltas)
    assert isinstance(end, Completion)
    return "".join(delta.text for delta in deltas if isinstance(delta, TextDelta)), end


def test_it_cites_the_documents_that_share_words_with_the_question():
    answer, end = run(build_prompt("How do leaves answer a range query?", DOCUMENTS))
    report = check_citations(answer, DOCUMENTS)
    assert [citation.n for citation in report.citations] == [1, 3]
    assert report.invalid == []
    assert end.stop_reason is StopReason.END_TURN
    assert end.model == SIMULATED_MODEL
    assert end.attempts[0].usage == TokenUsage()


def test_an_irrelevant_context_is_not_in_the_documents():
    answer, end = run(build_prompt("¿Quién ganó el mundial de fútbol?", DOCUMENTS))
    assert answer == NOT_IN_DOCUMENTS
    assert check_citations(answer, DOCUMENTS).citations == []
    assert end.stop_reason is StopReason.END_TURN


def test_it_stops_at_max_tokens_like_the_api():
    answer, end = run(build_prompt("range query leaves", DOCUMENTS), max_tokens=3)
    assert len(answer.split(" ")) == 3
    assert end.stop_reason is StopReason.MAX_TOKENS
