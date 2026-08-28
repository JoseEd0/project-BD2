"""Descripción de la forma de una tabla: campos, tipos y longitudes."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .types import FieldType, InvalidValueError, StorageError


class UnknownFieldError(StorageError):
    """El nombre pedido no corresponde a ningún campo del esquema."""


class DuplicateFieldError(StorageError):
    """El esquema declara dos veces el mismo nombre de campo."""


@dataclass(frozen=True, slots=True)
class Field:
    """Un campo del esquema.

    Attributes:
        name: nombre del campo tal como lo escribió el usuario.
        type: tipo de almacenamiento.
        length: longitud fija para STRING, BYTES y VECTOR; `None` en el resto.
        nullable: si admite NULL.
    """

    name: str
    type: FieldType
    length: int | None = None
    nullable: bool = True


class Schema:
    """Secuencia ordenada de campos con búsqueda por nombre sin distinguir mayúsculas.

    Raises:
        DuplicateFieldError: si dos campos comparten nombre.
        InvalidValueError: si el esquema no tiene campos.
    """

    def __init__(self, fields: Sequence[Field]) -> None:
        if not fields:
            raise InvalidValueError("un esquema necesita al menos un campo")
        self._fields = tuple(fields)
        self._positions: dict[str, int] = {}
        for position, field in enumerate(self._fields):
            key = field.name.lower()
            if key in self._positions:
                raise DuplicateFieldError(f"campo duplicado '{field.name}'")
            self._positions[key] = position

    @property
    def fields(self) -> tuple[Field, ...]:
        return self._fields

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self._fields)

    def __len__(self) -> int:
        return len(self._fields)

    def __iter__(self) -> Iterator[Field]:
        return iter(self._fields)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Schema):
            return NotImplemented
        return self._fields == other._fields

    def __hash__(self) -> int:
        return hash(self._fields)

    def __repr__(self) -> str:
        return f"Schema({list(self._fields)!r})"

    def position_of(self, name: str) -> int:
        """Posición del campo dentro del registro.

        Raises:
            UnknownFieldError: si el campo no existe.
        """
        try:
            return self._positions[name.lower()]
        except KeyError:
            raise UnknownFieldError(f"el esquema no tiene el campo '{name}'") from None

    def field_of(self, name: str) -> Field:
        """Campo con ese nombre.

        Raises:
            UnknownFieldError: si el campo no existe.
        """
        return self._fields[self.position_of(name)]

    def has_field(self, name: str) -> bool:
        return name.lower() in self._positions

    def project(self, names: Sequence[str]) -> Schema:
        """Esquema con solo los campos indicados, en el orden dado."""
        return Schema([self.field_of(name) for name in names])


def single_field_schema(field: Field) -> Schema:
    """Esquema de un solo campo, usado por los índices para serializar claves."""
    return Schema([field])


__all__ = [
    "DuplicateFieldError",
    "Field",
    "FieldType",
    "Schema",
    "UnknownFieldError",
    "single_field_schema",
]
