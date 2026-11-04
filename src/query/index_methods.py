"""Los métodos de índice secundario: qué sabe hacer cada uno y sobre qué columnas sirve.

Es el único lugar que conoce los métodos. La tabla los abre a través de él, el motor
comprueba con él si un método sirve para una columna y qué opciones admite, y el
planificador le pregunta qué índice de una columna sabe responder una condición. Añadir un
método es añadir una entrada aquí: ninguna otra pieza enumera los métodos que existen.

| Método | Responde | Columnas | Opciones |
|---|---|---|---|
| `BTREE` | igualdad y rango | las que tienen orden y caben en una clave | — |
| `HASH` | igualdad | las que caben en una clave, salvo POINT | — |
| `RTREE` | espacial y un punto exacto | POINT | — |
| `INVERTED` | búsqueda de texto (`MATCH`) | VARCHAR y TEXT | `idioma` |
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from config import EngineConfig
from errors import DomainError
from index.bplustree import UnclusteredBPlusIndex
from index.hash import HashSecondaryIndex
from index.inverted import TextSearchIndex
from index.rtree import SpatialIndex
from index.secondary import SecondaryIndex
from sql.nodes import IndexType, OptionValue
from storage.record import FieldReader
from storage.schema import Field
from storage.types import OUT_OF_ROW_FIELD_TYPES, UNORDERED_FIELD_TYPES, FieldType
from text import DEFAULT_LANGUAGE

IndexOptions = Mapping[str, OptionValue]
IndexOpener = Callable[[Path, FieldReader, str, EngineConfig, IndexOptions], SecondaryIndex]
OptionlessOpener = Callable[[Path, FieldReader, str, EngineConfig], SecondaryIndex]
ColumnRule = Callable[[Field], str | None]
LANGUAGE_OPTION = "idioma"
TEXT_FIELD_TYPES = frozenset({FieldType.STRING, FieldType.TEXT})


class IndexOptionError(DomainError):
    """Una opción del `WITH (...)` de un índice tiene un valor que el método no entiende."""


@unique
class Capability(Enum):
    """Lo que un índice sabe responder sin recorrer la tabla."""

    EQUALITY = "igualdad"
    RANGE = "rango"
    SPATIAL = "espacial"
    TEXT_SEARCH = "texto"


@dataclass(frozen=True, slots=True)
class IndexMethod:
    """Un método de índice secundario.

    Attributes:
        method: el nombre con que se declara en SQL.
        capabilities: las condiciones que sabe responder.
        opener: abre o crea el índice sobre una columna de una tabla, con las opciones de
            su `WITH (...)`, ya validadas por nombre.
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

    def unknown_options(self, options: IndexOptions) -> list[str]:
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


def _text_only(field: Field) -> str | None:
    if field.type in TEXT_FIELD_TYPES:
        return None
    return (
        f"un índice INVERTED necesita una columna de texto (VARCHAR o TEXT) y '{field.name}' "
        f"es {field.type.value}"
    )


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


def _without_options(opener: OptionlessOpener) -> IndexOpener:
    """El opener de un método que no tiene opciones: un `WITH` suyo ya se rechazó."""

    def open_index(
        path: Path, rows: FieldReader, column: str, config: EngineConfig, _: IndexOptions
    ) -> SecondaryIndex:
        return opener(path, rows, column, config)

    return open_index


def _open_text_search(
    path: Path, rows: FieldReader, column: str, config: EngineConfig, options: IndexOptions
) -> SecondaryIndex:
    return TextSearchIndex(path, rows, column, config, _language_of(options))


def _language_of(options: IndexOptions) -> str:
    """El idioma pedido con `idioma='es'`; sin él, el idioma por omisión del analizador.

    Raises:
        IndexOptionError: si el valor no es el código de un idioma escrito como texto.
    """
    named = {name.lower(): value for name, value in options.items()}
    language = named.get(LANGUAGE_OPTION, DEFAULT_LANGUAGE)
    if not isinstance(language, str):
        raise IndexOptionError(
            f"la opción {LANGUAGE_OPTION} es el código de un idioma, como '{DEFAULT_LANGUAGE}', "
            f"y llegó {language!r}"
        )
    return language


_METHODS: dict[IndexType, IndexMethod] = {
    IndexType.BTREE: IndexMethod(
        method=IndexType.BTREE,
        capabilities=frozenset({Capability.EQUALITY, Capability.RANGE}),
        opener=_without_options(UnclusteredBPlusIndex),
        refusal_for=_ordered_only,
        files_of=_single_file,
    ),
    IndexType.HASH: IndexMethod(
        method=IndexType.HASH,
        capabilities=frozenset({Capability.EQUALITY}),
        opener=_without_options(HashSecondaryIndex),
        refusal_for=_inline_non_point,
        files_of=HashSecondaryIndex.files_of,
    ),
    IndexType.RTREE: IndexMethod(
        method=IndexType.RTREE,
        capabilities=frozenset({Capability.EQUALITY, Capability.SPATIAL}),
        opener=_without_options(SpatialIndex),
        refusal_for=_points_only,
        files_of=_single_file,
    ),
    IndexType.INVERTED: IndexMethod(
        method=IndexType.INVERTED,
        capabilities=frozenset({Capability.TEXT_SEARCH}),
        opener=_open_text_search,
        refusal_for=_text_only,
        files_of=TextSearchIndex.files_of,
        options=frozenset({LANGUAGE_OPTION}),
    ),
}
