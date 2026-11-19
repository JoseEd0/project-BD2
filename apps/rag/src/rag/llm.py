"""Lo que el servicio necesita de un modelo de lenguaje, sin atarse a un proveedor.

Un `AnswerModel` recibe el `Prompt` y entrega, en streaming, trozos de texto, el aviso de que
la API pasó la respuesta a un modelo de respaldo y, al final, una `Completion` con el motivo de
parada y los tokens de cada intento. Hay dos: Claude por la API de Anthropic (`rag.claude`) y
uno simulado sin red (`rag.simulated`) para los tests y la CI.

Un fallo del proveedor se lanza como `LlmError`, con el estado HTTP y el mensaje que ve quien
preguntó; el detalle del proveedor queda en el log.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import ClassVar, Protocol

from .pricing import Attempt
from .prompt import Prompt


class StopReason(StrEnum):
    """Por qué terminó el modelo. Solo `END_TURN` es una respuesta completa."""

    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    STOP_SEQUENCE = "stop_sequence"
    PAUSE_TURN = "pause_turn"
    TOOL_USE = "tool_use"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ModelSwitch:
    """El modelo `from_model` rechazó la petición y `to_model` sigue desde ahí."""

    from_model: str
    to_model: str


@dataclass(frozen=True, slots=True)
class Completion:
    """El final del stream. `model` es el que produjo la respuesta; `refusal_category`, la
    categoría de un rechazo (`None` si no lo hubo o si la API no la nombra)."""

    model: str
    stop_reason: StopReason
    refusal_category: str | None
    attempts: tuple[Attempt, ...]


ModelEvent = TextDelta | ModelSwitch | Completion


class AnswerModel(Protocol):
    """Un modelo que responde un `Prompt` en streaming."""

    @property
    def name(self) -> str: ...

    def stream(self, prompt: Prompt, max_tokens: int) -> AsyncIterator[ModelEvent]:
        """Los eventos de la respuesta; el último es siempre una `Completion`.

        Cerrar el iterador antes de terminar cancela la petición al proveedor.

        Raises:
            LlmError: si el proveedor falla antes o durante la respuesta.
        """
        ...


class LlmError(Exception):
    """Un fallo del proveedor del modelo, con el estado y el mensaje que se muestran."""

    status: ClassVar[HTTPStatus] = HTTPStatus.SERVICE_UNAVAILABLE

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @property
    def kind(self) -> str:
        return type(self).__name__


class LlmUnavailableError(LlmError):
    """El proveedor no respondió, está sobrecargado o falló del suyo: reintentar puede servir."""

    status = HTTPStatus.SERVICE_UNAVAILABLE


class LlmRateLimitedError(LlmError):
    """El proveedor limitó la cuenta del servicio por ritmo o por cuota."""

    status = HTTPStatus.TOO_MANY_REQUESTS


class LlmAccessError(LlmError):
    """La clave del proveedor falta, es inválida o no tiene permiso: es configuración."""

    status = HTTPStatus.INTERNAL_SERVER_ERROR


class LlmRequestError(LlmError):
    """El proveedor rechazó la petición tal como la armó el servicio: es un bug."""

    status = HTTPStatus.INTERNAL_SERVER_ERROR
