"""Filas con valores fuera del registro: las columnas TEXT.

Un TEXT no tiene longitud máxima, así que no cabe en un registro de tamaño fijo. El
registro guarda la dirección del valor (los 6 bytes de un `RecordId`) y el texto vive en un
archivo de valores de longitud variable. Por encima de esta clase una fila es siempre la
tupla lógica, con cada TEXT como `str`: el ejecutor, los índices y el registro de deshacer
de las transacciones no ven nunca una dirección.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .record import Record, RecordSerializer
from .record_id import LOWEST_RECORD_ID, RecordId
from .schema import Schema
from .types import OUT_OF_ROW_FIELD_TYPES, TEXT_ENCODING, InvalidValueError, StorageError, Value


class MissingValueStoreError(StorageError):
    """El esquema tiene columnas TEXT y no hay dónde guardar sus valores."""


class ValueStore(Protocol):
    """Dónde viven los valores de las columnas TEXT: un `VariableLengthFile` o algo que lo
    abra cuando hace falta."""

    def put(self, value: bytes) -> RecordId: ...

    def get(self, address: RecordId) -> bytes: ...

    def replace(self, address: RecordId, value: bytes) -> RecordId: ...

    def delete(self, address: RecordId) -> None: ...


def out_of_row_positions(schema: Schema) -> tuple[int, ...]:
    """Posiciones de las columnas cuyo valor vive fuera del registro."""
    return tuple(
        position for position, field in enumerate(schema) if field.type in OUT_OF_ROW_FIELD_TYPES
    )


class OutOfRowSerializer:
    """Convierte filas lógicas en registros de tamaño fijo, guardando aparte sus TEXT.

    Un esquema sin TEXT no toca `values`: cada método es directamente el de
    `RecordSerializer`. Con TEXT, `pack` comprueba la fila entera antes de guardar el primer
    valor, así que una fila inválida no deja valores huérfanos.

    Raises:
        MissingValueStoreError: si el esquema tiene TEXT y `values` es `None`.
    """

    def __init__(self, serializer: RecordSerializer, values: ValueStore | None) -> None:
        self._serializer = serializer
        self._positions = out_of_row_positions(serializer.schema)
        if self._positions and values is None:
            raise MissingValueStoreError("un esquema con columnas TEXT necesita archivo de valores")
        self._values = values

    @property
    def schema(self) -> Schema:
        return self._serializer.schema

    @property
    def size(self) -> int:
        """Bytes que ocupa cualquier registro de este esquema; el texto de un TEXT va aparte."""
        return self._serializer.size

    def normalized(self, row: Sequence[Value]) -> Record:
        """La fila tal como quedaría guardada —un entero en una columna real ya es real—,
        sin escribir nada.

        Raises:
            InvalidValueError: si algún valor no corresponde a su columna.
        """
        stored = self._serializer.unpack(self._draft(row))
        if not self._positions:
            return stored
        values = list(stored)
        for position in self._positions:
            values[position] = row[position]
        return tuple(values)

    def pack(self, row: Sequence[Value]) -> bytes:
        """El registro de la fila, tras guardar cada TEXT en el archivo de valores.

        Raises:
            InvalidValueError: si algún valor no corresponde a su columna; entonces no se
                guarda ningún valor.
        """
        if not self._positions:
            return self._serializer.pack(row)
        self._draft(row)
        values = list(row)
        for position in self._positions:
            text = row[position]
            values[position] = None if text is None else self._store().put(_encoded(text)).pack()
        return self._serializer.pack(values)

    def repack(self, record: bytes, before: Record, after: Sequence[Value]) -> bytes:
        """El registro de una fila que pasa de `before` a `after`, reutilizando sus valores.

        `record` es el registro guardado de `before`. Un TEXT que no cambia conserva su
        dirección y no se reescribe; uno que cambia se reemplaza en su sitio si cabe, y
        uno que pasa a NULL se borra.

        Raises:
            InvalidValueError: si algún valor de `after` no corresponde a su columna.
        """
        if not self._positions:
            return self._serializer.pack(after)
        self._draft(after)
        values = list(after)
        for position in self._positions:
            reference = self._reference_at(record, position)
            values[position] = self._replaced(reference, before[position], after[position])
        return self._serializer.pack(values)

    def unpack(self, record: bytes) -> Record:
        if not self._positions:
            return self._serializer.unpack(record)
        values = list(self._serializer.unpack(record))
        for position in self._positions:
            values[position] = self._resolved(values[position])
        return tuple(values)

    def unpack_field(self, raw: bytes, position: int) -> Value:
        """El valor de un solo campo; si es un TEXT, el texto, que cuesta leerlo de su archivo."""
        value = self._serializer.unpack_field(raw, position)
        if position not in self._positions:
            return value
        return self._resolved(value)

    def release(self, record: bytes) -> None:
        """Borra los valores TEXT a los que apunta un registro que deja de existir."""
        for position in self._positions:
            reference = self._reference_at(record, position)
            if reference is not None:
                self._store().delete(reference)

    def _draft(self, row: Sequence[Value]) -> bytes:
        """El registro de la fila con una dirección cualquiera en cada TEXT: comprueba todos
        los valores sin escribir ninguno."""
        if not self._positions or len(row) != len(self.schema):
            return self._serializer.pack(row)
        values = list(row)
        for position in self._positions:
            text = row[position]
            if text is None:
                continue
            _require_text(text)
            values[position] = _PLACEHOLDER
        return self._serializer.pack(values)

    def _replaced(self, reference: RecordId | None, before: Value, after: Value) -> bytes | None:
        if after == before and reference is not None:
            return reference.pack()
        if after is None:
            if reference is not None:
                self._store().delete(reference)
            return None
        encoded = _encoded(after)
        if reference is None:
            return self._store().put(encoded).pack()
        return self._store().replace(reference, encoded).pack()

    def _reference_at(self, record: bytes, position: int) -> RecordId | None:
        raw = self._serializer.unpack_field(record, position)
        return None if raw is None else RecordId.unpack(_reference_bytes(raw))

    def _resolved(self, raw: Value) -> Value:
        if raw is None:
            return None
        return self._store().get(RecordId.unpack(_reference_bytes(raw))).decode(TEXT_ENCODING)

    def _store(self) -> ValueStore:
        if self._values is None:
            raise MissingValueStoreError("el esquema no tiene columnas TEXT")
        return self._values


# Cualquier dirección sirve para validar un registro: nunca llega a escribirse.
_PLACEHOLDER = LOWEST_RECORD_ID.pack()


def _encoded(text: Value) -> bytes:
    return _require_text(text).encode(TEXT_ENCODING)


def _require_text(value: Value) -> str:
    if not isinstance(value, str):
        raise InvalidValueError(f"se esperaba una cadena, llegó {value!r}")
    return value


def _reference_bytes(raw: Value) -> bytes:
    if not isinstance(raw, bytes):
        raise InvalidValueError(f"un TEXT guarda la dirección de su valor, no {raw!r}")
    return raw
