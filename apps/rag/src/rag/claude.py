"""Claude por la API de Anthropic con el SDK oficial (`anthropic`), en streaming.

La petición:

- `system`: las instrucciones fijas, con `cache_control` efímero (5 minutos): todas las
  preguntas comparten ese prefijo y lo leen de la caché a una vigésima parte del precio;
- un mensaje del usuario con dos bloques: los documentos y, al final, la pregunta;
- pensamiento adaptativo con el esfuerzo configurado (`low` por omisión: respuestas cortas
  sobre pasajes dados, donde en ese nivel el modelo casi no razona antes de escribir). En
  Sonnet 5.5 el pensamiento no se puede apagar (`disabled` es un 400) y sus bloques llegan
  vacíos (`display: "omitted"`): no se reenvían, la conversación es de un solo turno;
- respaldo del servidor (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`): si
  un clasificador rechaza la pregunta en una categoría que la API sabe rescatar, la misma
  llamada sigue con Claude Sonnet 5 y el stream trae un bloque `fallback` en el punto del
  cambio. Los tokens de cada intento llegan en `usage.iterations`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import anthropic
from anthropic.lib.streaming import (
    BetaAsyncMessageStreamManager,
    ParsedBetaMessageStreamEvent,
    ParsedBetaTextEvent,
)
from anthropic.types import CacheCreation, Usage
from anthropic.types.beta import (
    BetaCacheCreation,
    BetaFallbackBlock,
    BetaFallbackMessageIterationUsage,
    BetaMessage,
    BetaMessageIterationUsage,
    BetaRawContentBlockStartEvent,
    BetaTextBlockParam,
    BetaUsage,
)

from .llm import (
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
from .pricing import Attempt, TokenUsage
from .prompt import Prompt

Effort = Literal["low", "medium", "high", "xhigh", "max"]

SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_FALLBACKS: Literal["default"] = "default"

FIRST_ERROR_STATUS = 400
RATE_LIMIT_ERROR_TYPE = "rate_limit_error"

UNAVAILABLE_MESSAGE = "el modelo de lenguaje no está disponible; intenta de nuevo en un momento"
RATE_LIMITED_MESSAGE = "el modelo de lenguaje está limitando las peticiones; intenta más tarde"
ACCESS_MESSAGE = "el servicio no tiene acceso al modelo de lenguaje"
REQUEST_MESSAGE = "el modelo de lenguaje rechazó la petición del servicio"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaudeOptions:
    """Qué modelo se pide, con qué esfuerzo y si se activa el respaldo del servidor."""

    model: str
    effort: Effort
    server_fallback: bool


class ClaudeModel:
    """Un `AnswerModel` sobre `AsyncAnthropic`."""

    def __init__(self, client: anthropic.AsyncAnthropic, options: ClaudeOptions) -> None:
        self._client = client
        self._options = options

    @property
    def name(self) -> str:
        return self._options.model

    async def stream(self, prompt: Prompt, max_tokens: int) -> AsyncIterator[ModelEvent]:
        try:
            async with self._open(prompt, max_tokens) as stream:
                async for event in stream:
                    model_event = _model_event(event)
                    if model_event is not None:
                        yield model_event
                message = await stream.get_final_message()
        except anthropic.AnthropicError as error:
            raise llm_error_of(error) from error
        yield completion_of(message, self._options.model)

    def _open(self, prompt: Prompt, max_tokens: int) -> BetaAsyncMessageStreamManager[None]:
        options = self._options
        system: list[BetaTextBlockParam] = [
            {"type": "text", "text": prompt.system, "cache_control": {"type": "ephemeral"}}
        ]
        user_blocks: list[BetaTextBlockParam] = [
            {"type": "text", "text": prompt.documents},
            {"type": "text", "text": prompt.question},
        ]
        return self._client.beta.messages.stream(
            model=options.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_blocks}],
            thinking={"type": "adaptive"},
            output_config={"effort": options.effort},
            fallbacks=DEFAULT_FALLBACKS if options.server_fallback else anthropic.omit,
            betas=[SERVER_SIDE_FALLBACK_BETA] if options.server_fallback else anthropic.omit,
        )


def _model_event(event: ParsedBetaMessageStreamEvent[None]) -> ModelEvent | None:
    if isinstance(event, ParsedBetaTextEvent):
        return TextDelta(event.text)
    if isinstance(event, BetaRawContentBlockStartEvent) and isinstance(
        event.content_block, BetaFallbackBlock
    ):
        return ModelSwitch(event.content_block.from_.model, event.content_block.to.model)
    return None


def completion_of(message: BetaMessage, requested_model: str) -> Completion:
    """La `Completion` del mensaje final: motivo de parada, categoría de un rechazo y los
    tokens de cada intento (de `usage.iterations` si los trae; si no, del total)."""
    category = message.stop_details.category if message.stop_details is not None else None
    return Completion(
        model=message.model,
        stop_reason=_stop_reason(message.stop_reason),
        refusal_category=category,
        attempts=attempts_of(message.usage, message.model, requested_model),
    )


def attempts_of(usage: BetaUsage, served_model: str, requested_model: str) -> tuple[Attempt, ...]:
    """Un `Attempt` por pasada de modelo. Una entrada `message` sin modelo es del modelo
    pedido; la `fallback_message` dice el suyo. Las de compactación o del asesor no ocurren
    en esta petición y no se cuentan."""
    if not usage.iterations:
        return (Attempt(served_model, _usage_of(usage)),)
    return tuple(
        Attempt(iteration.model or requested_model, _iteration_usage(iteration))
        for iteration in usage.iterations
        if isinstance(iteration, BetaMessageIterationUsage | BetaFallbackMessageIterationUsage)
    )


def llm_error_of(error: anthropic.AnthropicError) -> LlmError:
    """El `LlmError` que corresponde a un error del SDK, de lo más específico a lo general;
    el detalle queda en el log."""
    logger.warning("la API de Anthropic falló: %s: %s", type(error).__name__, error)
    if isinstance(error, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return LlmAccessError(ACCESS_MESSAGE)
    if isinstance(error, anthropic.RateLimitError):
        return LlmRateLimitedError(RATE_LIMITED_MESSAGE)
    if isinstance(error, anthropic.InternalServerError | anthropic.APIConnectionError):
        return LlmUnavailableError(UNAVAILABLE_MESSAGE)
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= FIRST_ERROR_STATUS:
        return LlmRequestError(REQUEST_MESSAGE)
    if _streamed_error_type(error) == RATE_LIMIT_ERROR_TYPE:
        return LlmRateLimitedError(RATE_LIMITED_MESSAGE)
    return LlmUnavailableError(UNAVAILABLE_MESSAGE)


def _streamed_error_type(error: anthropic.AnthropicError) -> str | None:
    """El `error.type` de un evento `error` que llegó dentro de un stream ya aceptado (con
    estado 200): `overloaded_error`, `api_error`, `rate_limit_error`..."""
    body = getattr(error, "body", None)
    detail = body.get("error") if isinstance(body, dict) else None
    kind = detail.get("type") if isinstance(detail, dict) else None
    return kind if isinstance(kind, str) else None


def _stop_reason(value: str | None) -> StopReason:
    if value is None:
        return StopReason.UNKNOWN
    try:
        return StopReason(value)
    except ValueError:
        return StopReason.UNKNOWN


def _usage_of(usage: BetaUsage) -> TokenUsage:
    return _token_usage(
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_input_tokens or 0,
        usage.cache_read_input_tokens or 0,
        usage.cache_creation,
    )


def usage_of(usage: Usage) -> TokenUsage:
    """Los tokens de un mensaje de la API de mensajes sin betas (el juez y la Batch API de
    la evaluación)."""
    return _token_usage(
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_input_tokens or 0,
        usage.cache_read_input_tokens or 0,
        usage.cache_creation,
    )


def _iteration_usage(
    iteration: BetaMessageIterationUsage | BetaFallbackMessageIterationUsage,
) -> TokenUsage:
    return _token_usage(
        iteration.input_tokens,
        iteration.output_tokens,
        iteration.cache_creation_input_tokens,
        iteration.cache_read_input_tokens,
        iteration.cache_creation,
    )


def _token_usage(
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    breakdown: BetaCacheCreation | CacheCreation | None,
) -> TokenUsage:
    # Sin el desglose por vida de la caché, toda escritura es de 5 minutos: la única que
    # pide este servicio.
    one_hour = breakdown.ephemeral_1h_input_tokens if breakdown is not None else 0
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_5m_tokens=cache_creation_tokens - one_hour,
        cache_write_1h_tokens=one_hour,
        cache_read_tokens=cache_read_tokens,
    )
