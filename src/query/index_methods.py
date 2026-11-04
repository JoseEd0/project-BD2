"""Los métodos de índice secundario: qué sabe hacer cada uno y sobre qué columnas sirve.

Es el único lugar que conoce los métodos. La tabla los abre a través de él, el motor
comprueba con él si un método sirve para una columna y qué opciones admite, y el
planificador le pregunta qué índice de una columna sabe responder una condición. Añadir un
método es añadir una entrada aquí: ninguna otra pieza enumera los métodos que existen.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from config import EngineConfig
from index.bplustree import UnclusteredBPlusIndex
from index.hash import HashSecondaryIndex
from index.rtree import SpatialIndex
from index.secondary import SecondaryIndex
from sql.nodes import IndexType, OptionValue
from storage.record import FieldReader
from storage.schema import Field
from storage.types import OUT_OF_ROW_FIELD_TYPES, UNORDERED_FIELD_TYPES, FieldType

IndexOpener = Callable[[Path, FieldReader, str, EngineConfig], SecondaryIndex]
ColumnRule = Callable[[Field], str | None]


@unique
class Capability(Enum):
    """Lo que un índice sabe responder sin recorrer la tabla."""

    EQUALITY = "igualdad"
    RANGE = "rango"
    SPATIAL = "espacial"


@dataclass(frozen=True, slots=True)
class IndexMethod:
    """Un método de índice secundario.

    Attributes:
        method: el nombre con que se declara en SQL.
        capabilities: las condiciones que sabe responder.
        opener: abre o crea el índice sobre una columna de una tabla.
        refusal_for: por qué no sirve para esa columna, o `None` si sirve.
        files_of: los archivos que ocupa un índice guardado en esa ruta.
        options: las opciones que admite en un `WITH (...)`.
    """

    method: IndexType
    capabilities: frozenset[Capability]
    opener: IndexOpener
    refusal_for: ColumnRule
    files_of: Callable[[Path], list[Path]]
    options: frozenset[str] = frozenset()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def unknown_options(self, options: Mapping[str, OptionValue]) -> list[str]:
        """Las opciones pedidas que este método no usa."""
        return sorted(name for name in options if name.lower() not in self.options)


def method_named(method: IndexType) -> IndexMethod | None:
    """El método de índice secundario con ese nombre, o `None` si no lo es."""
    return _METHODS.get(method)


def secondary_method_names() -> str:
    """Los métodos disponibles, para un mensaje de error."""
    return ", ".join(method.value for method in _METHODS)


def _ordered_only(field: Field) -> str | None:
    """Un B+ ordena: solo sirve para lo que tiene orden y cabe en una clave."""
    if field.type is FieldType.POINT:
        return _point_refusal(field, IndexType.BTREE)
    if field.type in OUT_OF_ROW_FIELD_TYPES:
        return _out_of_row_refusal(field, IndexType.BTREE)
    if field.type in UNORDERED_FIELD_TYPES:
        return (
            f"la columna '{field.name}' es {field.type.value}, que no tiene orden: "
            "no admite un índice BTREE"
        )
    return None


def _inline_non_point(field: Field) -> str | None:
    """Un hash sobre puntos solo encontraría filas en unas coordenadas exactas."""
    if field.type is FieldType.POINT:
        return _point_refusal(field, IndexType.HASH)
    if field.type in OUT_OF_ROW_FIELD_TYPES:
        return _out_of_row_refusal(field, IndexType.HASH)
    return None


def _points_only(field: Field) -> str | None:
    if field.type is FieldType.POINT:
        return None
    return f"un índice RTREE necesita una columna POINT y '{field.name}' es {field.type.value}"


def _point_refusal(field: Field, method: IndexType) -> str:
    return f"la columna '{field.name}' es POINT: solo admite un índice RTREE, no {method.value}"


def _out_of_row_refusal(field: Field, method: IndexType) -> str:
    """Un B+ y un hash guardan el valor entero en cada clave, de tamaño fijo: un texto sin
    longitud máxima no cabe en ella."""
    return (
        f"la columna '{field.name}' es {field.type.value}, cuyo valor vive fuera de la fila "
        f"y no tiene longitud máxima: no admite un índice {method.value}; usa VARCHAR(n)"
    )


def _single_file(path: Path) -> list[Path]:
    return [path]


_METHODS: dict[IndexType, IndexMethod] = {
    IndexType.BTREE: IndexMethod(
        method=IndexType.BTREE,
        capabilities=frozenset({Capability.EQUALITY, Capability.RANGE}),
        opener=UnclusteredBPlusIndex,
        refusal_for=_ordered_only,
        files_of=_single_file,
    ),
    IndexType.HASH: IndexMethod(
        method=IndexType.HASH,
        capabilities=frozenset({Capability.EQUALITY}),
        opener=HashSecondaryIndex,
        refusal_for=_inline_non_point,
        files_of=HashSecondaryIndex.files_of,
    ),
    IndexType.RTREE: IndexMethod(
        method=IndexType.RTREE,
        capabilities=frozenset({Capability.EQUALITY, Capability.SPATIAL}),
        opener=SpatialIndex,
        refusal_for=_points_only,
        files_of=_single_file,
    ),
}
