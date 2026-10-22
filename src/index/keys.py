"""Códecs de clave compartidos por todos los índices.

Una clave de índice se guarda en disco como bytes de tamaño fijo y se compara en memoria
como valor de Python. Las claves compuestas se representan como tuplas, que Python ya
compara en orden lexicográfico, que es justo el orden que un índice necesita.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

from storage.record import BYTE_ORDER
from storage.record_id import RECORD_ID_FIELDS, RecordId
from storage.schema import Field
from storage.types import Key, StorageError, codec_for


class KeyShapeError(StorageError):
    """La clave no tiene los componentes que el códec espera."""


class KeyCodec(ABC):
    """Traduce una clave entre su valor de Python y sus bytes de tamaño fijo.

    Cada códec se describe como un formato de `struct` y la conversión entre la clave y los
    campos de ese formato. Así una clave compuesta es un solo `struct`, y las claves de un
    nodo entero se leen de una pasada con `iter_unpack` en vez de una a una.
    """

    def __init__(self, layout: str) -> None:
        self._struct = struct.Struct(BYTE_ORDER + layout)

    @property
    def layout(self) -> str:
        """Formato de `struct` de una clave, sin el orden de bytes."""
        return self._struct.format.removeprefix(BYTE_ORDER)

    @property
    def size(self) -> int:
        """Bytes que ocupa cualquier clave de este códec."""
        return self._struct.size

    @property
    @abstractmethod
    def slot_count(self) -> int:
        """Campos de `struct` que ocupa una clave."""

    @abstractmethod
    def to_slots(self, key: Key) -> tuple[Any, ...]:
        """Los campos de `struct` de la clave, validados."""

    @abstractmethod
    def from_slots(self, slots: tuple[Any, ...]) -> Key:
        """La clave a partir de sus campos."""

    def pack(self, key: Key) -> bytes:
        return self._struct.pack(*self.to_slots(key))

    def unpack(self, raw: bytes) -> Key:
        return self.from_slots(self._struct.unpack(raw))

    def pack_many(self, keys: Iterable[Key]) -> bytes:
        """Varias claves seguidas, como las guarda un nodo."""
        pack, to_slots = self._struct.pack, self.to_slots
        return b"".join(pack(*to_slots(key)) for key in keys)

    def unpack_many(self, raw: bytes) -> list[Key]:
        """Las claves guardadas seguidas en `raw`, cuyo largo es un múltiplo de `size`."""
        from_slots = self.from_slots
        return [from_slots(slots) for slots in self._struct.iter_unpack(raw)]


class ScalarKeyCodec(KeyCodec):
    """Clave de una sola columna."""

    def __init__(self, field: Field) -> None:
        self._codec = codec_for(field.type, field.length)
        super().__init__(self._codec.format)

    @property
    def slot_count(self) -> int:
        return self._codec.slots

    def to_slots(self, key: Key) -> tuple[Any, ...]:
        return self._codec.to_slots(key)

    def from_slots(self, slots: tuple[Any, ...]) -> Key:
        return self._codec.from_slots(slots)


class CompositeKeyCodec(KeyCodec):
    """Clave de varias columnas, comparada como tupla en orden lexicográfico.

    Sin alineación (`<`), el `struct` de la clave entera ocupa lo mismo que sus componentes
    uno detrás de otro: los bytes son los de empaquetar cada componente por separado.
    """

    def __init__(self, codecs: Sequence[KeyCodec]) -> None:
        if not codecs:
            raise KeyShapeError("una clave compuesta necesita al menos un componente")
        self._codecs = tuple(codecs)
        self._components = self._locate_components()
        super().__init__("".join(codec.layout for codec in self._codecs))

    @property
    def slot_count(self) -> int:
        return sum(codec.slot_count for codec in self._codecs)

    def to_slots(self, key: Key) -> tuple[Any, ...]:
        if len(key) != len(self._codecs):
            raise KeyShapeError(
                f"la clave tiene {len(key)} componentes y el índice espera {len(self._codecs)}"
            )
        slots: list[Any] = []
        for codec, part in zip(self._codecs, key, strict=True):
            slots.extend(codec.to_slots(part))
        return tuple(slots)

    def from_slots(self, slots: tuple[Any, ...]) -> Key:
        return tuple([codec.from_slots(slots[start:end]) for codec, start, end in self._components])

    def _locate_components(self) -> tuple[tuple[KeyCodec, int, int], ...]:
        """Cada componente con el tramo de campos que ocupa dentro de la clave."""
        components: list[tuple[KeyCodec, int, int]] = []
        start = 0
        for codec in self._codecs:
            components.append((codec, start, start + codec.slot_count))
            start += codec.slot_count
        return tuple(components)


class RecordIdKeyCodec(KeyCodec):
    """Componente de desempate: la dirección física del registro."""

    def __init__(self) -> None:
        super().__init__(RECORD_ID_FIELDS)

    @property
    def slot_count(self) -> int:
        return len(RECORD_ID_FIELDS)

    def to_slots(self, key: Key) -> tuple[Any, ...]:
        return (key.page_id, key.slot)

    def from_slots(self, slots: tuple[Any, ...]) -> Key:
        page_id, slot = slots
        return RecordId(page_id, slot)
