"""El cuerpo SSE de una respuesta con un modelo de guion: orden y forma de los eventos, citas
válidas e inválidas, motivos de parada, errores del modelo, costo y tope de gasto, y que
cerrar el stream (el cliente se fue) cierre también la petición al modelo."""

from __future__ import annotations

from collections.abc import Sequence

import anyio
import pytest
from rag_llm_fakes import ScriptedModel, answering_script, completion, sources
from rag_sse import data_of, names, parse_sse

from rag.answering import (
    INCOMPLETE_STREAM_MESSAGE,
    NO_SOURCES_ANSWER,
    Answerer,
    AnswerSettings,
    PreparedAnswer,
)
from rag.budget import DailyBudget
from rag.llm import (
    Completion,
    LlmRateLimitedError,
    ModelEvent,
    ModelSwitch,
    StopReason,
    TextDelta,
)
from rag.pricing import SONNET_5, SONNET_5_5, Attempt, TokenUsage
from rag.prompt import Source, build_prompt

SETTINGS = AnswerSettings(chunks=8, context_tokens=6000, max_tokens=512, rrf_k=60)
QUESTION = "How does a B+ tree answer a range query?"
SOURCES = sources("A B+ tree links its leaves.", "Range queries scan the leaves.")
RESERVED = 0.05


def answer(
    model: ScriptedModel, sent: Sequence[Source] = SOURCES, budget: DailyBudget | None = None
) -> tuple[list[tuple[str, dict]], DailyBudget]:
    budget = budget or DailyBudget(1.0)
    prepared = PreparedAnswer(
        QUESTION,
        tuple(sent),
        build_prompt(QUESTION, sent),
        budget.reserve(RESERVED) if sent else None,
    )

    async def body() -> str:
        frames = Answerer(model, budget, SETTINGS).events(prepared)
        return "".join([frame.decode() async for frame in frames])

    return parse_sse(anyio.run(body)), budget


def test_sources_then_deltas_then_done():
    model = ScriptedModel(answering_script("Leaves are linked [1]", " and scanned [2]."))
    events, _ = answer(model)
    assert names(events) == ["sources", "delta", "delta", "done"]
    sent = data_of(events, "sources")
    assert sent["question"] == QUESTION
    assert sent["model"] == SONNET_5_5
    assert [(source["n"], source["chunk_id"]) for source in sent["sources"]] == [
        (1, "2501.00001#0"),
        (2, "2501.00002#0"),
    ]
    assert sent["sources"][0]["paper"]["title"] == "B+ Trees"
    assert (sent["sources"][0]["page_start"], sent["sources"][0]["page_end"]) == (3, 4)
    assert [data["text"] for name, data in events if name == "delta"] == [
        "Leaves are linked [1]",
        " and scanned [2].",
    ]
    assert model.max_tokens == [SETTINGS.max_tokens]


def test_done_reports_the_answer_citations_usage_and_cost():
    usage = TokenUsage(input_tokens=3000, output_tokens=400, cache_read_tokens=650)
    model = ScriptedModel(answering_script("Linked [1][2].", end=completion(usage=usage)))
    events, budget = answer(model)
    done = data_of(events, "done")
    assert done["answer"] == "Linked [1][2]."
    assert (done["stop_reason"], done["complete"], done["refusal_category"]) == (
        "end_turn",
        True,
        None,
    )
    assert [citation["n"] for citation in done["citations"]] == [1, 2]
    assert done["invalid_citations"] == []
    assert done["answered_from_documents"] is True
    assert done["usage"] == {
        "input_tokens": 3000,
        "output_tokens": 400,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 650,
    }
    assert done["cost_usd"] == pytest.approx(0.010065)
    assert done["attempts"] == [
        {"model": SONNET_5_5, "usage": done["usage"], "cost_usd": done["cost_usd"]}
    ]
    assert budget.spent == pytest.approx(0.010065)


def test_citations_that_were_not_sent_are_reported_as_invalid():
    model = ScriptedModel(answering_script("Linked [1]. Also [7] and [12]."))
    done = data_of(answer(model)[0], "done")
    assert [citation["n"] for citation in done["citations"]] == [1]
    assert done["invalid_citations"] == [7, 12]


def test_an_answer_without_citations_is_not_from_the_documents():
    model = ScriptedModel(answering_script("The documents do not answer this question."))
    done = data_of(answer(model)[0], "done")
    assert done["answered_from_documents"] is False
    assert done["complete"] is True


@pytest.mark.parametrize(
    ("end", "category"),
    [
        (completion(StopReason.REFUSAL, category="bio"), "bio"),
        (completion(StopReason.MAX_TOKENS), None),
    ],
)
def test_a_refusal_or_a_cut_answer_is_not_complete(end: Completion, category: str | None):
    model = ScriptedModel(answering_script("Partial", end=end))
    done = data_of(answer(model)[0], "done")
    assert done["stop_reason"] == end.stop_reason.value
    assert done["complete"] is False
    assert done["refusal_category"] == category


def test_a_server_fallback_is_announced_and_both_attempts_are_billed():
    attempts = (
        Attempt(SONNET_5_5, TokenUsage(input_tokens=1000, output_tokens=10)),
        Attempt(SONNET_5, TokenUsage(input_tokens=1000, output_tokens=100)),
    )
    script: list[ModelEvent] = [
        TextDelta("Part "),
        ModelSwitch(SONNET_5_5, SONNET_5),
        TextDelta("two [1]."),
        Completion(SONNET_5, StopReason.END_TURN, None, attempts),
    ]
    events, _ = answer(ScriptedModel(script))
    assert names(events) == ["sources", "delta", "fallback", "delta", "done"]
    assert data_of(events, "fallback") == {"from_model": SONNET_5_5, "to_model": SONNET_5}
    done = data_of(events, "done")
    assert done["model"] == SONNET_5
    assert [attempt["model"] for attempt in done["attempts"]] == [SONNET_5_5, SONNET_5]
    assert done["usage"]["output_tokens"] == 110
    assert done["cost_usd"] == pytest.approx((2000 * 2 + 110 * 10) / 1_000_000)


def test_a_model_error_ends_the_stream_with_an_error_event_and_keeps_the_reservation():
    model = ScriptedModel([TextDelta("Half"), LlmRateLimitedError("limitado")])
    events, budget = answer(model)
    assert names(events) == ["sources", "delta", "error"]
    error = data_of(events, "error")
    assert (error["error"], error["kind"]) == ("limitado", "LlmRateLimitedError")
    assert "request_id" in error
    assert budget.spent == pytest.approx(RESERVED)


def test_an_unexpected_failure_is_an_error_event_without_details():
    events, _ = answer(ScriptedModel([RuntimeError("detalle interno")]))
    assert names(events) == ["sources", "error"]
    assert data_of(events, "error")["kind"] == "ServiceError"
    assert "detalle" not in data_of(events, "error")["error"]


def test_a_stream_that_ends_without_a_completion_is_an_error():
    events, _ = answer(ScriptedModel([TextDelta("Half")]))
    assert data_of(events, "error")["error"] == INCOMPLETE_STREAM_MESSAGE


def test_without_sources_the_model_is_not_called():
    model = ScriptedModel(answering_script("nunca"))
    events, budget = answer(model, sent=[])
    assert names(events) == ["sources", "done"]
    done = data_of(events, "done")
    assert (done["answer"], done["cost_usd"], done["answered_from_documents"]) == (
        NO_SOURCES_ANSWER,
        0.0,
        False,
    )
    assert model.prompts == []
    assert budget.spent == 0


def test_the_prompt_sent_is_the_prepared_one():
    model = ScriptedModel(answering_script("ok [1]"))
    answer(model)
    (prompt,) = model.prompts
    assert prompt == build_prompt(QUESTION, SOURCES)


def test_closing_the_stream_closes_the_model_and_keeps_the_reservation():
    model = ScriptedModel(answering_script("First", "never"), hang_after=1)
    budget = DailyBudget(1.0)
    prepared = PreparedAnswer(
        QUESTION, tuple(SOURCES), build_prompt(QUESTION, SOURCES), budget.reserve(RESERVED)
    )

    async def read_two_then_leave() -> list[bytes]:
        frames = Answerer(model, budget, SETTINGS).events(prepared)
        read = [await anext(frames), await anext(frames)]
        await frames.aclose()
        return read

    sources_frame, delta_frame = anyio.run(read_two_then_leave)
    assert sources_frame.startswith(b"event: sources\n")
    assert delta_frame == b'event: delta\ndata: {"text":"First"}\n\n'
    assert model.closed
    assert budget.spent == pytest.approx(RESERVED)
