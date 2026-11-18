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
| `HNSW` | similitud | rutas y `VECTOR(n)` | `metric`, `m`, `ef_*`, `vocabulary` |
| `IVF` | similitud | rutas y `VECTOR(n)` | `metric`, `lists`, `probes`, `vocabulary` |

`vocabulary` solo vale sobre rutas: una columna `VECTOR(n)` ya guarda sus vectores.
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
from index.secondary import RowSource, SecondaryIndex
from index.similarity import (
    HNSW_KIND,
    IVF_KIND,
    MediaSimilarityIndex,
    VectorColumnIndex,
    VectorIndexSettings,
)
from index.vector import VectorMetric, vector_metric_named
from query.files import resolve_user_file
from sql.nodes import IndexType, OptionValue
from storage.record import FieldReader
from storage.schema import Field
from storage.types import OUT_OF_ROW_FIELD_TYPES, UNORDERED_FIELD_TYPES, FieldType, is_number
from text import DEFAULT_LANGUAGE

IndexOptions = Mapping[str, OptionValue]
IndexOpener = Callable[
    [Path, FieldReader, str, EngineConfig, IndexOptions, RowSource], SecondaryIndex
]
OptionlessOpener = Callable[[Path, FieldReader, str, EngineConfig], SecondaryIndex]
ColumnRule = Callable[[Field], str | None]
IndexSettings = dict[str, OptionValue]
SettingsRule = Callable[[IndexOptions, EngineConfig], IndexSettings]
LANGUAGE_OPTION = "idioma"
METRIC_OPTION = "metric"
M_OPTION = "m"
EF_CONSTRUCTION_OPTION = "ef_construction"
EF_SEARCH_OPTION = "ef_search"
LISTS_OPTION = "lists"
PROBES_OPTION = "probes"
VOCABULARY_OPTION = "vocabulary"
# El coseno es la métrica natural de los histogramas TF-IDF, como en la búsqueda de texto: no
# depende de cuántos descriptores tenga cada objeto.
DEFAULT_VECTOR_METRIC = VectorMetric.COSINE
HNSW_OPTIONS = frozenset(
    {METRIC_OPTION, M_OPTION, EF_CONSTRUCTION_OPTION, EF_SEARCH_OPTION, VOCABULARY_OPTION}
)
IVF_OPTIONS = frozenset({METRIC_OPTION, LISTS_OPTION, PROBES_OPTION, VOCABULARY_OPTION})
TEXT_FIELD_TYPES = frozenset({FieldType.STRING, FieldType.TEXT})


class IndexOptionError(DomainError):
    """Una opción del `WITH (...)` de un índice tiene un valor que el método no entiende."""


def _no_settings(_: IndexOptions, __: EngineConfig) -> IndexSettings:
    return {}


@unique
class Capability(Enum):
    """Lo que un índice sabe responder sin recorrer la tabla."""

    EQUALITY = "igualdad"
    RANGE = "rango"
    SPATIAL = "espacial"
    TEXT_SEARCH = "texto"
    SIMILARITY = "similitud"


@dataclass(frozen=True, slots=True)
class IndexMethod:
    """Un método de índice secundario.

    Attributes:
        method: el nombre con que se declara en SQL.
        capabilities: las condiciones que sabe responder.
        opener: abre o crea el índice sobre una columna de una tabla, con las opciones de
            su `WITH (...)`, ya validadas por nombre, y las filas de la tabla, que lee un
            índice derivado de ellas.
        refusal_for: por qué no sirve para esa columna, o `None` si sirve.
        files_of: los archivos que ocupa un índice guardado en esa ruta.
        options: las opciones que admite en un `WITH (...)`.
        settings_of: el valor de cada opción con que queda un índice que pide esas: la
            pedida o, si no, la de omisión.
    """

    method: IndexType
    capabilities: frozenset[Capability]
    opener: IndexOpener
    refusal_for: ColumnRule
    files_of: Callable[[Path], list[Path]]
    options: frozenset[str] = frozenset()
    settings_of: SettingsRule = _no_settings

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def unknown_options(self, options: IndexOptions) -> list[str]:
        """Las opciones pedidas que este método no usa."""
        return sorted(name for name in options if name.lower() not in self.options)


def method_named(method: IndexType) -> IndexMethod | None:
    """El método de índice secundario con ese nombre, o `None` si no lo es."""
    return _METHODS.get(method)


def effective_options(
    method: IndexType, options: IndexOptions, config: EngineConfig
) -> IndexSettings:
    """Las opciones con que queda de verdad un índice declarado con `options`: dos
    declaraciones que solo difieren en escribir o no un valor por omisión son el mismo
    índice. Es lo que compara `CREATE INDEX IF NOT EXISTS`.

    Raises:
        IndexOptionError: si alguna opción tiene un valor que el método no entiende.
        UnknownVectorMetricError: si la métrica no existe.
    """
    known = method_named(method)
    return {} if known is None else known.settings_of(options, config)


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


def _similarity_columns(method: IndexType) -> ColumnRule:
    def refusal_for(field: Field) -> str | None:
        if field.type in TEXT_FIELD_TYPES or field.type is FieldType.VECTOR:
            return None
        return (
            f"un índice {method.value} va sobre la columna con la ruta de cada imagen o audio "
            f"(VARCHAR o TEXT) o sobre una VECTOR(n), y '{field.name}' es {field.type.value}"
        )

    return refusal_for


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


def _similarity_files(path: Path) -> list[Path]:
    """Los archivos de un índice de similitud, sea sobre rutas o sobre vectores."""
    return list(
        dict.fromkeys([*MediaSimilarityIndex.files_of(path), *VectorColumnIndex.files_of(path)])
    )


def _without_options(opener: OptionlessOpener) -> IndexOpener:
    """El opener de un método que no tiene opciones: un `WITH` suyo ya se rechazó."""

    def open_index(
        path: Path,
        rows: FieldReader,
        column: str,
        config: EngineConfig,
        _: IndexOptions,
        __: RowSource,
    ) -> SecondaryIndex:
        return opener(path, rows, column, config)

    return open_index


def _open_text_search(
    path: Path,
    rows: FieldReader,
    column: str,
    config: EngineConfig,
    options: IndexOptions,
    _: RowSource,
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


def _similarity_opener(kind: str) -> IndexOpener:
    def open_index(
        path: Path,
        rows: FieldReader,
        column: str,
        config: EngineConfig,
        options: IndexOptions,
        table_rows: RowSource,
    ) -> SecondaryIndex:
        settings = _vector_settings(kind, options, config)
        if rows.schema.field_of(column).type is FieldType.VECTOR:
            if settings.vocabulary_size is not None:
                raise IndexOptionError(
                    f"la opción {VOCABULARY_OPTION} es del vocabulario de un índice sobre rutas "
                    f"de archivos, y '{column}' ya guarda sus vectores"
                )
            return VectorColumnIndex(path, rows, column, config, settings, table_rows)
        return MediaSimilarityIndex(
            path, rows, column, config, settings, lambda text: resolve_user_file(text, config)
        )

    return open_index


def _text_settings(options: IndexOptions, _: EngineConfig) -> IndexSettings:
    return {LANGUAGE_OPTION: _language_of(options)}


def _vector_settings(
    kind: str, options: IndexOptions, config: EngineConfig
) -> VectorIndexSettings:
    named = {name.lower(): value for name, value in options.items()}
    return VectorIndexSettings(
        kind=kind,
        metric=_metric_of(named),
        m=_positive(named, M_OPTION, config.hnsw_m),
        ef_construction=_positive(named, EF_CONSTRUCTION_OPTION, config.hnsw_ef_construction),
        ef_search=_positive(named, EF_SEARCH_OPTION, config.hnsw_ef_search),
        lists=_optional_positive(named, LISTS_OPTION),
        probes=_optional_positive(named, PROBES_OPTION),
        vocabulary_size=_optional_positive(named, VOCABULARY_OPTION),
    )


def _similarity_settings(kind: str) -> SettingsRule:
    def settings_of(options: IndexOptions, config: EngineConfig) -> IndexSettings:
        settings = _vector_settings(kind, options, config)
        return {
            METRIC_OPTION: settings.metric.value,
            M_OPTION: settings.m,
            EF_CONSTRUCTION_OPTION: settings.ef_construction,
            EF_SEARCH_OPTION: settings.ef_search,
            LISTS_OPTION: settings.lists,
            PROBES_OPTION: settings.probes,
            VOCABULARY_OPTION: settings.vocabulary_size,
        }

    return settings_of


def _metric_of(options: IndexOptions) -> VectorMetric:
    """La métrica de `metric = cosine`; sin ella, el coseno.

    Raises:
        IndexOptionError: si no es el nombre de una métrica escrito como texto.
        UnknownVectorMetricError: si no es ninguna de las tres.
    """
    value = options.get(METRIC_OPTION)
    if value is None:
        return DEFAULT_VECTOR_METRIC
    if not isinstance(value, str):
        raise IndexOptionError(
            f"la opción {METRIC_OPTION} es el nombre de una métrica, como cosine, y llegó {value!r}"
        )
    return vector_metric_named(value)


def _positive(options: IndexOptions, name: str, default: int) -> int:
    value = _optional_positive(options, name)
    return default if value is None else value


def _optional_positive(options: IndexOptions, name: str) -> int | None:
    """Raises:
    IndexOptionError: si la opción está y no es un entero positivo."""
    value = options.get(name)
    if value is None:
        return None
    if not is_number(value) or isinstance(value, bool) or value != int(value) or value < 1:
        raise IndexOptionError(f"la opción {name} es un entero positivo y llegó {value!r}")
    return int(value)


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
        settings_of=_text_settings,
    ),
    IndexType.HNSW: IndexMethod(
        method=IndexType.HNSW,
        capabilities=frozenset({Capability.SIMILARITY}),
        opener=_similarity_opener(HNSW_KIND),
        refusal_for=_similarity_columns(IndexType.HNSW),
        files_of=_similarity_files,
        options=HNSW_OPTIONS,
        settings_of=_similarity_settings(HNSW_KIND),
    ),
    IndexType.IVF: IndexMethod(
        method=IndexType.IVF,
        capabilities=frozenset({Capability.SIMILARITY}),
        opener=_similarity_opener(IVF_KIND),
        refusal_for=_similarity_columns(IndexType.IVF),
        files_of=_similarity_files,
        options=IVF_OPTIONS,
        settings_of=_similarity_settings(IVF_KIND),
    ),
}
