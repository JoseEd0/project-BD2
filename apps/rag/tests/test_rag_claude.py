"""Claude por el SDK oficial, con la red reemplazada por un transporte de mentira: la forma
de la petición (instrucciones cacheadas primero, documentos y pregunta después, pensamiento
adaptativo, esfuerzo y respaldo del servidor), el texto en streaming, el cambio de modelo, los
tokens de cada intento, los motivos de parada y los errores de la API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import anthropic
import anyio
import httpx2
import pytest
from rag_llm_fakes import (
    fallback_block,
    message_delta,
    message_start,
    message_stop,
    sources,
    sse_events,
    text_block,
    thinking_block,
)

from rag.claude import SERVER_SIDE_FALLBACK_BETA, ClaudeModel, ClaudeOptions
from rag.llm import (
    Completion,
    LlmAccessError,
    LlmError,
    LlmRateLimitedError,
    LlmRequestError,
    LlmUnavailableError,
    ModelEvent,
    ModelSwitch,
    StopReason,
    TextDelta,
)
from rag.pricing import SONNET_5, SONNET_5_5, Attempt, TokenUsage
from rag.prompt import SYSTEM_INSTRUCTIONS, build_prompt

QUESTION = "How does a B+ tree answer a range query?"
PROMPT = build_prompt(QUESTION, sources("A B+ tree links its leaves."))
OPTIONS = ClaudeOptions(model=SONNET_5_5, effort="low", server_fallback=True)
MAX_TOKENS = 2048
Handler = Callable[[httpx2.Request], httpx2.Response]


def model_with(handler: Handler, options: ClaudeOptions = OPTIONS) -> ClaudeModel:
    client = anthropic.AsyncAnthropic(
        api_key="clave-de-prueba",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return ClaudeModel(client, options)


def streaming(body: bytes, requests: list[httpx2.Request] | None = None) -> Handler:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    return handle


def collect(model: ClaudeModel) -> list[ModelEvent]:
    async def run() -> list[ModelEvent]:
        return [event async for event in model.stream(PROMPT, MAX_TOKENS)]

    return anyio.run(run)


def answer_body(*blocks: list[dict[str, Any]], delta: dict[str, Any] | None = None) -> bytes:
    events = [message_start(), *(event for block in blocks for event in block)]
    return sse_events(*events, delta or message_delta(), message_stop())


def test_the_request_puts_the_cached_instructions_first_and_the_question_last():
    requests: list[httpx2.Request] = []
    collect(model_with(streaming(answer_body(text_block(0, "Yes [1].")), requests)))
    (request,) = requests
    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert body["model"] == SONNET_5_5
    assert body["max_tokens"] == MAX_TOKENS
    assert body["stream"] is True
    assert body["system"] == [
        {"type": "text", "text": SYSTEM_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}}
    ]
    (message,) = body["messages"]
    assert message["role"] == "user"
    assert [block["text"] for block in message["content"]] == [PROMPT.documents, PROMPT.question]
    assert all("cache_control" not in block for block in message["content"])
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default"
    assert SERVER_SIDE_FALLBACK_BETA in request.headers["anthropic-beta"]
    assert "temperature" not in body


def test_without_the_server_fallback_the_request_has_no_beta():
    requests: list[httpx2.Request] = []
    options = ClaudeOptions(model=SONNET_5_5, effort="medium", server_fallback=False)
    collect(model_with(streaming(answer_body(text_block(0, "Yes.")), requests), options))
    body = json.loads(requests[0].content)
    assert "fallbacks" not in body
    assert body["output_config"] == {"effort": "medium"}
    assert SERVER_SIDE_FALLBACK_BETA not in requests[0].headers.get("anthropic-beta", "")


def test_text_streams_in_order_and_thinking_blocks_are_skipped():
    events = collect(
        model_with(
            streaming(
                answer_body(thinking_block(0), text_block(1, "B+ trees ", "scan leaves [1]."))
            )
        )
    )
    *deltas, end = events
    assert deltas == [TextDelta("B+ trees "), TextDelta("scan leaves [1].")]
    assert end == Completion(
        model=SONNET_5_5,
        stop_reason=StopReason.END_TURN,
        refusal_category=None,
        attempts=(
            Attempt(
                SONNET_5_5, TokenUsage(input_tokens=900, output_tokens=42, cache_read_tokens=700)
            ),
        ),
    )


def test_a_mid_stream_fallback_is_announced_and_each_attempt_has_its_own_tokens():
    iterations = [
        {
            "type": "message",
            "input_tokens": 900,
            "output_tokens": 12,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 700,
        },
        {
            "type": "fallback_message",
            "model": SONNET_5,
            "input_tokens": 1600,
            "output_tokens": 80,
            "cache_creation_input_tokens": 650,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 650, "ephemeral_1h_input_tokens": 0},
        },
    ]
    body = answer_body(
        text_block(0, "Partial "),
        fallback_block(1, SONNET_5_5, SONNET_5),
        text_block(2, "answer [1]."),
        delta=message_delta(output_tokens=80, iterations=iterations),
    )
    events = collect(model_with(streaming(body)))
    assert events[:3] == [
        TextDelta("Partial "),
        ModelSwitch(SONNET_5_5, SONNET_5),
        TextDelta("answer [1]."),
    ]
    end = events[-1]
    assert isinstance(end, Completion)
    assert end.model == SONNET_5
    assert end.attempts == (
        Attempt(SONNET_5_5, TokenUsage(input_tokens=900, output_tokens=12, cache_read_tokens=700)),
        Attempt(
            SONNET_5, TokenUsage(input_tokens=1600, output_tokens=80, cache_write_5m_tokens=650)
        ),
    )


def test_a_refusal_reports_its_category():
    delta = message_delta(
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "bio", "explanation": None},
    )
    events = collect(model_with(streaming(answer_body(delta=delta))))
    (end,) = events
    assert isinstance(end, Completion)
    assert (end.stop_reason, end.refusal_category) == (StopReason.REFUSAL, "bio")


def test_max_tokens_is_reported_as_the_stop_reason():
    delta = message_delta(stop_reason="max_tokens", output_tokens=MAX_TOKENS)
    events = collect(model_with(streaming(answer_body(text_block(0, "cut"), delta=delta))))
    end = events[-1]
    assert isinstance(end, Completion)
    assert end.stop_reason is StopReason.MAX_TOKENS


def error_response(status: int, error_type: str) -> Handler:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": error_type, "message": "detalle"}}
        )

    return handle


@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [
        (401, "authentication_error", LlmAccessError),
        (403, "permission_error", LlmAccessError),
        (429, "rate_limit_error", LlmRateLimitedError),
        (529, "overloaded_error", LlmUnavailableError),
        (500, "api_error", LlmUnavailableError),
        (400, "invalid_request_error", LlmRequestError),
    ],
)
def test_api_errors_become_llm_errors(status: int, error_type: str, expected: type[LlmError]):
    with pytest.raises(expected) as raised:
        collect(model_with(error_response(status, error_type)))
    assert "detalle" not in raised.value.message


def test_a_connection_failure_is_unavailable():
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("sin red", request=request)

    with pytest.raises(LlmUnavailableError):
        collect(model_with(refuse))


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        ("overloaded_error", LlmUnavailableError),
        ("api_error", LlmUnavailableError),
        ("rate_limit_error", LlmRateLimitedError),
    ],
)
def test_an_error_event_in_the_middle_of_the_stream(error_type: str, expected: type[LlmError]):
    body = sse_events(
        message_start(),
        *text_block(0, "Half"),
        {"type": "error", "error": {"type": error_type, "message": "detalle"}},
    )
    with pytest.raises(expected):
        collect(model_with(streaming(body)))


class HangingStream(httpx2.AsyncByteStream):
    """El cuerpo de una respuesta que manda un trozo y no termina; recuerda si lo cerraron."""

    def __init__(self, first: bytes) -> None:
        self.first = first
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        await anyio.sleep_forever()

    async def aclose(self) -> None:
        self.closed = True


def test_closing_the_events_early_closes_the_http_response():
    body = HangingStream(sse_events(message_start(), *text_block(0, "First")[:2]))

    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    async def first_delta_then_close() -> ModelEvent:
        events = model_with(handle).stream(PROMPT, MAX_TOKENS)
        first = await anext(events)
        await events.aclose()  # type: ignore[attr-defined]
        return first

    assert anyio.run(first_delta_then_close) == TextDelta("First")
    assert body.closed
