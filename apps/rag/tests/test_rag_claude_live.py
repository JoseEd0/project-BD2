"""Una prueba contra la API real de Anthropic, aparte del resto: cuesta unos centavos y se
salta sin `RAG_ANTHROPIC_API_KEY`. Con la clave:

    RAG_ANTHROPIC_API_KEY=… uv run pytest -m anthropic apps/rag -q

Comprueba lo que el modelo simulado no puede: que Claude Sonnet 5.5 acepta la petición tal
como la arma el servicio, que cita con los números enviados, que dice que no sabe cuando los
documentos no responden, y que la segunda pregunta lee las instrucciones desde la caché.
"""

from __future__ import annotations

import os

import anthropic
import anyio
import pytest
from rag_llm_fakes import sources

from rag.citations import check_citations
from rag.claude import ClaudeModel, ClaudeOptions
from rag.config import DEFAULT_ANSWER_EFFORT, DEFAULT_ANSWER_MAX_TOKENS
from rag.llm import Completion, StopReason, TextDelta
from rag.pricing import SONNET_5_5, total_cost, total_usage
from rag.prompt import Prompt, build_prompt

KEY_VARIABLE = "RAG_ANTHROPIC_API_KEY"
DOCUMENTS = sources(
    "A B+ tree keeps every row in its leaves and links the leaves left to right, so a range "
    "query descends once to the first key and then scans the linked leaves.",
    "BM25 ranks documents by term frequency saturated with the parameter k1.",
)

pytestmark = [
    pytest.mark.anthropic,
    pytest.mark.skipif(not os.environ.get(KEY_VARIABLE), reason=f"sin {KEY_VARIABLE}"),
]


def ask(prompt: Prompt) -> tuple[str, Completion]:
    async def run() -> tuple[str, Completion]:
        async with anthropic.AsyncAnthropic(api_key=os.environ[KEY_VARIABLE]) as client:
            model = ClaudeModel(
                client,
                ClaudeOptions(model=SONNET_5_5, effort=DEFAULT_ANSWER_EFFORT, server_fallback=True),
            )
            events = [event async for event in model.stream(prompt, DEFAULT_ANSWER_MAX_TOKENS)]
        *deltas, end = events
        assert isinstance(end, Completion)
        return "".join(delta.text for delta in deltas if isinstance(delta, TextDelta)), end

    return anyio.run(run)


def test_claude_answers_with_valid_citations_and_reads_the_cached_instructions():
    prompt = build_prompt("How does a B+ tree answer a range query?", DOCUMENTS)
    answer, first = ask(prompt)
    report = check_citations(answer, DOCUMENTS)
    assert first.stop_reason is StopReason.END_TURN
    assert 1 in [citation.n for citation in report.citations]
    assert report.invalid == []
    assert total_cost(first.attempts) is not None
    _, second = ask(prompt)
    assert total_usage(second.attempts).cache_read_tokens > 0


def test_claude_says_when_the_documents_do_not_answer():
    answer, end = ask(build_prompt("Who won the 1998 football world cup?", DOCUMENTS))
    assert end.stop_reason is StopReason.END_TURN
    assert check_citations(answer, DOCUMENTS).citations == []
    assert "France" not in answer
