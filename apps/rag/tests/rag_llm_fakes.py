"""Lo que necesitan los tests de las respuestas sin red: fragmentos hechos a mano, un modelo
que repite un guion y el stream SSE de la API de mensajes de Anthropic escrito a mano."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio

from rag.llm import Completion, LlmError, ModelEvent, StopReason, TextDelta
from rag.pricing import SONNET_5_5, Attempt, TokenUsage
from rag.prompt import Prompt, Source
from rag.search import Paper, SearchHit


def paper(paper_id: str = "2501.00001", title: str = "B+ Trees for Range Queries") -> Paper:
    return Paper(
        paper_id=paper_id,
        title=title,
        authors=["Ada Lovelace"],
        year=2025,
        url=f"https://arxiv.org/abs/{paper_id}",
        file_path=f"pdf/{paper_id}.pdf",
        media_type="application/pdf",
        license="http://creativecommons.org/licenses/by/4.0/",
    )


def hit(
    text: str, chunk_id: str = "2501.00001#0", rank: int = 1, title: str = "B+ Trees"
) -> SearchHit:
    paper_id = chunk_id.split("#")[0]
    return SearchHit(
        rank=rank,
        chunk_id=chunk_id,
        paper=paper(paper_id, title),
        page_start=3,
        page_end=4,
        text=text,
        score=1.0 / rank,
        bm25=None,
        vector=None,
    )


def sources(*texts: str) -> list[Source]:
    return [
        Source(n, hit(text, chunk_id=f"2501.0000{n}#0", rank=n))
        for n, text in enumerate(texts, start=1)
    ]


def completion(
    stop_reason: StopReason = StopReason.END_TURN,
    usage: TokenUsage | None = None,
    category: str | None = None,
) -> Completion:
    attempt = Attempt(SONNET_5_5, usage or TokenUsage(input_tokens=1000, output_tokens=100))
    return Completion(SONNET_5_5, stop_reason, category, (attempt,))


@dataclass
class ScriptedModel:
    """Un `AnswerModel` que entrega `script` en orden: un `LlmError` se lanza, y con
    `hang_after` deja de responder después de esos eventos (un proveedor que tarda)."""

    script: Sequence[ModelEvent | LlmError | Exception]
    hang_after: int | None = None
    name: str = SONNET_5_5
    prompts: list[Prompt] = field(default_factory=list)
    max_tokens: list[int] = field(default_factory=list)
    closed: bool = False

    async def stream(self, prompt: Prompt, max_tokens: int) -> AsyncIterator[ModelEvent]:
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        try:
            for position, step in enumerate(self.script):
                if self.hang_after is not None and position >= self.hang_after:
                    await anyio.sleep_forever()
                if isinstance(step, Exception):
                    raise step
                yield step
        finally:
            self.closed = True


def answering_script(*texts: str, end: Completion | None = None) -> list[ModelEvent]:
    return [*(TextDelta(text) for text in texts), end or completion()]


def sse_events(*events: dict[str, Any]) -> bytes:
    """El cuerpo SSE de la API de mensajes con `events`, cada uno con su `type`."""
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def message_start(
    model: str = SONNET_5_5, input_tokens: int = 900, cache_read: int = 700
) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def text_block(index: int, *pieces: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        },
        *(
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": piece},
            }
            for piece in pieces
        ),
        {"type": "content_block_stop", "index": index},
    ]


def thinking_block(index: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "signature_delta", "signature": "firma"},
        },
        {"type": "content_block_stop", "index": index},
    ]


def fallback_block(index: int, from_model: str, to_model: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "fallback",
                "from": {"model": from_model},
                "to": {"model": to_model},
                "trigger": {"type": "refusal", "category": "cyber"},
            },
        },
        {"type": "content_block_stop", "index": index},
    ]


def message_delta(
    stop_reason: str = "end_turn",
    output_tokens: int = 42,
    stop_details: dict[str, Any] | None = None,
    iterations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    usage: dict[str, Any] = {"output_tokens": output_tokens}
    if iterations is not None:
        usage["iterations"] = iterations
    return {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details},
        "usage": usage,
    }


def message_stop() -> dict[str, Any]:
    return {"type": "message_stop"}
