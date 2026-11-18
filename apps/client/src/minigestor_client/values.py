"""Valores de Python a JSON, como los espera el gestor en `params` y en la carga masiva.

| Python | JSON | Tipo en el gestor |
|---|---|---|
| `int`, `float`, `str`, `bool`, `None` | el mismo | el literal del mismo tipo |
| `list`/`tuple` de números, arreglo de numpy | lista de números | `VECTOR` |
| `Point` | `{"lat", "lon"}` | `POINT` |

numpy no es una dependencia: cualquier objeto con `tolist()` (un arreglo o un escalar de
numpy) se convierte con ese método.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

NDJSON_LINE_END = b"\n"
# Juntar filas hasta unos 64 KiB antes de escribir: una fila por trozo multiplicaría las
# cabeceras del envío por trozos y las llamadas al socket, y más no ahorra nada medible.
NDJSON_CHUNK_BYTES = 64 * 1024


class UnencodableValueError(ValueError):
    """Un valor que el gestor no sabría leer: un objeto sin forma JSON, un `NaN`, un infinito."""


@dataclass(frozen=True, slots=True)
class Point:
    """Un punto geográfico en grados decimales, el `POINT` del gestor."""

    lat: float
    lon: float


def _json_of(value: Any) -> Any:
    if isinstance(value, Point):
        return {"lat": value.lat, "lon": value.lon}
    to_list = getattr(value, "tolist", None)
    if callable(to_list):
        return to_list()
    raise UnencodableValueError(f"{type(value).__name__} no tiene forma JSON: {value!r}")


_ENCODER = json.JSONEncoder(
    default=_json_of, allow_nan=False, ensure_ascii=False, separators=(",", ":")
)


def encode_json(value: Any) -> bytes:
    """`value` en JSON compacto y UTF-8.

    Raises:
        UnencodableValueError: si contiene un valor sin forma JSON, un `NaN` o un infinito.
    """
    try:
        return _ENCODER.encode(value).encode()
    except ValueError as error:
        raise UnencodableValueError(str(error)) from error


def ndjson_line(row: Mapping[str, Any]) -> bytes:
    """Una fila de la carga masiva, `{"columna": valor}`, como línea NDJSON.

    Raises:
        UnencodableValueError: si la fila no es un diccionario o tiene un valor sin forma JSON.
    """
    if not isinstance(row, Mapping):
        raise UnencodableValueError(f"cada fila es un diccionario, no {type(row).__name__}")
    return encode_json(row) + NDJSON_LINE_END


def ndjson_chunks(
    rows: Iterable[Mapping[str, Any]], chunk_bytes: int = NDJSON_CHUNK_BYTES
) -> Iterator[bytes]:
    """Las filas en trozos de unos `chunk_bytes`, pidiéndolas a `rows` solo cuando hacen falta:
    nunca hay más de un trozo en memoria."""
    pending = bytearray()
    for row in rows:
        pending += ndjson_line(row)
        if len(pending) >= chunk_bytes:
            yield bytes(pending)
            pending.clear()
    if pending:
        yield bytes(pending)


async def async_ndjson_chunks(
    rows: AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    chunk_bytes: int = NDJSON_CHUNK_BYTES,
) -> AsyncIterator[bytes]:
    """Como `ndjson_chunks`, para filas que llegan de un iterable síncrono o asíncrono."""
    pending = bytearray()
    async for row in _aiterate(rows):
        pending += ndjson_line(row)
        if len(pending) >= chunk_bytes:
            yield bytes(pending)
            pending.clear()
    if pending:
        yield bytes(pending)


async def _aiterate(
    rows: AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> AsyncIterator[Mapping[str, Any]]:
    if isinstance(rows, AsyncIterable):
        async for row in rows:
            yield row
        return
    for row in rows:
        yield row
