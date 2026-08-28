"""Códecs de clave compartidos por todos los índices.

Una clave de índice se guarda en disco como bytes de tamaño fijo y se compara en memoria
como valor de Python. Las claves compuestas se representan como tuplas, que Python ya
compara en orden lexicográfico, que es justo el orden que un índice necesita.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from storage.record_id import RECORD_ID_SIZE, RecordId
from storage.schema import Field
from storage.types import codec_for

Key = Any


class KeyCodec(ABC):
    """Traduce una clave entre su valor de Python y sus bytes de tamaño fijo."""

    @property
    @abstractmethod
    def size(self) -> int:
        """Bytes que ocupa cualquier clave de este códec."""

    @abstractmethod
    def pack(self, key: Key) -> bytes:
        """Serializa la clave."""

    @abstractmethod
    def unpack(self, raw: bytes) -> Key:
        """Reconstruye la clave."""


class ScalarKeyCodec(KeyCodec):
    """Clave de una sola columna."""

    def __init__(self, field: Field) -> None:
        self._codec = codec_for(field.type, field.length)
        self._struct = struct.Struct("<" + self._codec.format)

    @property
    def size(self) -> int:
        return self._struct.size

    def pack(self, key: Key) -> bytes:
        return self._struct.pack(*self._codec.to_slots(key))

    def unpack(self, raw: bytes) -> Key:
        return self._codec.from_slots(self._struct.unpack(raw))


class CompositeKeyCodec(KeyCodec):
    """Clave de varias columnas, comparada como tupla en orden lexicográfico."""

    def __init__(self, codecs: Sequence[KeyCodec]) -> None:
        if not codecs:
            raise ValueError("una clave compuesta necesita al menos un componente")
        self._codecs = tuple(codecs)
        self._offsets = self._compute_offsets()
        self._size = sum(codec.size for codec in self._codecs)

    @property
    def size(self) -> int:
        return self._size

    def pack(self, key: Key) -> bytes:
        if len(key) != len(self._codecs):
            raise ValueError(
                f"la clave tiene {len(key)} componentes y el índice espera {len(self._codecs)}"
            )
        return b"".join(codec.pack(part) for codec, part in zip(self._codecs, key, strict=True))

    def unpack(self, raw: bytes) -> Key:
        return tuple(
            codec.unpack(raw[offset : offset + codec.size])
            for codec, offset in zip(self._codecs, self._offsets, strict=True)
        )

    def _compute_offsets(self) -> tuple[int, ...]:
        offsets: list[int] = []
        offset = 0
        for codec in self._codecs:
            offsets.append(offset)
            offset += codec.size
        return tuple(offsets)


class RecordIdKeyCodec(KeyCodec):
    """Componente de desempate: la dirección física del registro."""

    @property
    def size(self) -> int:
        return RECORD_ID_SIZE

    def pack(self, key: Key) -> bytes:
        return bytes(key.pack())

    def unpack(self, raw: bytes) -> Key:
        return RecordId.unpack(raw)
