"""Serialización de registros de tamaño fijo.

Un registro se guarda como un mapa de nulos seguido de los campos empaquetados con un
único `struct`. El tamaño fijo es lo que permite que heap file, archivo secuencial y
árbol B+ compartan el mismo formato de página y calculen su capacidad de antemano.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from typing import Any

from .schema import Schema
from .types import FieldCodec, InvalidValueError, Value, codec_for

BITS_PER_BYTE = 8
BYTE_ORDER = "<"

Record = tuple[Value, ...]


class RecordSerializer:
    """Convierte tuplas de valores en bytes de longitud constante y viceversa.

    El mapa de nulos ocupa `ceil(n_campos / 8)` bytes; cada bit en 1 indica NULL. Los
    campos nulos igual escriben su valor neutro para que el registro conserve su tamaño.

    Raises:
        InvalidValueError: si la tupla no tiene la aridad del esquema, si un valor no
            corresponde al tipo del campo o si un campo no anulable recibe NULL.
    """

    def __init__(self, schema: Schema) -> None:
        self._schema = schema
        self._codecs: tuple[FieldCodec, ...] = tuple(
            codec_for(field.type, field.length) for field in schema
        )
        self._null_bitmap_size = -(-len(schema) // BITS_PER_BYTE)
        self._struct = struct.Struct(BYTE_ORDER + "".join(codec.format for codec in self._codecs))
        self._field_structs: tuple[struct.Struct, ...] = tuple(
            struct.Struct(BYTE_ORDER + codec.format) for codec in self._codecs
        )
        self._field_offsets = self._compute_field_offsets()

    @property
    def schema(self) -> Schema:
        return self._schema

    @property
    def size(self) -> int:
        """Bytes que ocupa cualquier registro de este esquema."""
        return self._null_bitmap_size + self._struct.size

    def pack(self, values: Sequence[Value]) -> bytes:
        if len(values) != len(self._schema):
            raise InvalidValueError(
                f"el registro tiene {len(values)} valores y el esquema {len(self._schema)}"
            )
        bitmap = bytearray(self._null_bitmap_size)
        slots: list[Any] = []
        for position, (value, codec) in enumerate(zip(values, self._codecs, strict=True)):
            if value is None:
                self._reject_null_on_required_field(position)
                bitmap[position // BITS_PER_BYTE] |= 1 << (position % BITS_PER_BYTE)
                slots.extend(codec.empty_slots())
                continue
            slots.extend(codec.to_slots(value))
        return bytes(bitmap) + self._struct.pack(*slots)

    def unpack(self, raw: bytes) -> Record:
        if len(raw) != self.size:
            raise InvalidValueError(
                f"el registro ocupa {len(raw)} bytes y se esperaban {self.size}"
            )
        bitmap = raw[: self._null_bitmap_size]
        slots = self._struct.unpack(raw[self._null_bitmap_size :])
        values: list[Value] = []
        offset = 0
        for position, codec in enumerate(self._codecs):
            chunk = slots[offset : offset + codec.slots]
            offset += codec.slots
            if self._is_null(bitmap, position):
                values.append(None)
                continue
            values.append(codec.from_slots(chunk))
        return tuple(values)

    def unpack_field(self, raw: bytes, position: int) -> Value:
        """Desempaqueta un solo campo sin reconstruir el registro completo.

        Es lo que usan el archivo secuencial y los índices para leer la clave de un
        registro sin pagar la deserialización de las demás columnas.
        """
        if self._is_null(raw[: self._null_bitmap_size], position):
            return None
        offset = self._field_offsets[position]
        field_struct = self._field_structs[position]
        slots = field_struct.unpack_from(raw, offset)
        return self._codecs[position].from_slots(slots)

    def _compute_field_offsets(self) -> tuple[int, ...]:
        offsets: list[int] = []
        offset = self._null_bitmap_size
        for field_struct in self._field_structs:
            offsets.append(offset)
            offset += field_struct.size
        return tuple(offsets)

    def _reject_null_on_required_field(self, position: int) -> None:
        field = self._schema.fields[position]
        if not field.nullable:
            raise InvalidValueError(f"el campo '{field.name}' no admite NULL")

    @staticmethod
    def _is_null(bitmap: bytes, position: int) -> bool:
        return bool(bitmap[position // BITS_PER_BYTE] & (1 << (position % BITS_PER_BYTE)))
