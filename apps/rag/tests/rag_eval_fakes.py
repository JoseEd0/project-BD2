"""Respuestas de la API de Anthropic escritas a mano para probar la evaluación con el SDK
real sobre un transporte HTTP de mentira: mensajes, lotes y resultados de la Batch API."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2

from rag.pricing import SONNET_5_5

Handler = Callable[[httpx2.Request], httpx2.Response]
BATCH_ID = "msgbatch_prueba"
RESULTS_URL = f"https://api.anthropic.com/v1/messages/batches/{BATCH_ID}/results"


def message(
    text: str | None,
    stop_reason: str = "end_turn",
    model: str = SONNET_5_5,
    usage: tuple[int, int] = (1000, 200),
) -> dict[str, Any]:
    content = [] if text is None else [{"type": "text", "text": text}]
    return {
        "id": "msg_prueba",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
    }


def batch(status: str) -> dict[str, Any]:
    return {
        "id": BATCH_ID,
        "type": "message_batch",
        "processing_status": status,
        "request_counts": {
            "processing": 0, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0,
        },
        "created_at": "2026-11-19T00:00:00Z",
        "expires_at": "2026-11-20T00:00:00Z",
        "ended_at": None,
        "archived_at": None,
        "cancel_initiated_at": None,
        "results_url": RESULTS_URL if status == "ended" else None,
    }  # fmt: skip


def succeeded(custom_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"custom_id": custom_id, "result": {"type": "succeeded", "message": body}}


def errored(custom_id: str) -> dict[str, Any]:
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "mal"}}
    return {"custom_id": custom_id, "result": {"type": "errored", "error": error}}


def json_response(body: dict[str, Any], status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json=body)


def jsonl_response(rows: list[dict[str, Any]]) -> httpx2.Response:
    content = "".join(json.dumps(row) + "\n" for row in rows).encode()
    return httpx2.Response(200, content=content)


def sync_client(handler: Handler) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="clave-de-prueba",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def async_client(handler: Handler) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key="clave-de-prueba",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
