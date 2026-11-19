"""El protocolo del reconocimiento en vivo por WebSocket (`/ws/recognize`).

El celular manda **cuadros** en mensajes binarios: una cabecera de 5 bytes —la versión del
protocolo (1 byte) y el número del cuadro (4 bytes, entero sin signo big-endian)— seguida de
la imagen, un JPEG reducido. El número lo pone el cliente y vuelve en la respuesta, así que
el cliente mide la latencia de cada cuadro sin relojes compartidos.

Opcionalmente manda un mensaje de texto `{"type": "hello", "session": "…", "track_cache":
true}` para retomar los rastros de una sesión anterior (tras reconectarse, en cualquier
réplica) o para pedir cuadros sin la caché de rastros (medir su efecto).

El servidor responde con JSON en mensajes de texto: `ready` al conectarse y tras cada
`hello`, un `result` por cuadro procesado y un `error` por cuadro o mensaje que no sirve,
sin cerrar la conexión.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from faces.api.models import FaceBox, MatchStatus

PROTOCOL_VERSION = 1
FRAME_HEADER = struct.Struct(">BI")
SESSION_PATTERN = r"^[0-9a-f]{32}$"
UNKNOWN_LABEL = "desconocido"
TOO_SMALL_LABEL = "acércate"


class StreamProtocolError(Exception):
    """Un mensaje del cliente que no se puede atender. Se responde con un `error` y la
    conexión sigue."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @property
    def kind(self) -> str:
        return type(self).__name__


class InvalidFrameError(StreamProtocolError):
    """Un mensaje binario sin la cabecera del protocolo, de otra versión o sin imagen."""


class FrameTooLargeError(StreamProtocolError):
    """Un cuadro de más bytes que el límite: no se decodifica."""


class InvalidMessageError(StreamProtocolError):
    """Un mensaje de texto que no es un `hello` válido."""


@dataclass(frozen=True, slots=True)
class Frame:
    """Un cuadro recibido: su número, su imagen y cuándo llegó (reloj monotónico)."""

    number: int
    image: bytes
    received_at: float


def encode_frame(number: int, image: bytes) -> bytes:
    """El mensaje binario de un cuadro: lo que manda el cliente."""
    return FRAME_HEADER.pack(PROTOCOL_VERSION, number) + image


def decode_frame(message: bytes, received_at: float, max_bytes: int) -> Frame:
    """El cuadro de un mensaje binario.

    Raises:
        FrameTooLargeError: si la imagen pesa más de `max_bytes`.
        InvalidFrameError: si falta la cabecera, es de otra versión o no trae imagen.
    """
    if len(message) <= FRAME_HEADER.size:
        raise InvalidFrameError(
            f"Un cuadro empieza con {FRAME_HEADER.size} bytes de cabecera y sigue con la imagen."
        )
    version, number = FRAME_HEADER.unpack_from(message)
    if version != PROTOCOL_VERSION:
        raise InvalidFrameError(f"Versión del protocolo {version}; se espera {PROTOCOL_VERSION}.")
    image = message[FRAME_HEADER.size :]
    if len(image) > max_bytes:
        raise FrameTooLargeError(
            f"El cuadro pesa {len(image)} bytes y el máximo es {max_bytes}. Redúcelo."
        )
    return Frame(number, image, received_at)


class Hello(BaseModel):
    """Lo que el cliente puede pedir con un mensaje de texto."""

    type: Literal["hello"]
    session: str | None = Field(default=None, pattern=SESSION_PATTERN)
    track_cache: bool = True


class StreamLimitsView(BaseModel):
    max_frame_bytes: int
    max_frame_pixels: int
    max_fps: float
    idle_timeout_seconds: float


class Ready(BaseModel):
    type: Literal["ready"] = "ready"
    session: str = Field(description="Para retomar los rastros con un `hello` al reconectarse.")
    track_cache: bool = Field(description="Si los cuadros de esta conexión usan los rastros.")
    limits: StreamLimitsView


class StreamFace(BaseModel):
    track: int | None = Field(description="El rastro del rostro; `None` sin caché de rastros.")
    box: FaceBox
    status: MatchStatus
    label: str = Field(description="Lo que se dibuja: el nombre, «desconocido» o «acércate».")
    display_name: str | None = None
    score: float | None = None
    cached: bool = Field(description="Si la identidad salió del rastro, sin buscar de nuevo.")


class StageTimings(BaseModel):
    """Milisegundos de cada etapa del cuadro en el servidor."""

    wait: float = Field(description="Desde que llegó hasta que empezó a procesarse.")
    decode: float
    detect: float
    track: float = Field(description="Leer y guardar los rastros en Redis.")
    embed: float
    search: float = Field(description="El HNSW y el nombre de la persona en el gestor.")
    total: float = Field(description="Desde que llegó hasta que se respondió.")


class FrameResult(BaseModel):
    type: Literal["result"] = "result"
    frame: int
    faces: list[StreamFace] = Field(description="Del rostro más grande al más chico.")
    dropped: int = Field(description="Cuadros descartados en la conexión, sin procesar.")
    timings: StageTimings


class StreamError(BaseModel):
    type: Literal["error"] = "error"
    frame: int | None
    kind: str
    error: str
