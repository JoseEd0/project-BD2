"""Sistema de tipos del almacenamiento: cómo cada valor se convierte en bytes.

El almacenamiento no conoce SQL. La capa de consultas traduce los tipos del dialecto
a los `FieldType` de este módulo, que son los únicos que saben serializarse.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import date, timedelta
from enum import Enum, unique
from typing import Any

EPOCH = date(1970, 1, 1)
STRING_PADDING = b"\x00"
TEXT_ENCODING = "utf-8"

Value = int | float | bool | str | bytes | date | tuple[float, ...] | None


class StorageError(Exception):
    """Base de los errores de la capa de almacenamiento."""


class ValueTooLargeError(StorageError):
    """El valor no cabe en la longitud declarada del campo."""


class InvalidValueError(StorageError):
    """El valor no corresponde al tipo del campo."""


@unique
class FieldType(Enum):
    INT = "INT"
    FLOAT = "FLOAT"
    BOOL = "BOOL"
    STRING = "STRING"
    DATE = "DATE"
    POINT = "POINT"
    VECTOR = "VECTOR"
    BYTES = "BYTES"


SIZED_FIELD_TYPES = frozenset({FieldType.STRING, FieldType.VECTOR, FieldType.BYTES})
ORDERED_FIELD_TYPES = frozenset(
    {FieldType.INT, FieldType.FLOAT, FieldType.BOOL, FieldType.STRING, FieldType.DATE}
)


class FieldCodec(ABC):
    """Traduce un valor de Python a las ranuras de un `struct` de tamaño fijo.

    Cada códec aporta un fragmento de formato al `struct` del registro completo, de modo
    que empaquetar una tupla es una sola llamada a `struct.pack`.
    """

    format: str
    slots: int

    @property
    def size(self) -> int:
        return struct.calcsize("<" + self.format)

    @abstractmethod
    def to_slots(self, value: Value) -> tuple[Any, ...]:
        """Descompone el valor en las ranuras que consume el formato."""

    @abstractmethod
    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        """Reconstruye el valor a partir de sus ranuras."""

    def empty_slots(self) -> tuple[Any, ...]:
        """Ranuras que se escriben cuando el campo es NULL."""
        return self.to_slots(self.neutral_value())

    @abstractmethod
    def neutral_value(self) -> Value:
        """Valor de relleno usado para los campos nulos."""


class IntCodec(FieldCodec):
    format = "q"
    slots = 1

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidValueError(f"se esperaba un entero, llegó {value!r}")
        return (value,)

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        return int(slots[0])

    def neutral_value(self) -> Value:
        return 0


class FloatCodec(FieldCodec):
    format = "d"
    slots = 1

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidValueError(f"se esperaba un número, llegó {value!r}")
        return (float(value),)

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        return float(slots[0])

    def neutral_value(self) -> Value:
        return 0.0


class BoolCodec(FieldCodec):
    format = "?"
    slots = 1

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if not isinstance(value, bool):
            raise InvalidValueError(f"se esperaba un booleano, llegó {value!r}")
        return (value,)

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        return bool(slots[0])

    def neutral_value(self) -> Value:
        return False


class DateCodec(FieldCodec):
    """Guarda la fecha como días desde 1970-01-01, que preserva el orden natural."""

    format = "i"
    slots = 1

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if not isinstance(value, date):
            raise InvalidValueError(f"se esperaba una fecha, llegó {value!r}")
        return ((value - EPOCH).days,)

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        return EPOCH + timedelta(days=int(slots[0]))

    def neutral_value(self) -> Value:
        return EPOCH


class StringCodec(FieldCodec):
    slots = 1

    def __init__(self, length: int) -> None:
        self.length = length
        self.format = f"{length}s"

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if not isinstance(value, str):
            raise InvalidValueError(f"se esperaba una cadena, llegó {value!r}")
        encoded = value.encode(TEXT_ENCODING)
        if len(encoded) > self.length:
            raise ValueTooLargeError(
                f"la cadena ocupa {len(encoded)} bytes y el campo admite {self.length}"
            )
        return (encoded,)

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        raw: bytes = slots[0]
        return raw.rstrip(STRING_PADDING).decode(TEXT_ENCODING)

    def neutral_value(self) -> Value:
        return ""


class BytesCodec(FieldCodec):
    slots = 1

    def __init__(self, length: int) -> None:
        self.length = length
        self.format = f"{length}s"

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if not isinstance(value, bytes):
            raise InvalidValueError(f"se esperaban bytes, llegó {value!r}")
        if len(value) > self.length:
            raise ValueTooLargeError(
                f"el valor ocupa {len(value)} bytes y el campo admite {self.length}"
            )
        return (value,)

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        raw: bytes = slots[0]
        return raw.rstrip(STRING_PADDING)

    def neutral_value(self) -> Value:
        return b""


class PointCodec(FieldCodec):
    format = "dd"
    slots = 2
    COORDINATES = 2

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if not isinstance(value, tuple) or len(value) != self.COORDINATES:
            raise InvalidValueError(f"se esperaba un par de coordenadas, llegó {value!r}")
        return (float(value[0]), float(value[1]))

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        return (float(slots[0]), float(slots[1]))

    def neutral_value(self) -> Value:
        return (0.0, 0.0)


class VectorCodec(FieldCodec):
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.format = f"{dimension}f"
        self.slots = dimension

    def to_slots(self, value: Value) -> tuple[Any, ...]:
        if not isinstance(value, tuple) or len(value) != self.dimension:
            raise InvalidValueError(
                f"se esperaba un vector de dimensión {self.dimension}, llegó {value!r}"
            )
        return tuple(float(component) for component in value)

    def from_slots(self, slots: tuple[Any, ...]) -> Value:
        return tuple(float(component) for component in slots)

    def neutral_value(self) -> Value:
        return (0.0,) * self.dimension


_UNSIZED_CODECS: dict[FieldType, FieldCodec] = {
    FieldType.INT: IntCodec(),
    FieldType.FLOAT: FloatCodec(),
    FieldType.BOOL: BoolCodec(),
    FieldType.DATE: DateCodec(),
    FieldType.POINT: PointCodec(),
}

_SIZED_CODEC_FACTORIES: dict[FieldType, Callable[[int], FieldCodec]] = {
    FieldType.STRING: StringCodec,
    FieldType.BYTES: BytesCodec,
    FieldType.VECTOR: VectorCodec,
}


def codec_for(field_type: FieldType, length: int | None) -> FieldCodec:
    """Devuelve el códec del tipo, exigiendo longitud solo donde el tipo la necesita.

    Raises:
        InvalidValueError: si falta la longitud, sobra, o no es positiva.
    """
    if field_type in SIZED_FIELD_TYPES:
        if length is None or length <= 0:
            raise InvalidValueError(f"{field_type.value} requiere una longitud positiva")
        return _SIZED_CODEC_FACTORIES[field_type](length)
    if length is not None:
        raise InvalidValueError(f"{field_type.value} no admite longitud")
    return _UNSIZED_CODECS[field_type]
