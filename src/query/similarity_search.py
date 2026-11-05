"""Reconocimiento de la búsqueda por similitud dentro de un SELECT: `SIMILAR_TO`,
`SIMILARITY_SCORE()`, `USING` y `WITH`.

Aquí se decide si una consulta busca imágenes o audios parecidos, en qué columna, con qué
índice y con qué métrica, y se rechaza antes de leer ninguna fila la que no se puede
responder.

| Forma en SQL | Qué es |
|---|---|
| `WHERE SIMILAR_TO('consulta.jpg', k = 10)` | las 10 filas más parecidas al archivo |
| `WHERE SIMILAR_TO(ruta, 'consulta.jpg', k = 10)` | lo mismo, nombrando la columna |
| `USING HNSW` / `USING IVF` | el índice; sin `USING`, el único que tenga la tabla |
| `WITH METRIC = cosine` | la métrica (`euclidean`, `dot` o `cosine`); sin ella, la del índice |
| `WITH ef_search = 100` / `WITH probes = 20` | el esfuerzo de HNSW o de IVF en esta consulta |
| `SIMILARITY_SCORE()` | el puntaje de cada fila; mayor es más parecido |

Como `MATCH`, `SIMILAR_TO` no se evalúa fila a fila: la resuelve el índice, y por eso va en
el WHERE unida al resto con `AND`. El resto del WHERE filtra **antes** de buscar, de modo
que la consulta devuelve las k filas más parecidas que lo cumplen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from errors import DomainError
from index.similarity import HNSW_KIND
from index.vector import VectorMetric, vector_metric_named
from multimedia import MediaKind, media_kind_of
from query.catalog import IndexDefinition
from query.expressions import constant_value, describe_expression, subexpressions
from query.files import resolve_user_file
from query.functions import MATCH, SIMILAR_TO, SIMILARITY_SCORE
from query.index_methods import Capability, method_named
from query.scored_search import (
    ScoreFunction,
    calls_anywhere,
    calls_to,
    is_conjunct,
    ordered_by,
    searched_source,
    sources_of,
)
from query.scored_search import served as served_search
from query.table import Table
from sql.nodes import (
    ColumnRef,
    Expression,
    FunctionCall,
    IndexType,
    OptionValue,
    SearchMethod,
    SelectStatement,
    TableRef,
)
from storage.types import is_number

# Como el puntaje de `MATCH`: el paréntesis impide escribir la columna en SQL sin comillas.
SIMILARITY_COLUMN = "similarity_score()"
SIMILARITY_LABEL = SIMILARITY_SCORE.lower()
K_ARGUMENT = "k"
METRIC_OPTION = "metric"
EF_SEARCH_OPTION = "ef_search"
PROBES_OPTION = "probes"
QUERY_ONLY = 1
SIMILARITY_METHODS = (IndexType.HNSW, IndexType.IVF)
COLUMN_AND_QUERY = 2


class SimilaritySearchError(DomainError):
    """La búsqueda por similitud de la consulta no se puede responder tal como está escrita."""


SIMILARITY_FUNCTION = ScoreFunction(
    SIMILARITY_SCORE, SIMILARITY_COLUMN, SIMILARITY_LABEL, SimilaritySearchError
)
SIMILARITY_REFERENCE = SIMILARITY_FUNCTION.reference


@dataclass(frozen=True, slots=True)
class SimilaritySearch:
    """Un `SIMILAR_TO` ya resuelto contra el catálogo.

    Attributes:
        table: la tabla en la que se busca.
        reference: el nombre o alias con que la consulta nombra esa tabla.
        column: la columna con las rutas, tal como la declara la tabla.
        index_name: su índice de similitud.
        method: HNSW o IVF.
        query: la ruta de la consulta tal como se escribió.
        query_path: el archivo de la consulta, ya resuelto y permitido.
        media: si la consulta es una imagen o un audio.
        k: cuántas filas devuelve.
        metric: la métrica con que se puntúa.
        effort: `ef_search` (HNSW) o `probes` (IVF) de esta consulta; `None`, el del índice.
        condition: el `SIMILAR_TO` del WHERE, que el acceso consume.
    """

    table: Table
    reference: str
    column: str
    index_name: str
    method: IndexType
    query: str
    query_path: Path
    media: MediaKind
    k: int
    metric: VectorMetric
    effort: int | None
    condition: FunctionCall


@dataclass(frozen=True, slots=True)
class SimilarityView:
    """Lo que la interfaz necesita para mostrar el resultado de un `SIMILAR_TO`: en qué
    columna están los archivos, qué se buscó y cómo.

    Attributes:
        table: tabla en la que se buscó.
        column: columna con las rutas de los archivos.
        index_name: el índice que respondió.
        method: HNSW o IVF.
        metric: la métrica con que se puntuó.
        k: cuántas filas pidió la consulta.
        query: la ruta de la consulta tal como se escribió.
        media: si son imágenes o audios.
    """

    table: str
    column: str
    index_name: str
    method: IndexType
    metric: VectorMetric
    k: int
    query: str
    media: MediaKind


def similarity_view(search: SimilaritySearch | None) -> SimilarityView | None:
    """La vista de un `SIMILAR_TO` ya resuelto por el planificador; `None` si no hubo."""
    if search is None:
        return None
    return SimilarityView(
        table=search.table.name,
        column=search.column,
        index_name=search.index_name,
        method=search.method,
        metric=search.metric,
        k=search.k,
        query=search.query,
        media=search.media,
    )


def similarity_search(
    statement: SelectStatement, tables: Mapping[str, Table], config: EngineConfig
) -> SimilaritySearch | None:
    """El `SIMILAR_TO` del WHERE resuelto, o `None` si la consulta no busca por similitud.

    Raises:
        SimilaritySearchError: si hay más de un `SIMILAR_TO` o va junto a un `MATCH`, si está
            dentro de un `OR` o un `NOT`, si sus argumentos no son una consulta constante y
            un `k` entero positivo, si no hay un índice de similitud que la responda, si el
            `USING` no es HNSW ni IVF o si el `WITH` pide algo que no es una métrica o el
            esfuerzo del índice; también si hay un `SIMILARITY_SCORE()` sin `SIMILAR_TO`.
        UnknownVectorMetricError: si la métrica no existe.
        LoaderError: si el archivo de la consulta no existe o sale de los directorios
            permitidos.
        UnsupportedMediaTypeError: si el archivo no es una imagen ni un audio.
        ExpressionError: si la columna nombrada no existe o es ambigua.
    """
    found = calls_to(SIMILAR_TO, statement.where)
    if not found:
        if calls_anywhere(statement, SIMILARITY_SCORE):
            raise SimilaritySearchError(
                "SIMILARITY_SCORE() es el puntaje de un SIMILAR_TO y esta consulta no tiene "
                "ninguno en su WHERE"
            )
        return None
    condition = _single_condition(statement, found)
    column, query, k = _arguments(condition)
    method = _index_method(statement.search_method)
    source, index = _searched_index(statement, tables, column, method)
    table = tables[source.name.lower()]
    settings = table.similarity_index(index.name).settings
    options = {name.lower(): value for name, value in statement.options.items()}
    query_path = resolve_user_file(query, config)
    return SimilaritySearch(
        table=table,
        reference=source.reference,
        column=index.column,
        index_name=index.name,
        method=index.method,
        query=query,
        query_path=query_path,
        media=media_kind_of(query_path),
        k=k,
        metric=_metric(options, settings.metric),
        effort=_effort(options, settings.kind),
        condition=condition,
    )


def served(statement: SelectStatement, search: SimilaritySearch) -> SelectStatement:
    """La consulta que queda cuando el acceso ya resolvió el `SIMILAR_TO`: sin él en el
    WHERE, con cada `SIMILARITY_SCORE()` leyendo el puntaje del acceso y sin `USING` ni
    `WITH`, que ya se usaron.

    Raises:
        SimilaritySearchError: si un `SIMILARITY_SCORE()` lleva argumentos.
    """
    return served_search(statement, search.condition, SIMILARITY_FUNCTION)


def ordered_by_similarity(statement: SelectStatement) -> bool:
    """Si la consulta ordena solo por el puntaje de mayor a menor: el orden del acceso."""
    return ordered_by(statement, SIMILARITY_REFERENCE)


def reads_similarity(expression: Expression) -> bool:
    """Si la expresión lee el puntaje, que solo existe después de buscar."""
    if expression == SIMILARITY_REFERENCE:
        return True
    return any(reads_similarity(child) for child in subexpressions(expression))


def _single_condition(statement: SelectStatement, found: list[FunctionCall]) -> FunctionCall:
    if len(found) > 1:
        raise SimilaritySearchError("una consulta admite un solo SIMILAR_TO")
    if calls_to(MATCH, statement.where):
        raise SimilaritySearchError(
            "una consulta busca texto con MATCH o por similitud con SIMILAR_TO, no las dos"
        )
    condition = found[0]
    if not is_conjunct(statement, condition):
        raise SimilaritySearchError(
            "SIMILAR_TO no se puede negar ni combinar con OR: va en el WHERE, unido al resto de "
            "las condiciones con AND"
        )
    return condition


def _arguments(condition: FunctionCall) -> tuple[ColumnRef | None, str, int]:
    """La columna (si se nombra), la ruta de la consulta y `k`."""
    arguments = condition.arguments
    keywords = {name.lower(): value for name, value in condition.keyword_arguments.items()}
    unknown = sorted(set(keywords) - {K_ARGUMENT})
    shape_is_wrong = len(arguments) not in (QUERY_ONLY, COLUMN_AND_QUERY)
    if shape_is_wrong or unknown or K_ARGUMENT not in keywords:
        raise SimilaritySearchError(
            "SIMILAR_TO recibe el archivo de la consulta y cuántas filas devolver: "
            "SIMILAR_TO('foto.jpg', k = 10), o SIMILAR_TO(ruta, 'foto.jpg', k = 10)"
        )
    column = None
    if len(arguments) == COLUMN_AND_QUERY:
        if not isinstance(arguments[0], ColumnRef):
            raise SimilaritySearchError(
                f"el primer de dos argumentos de SIMILAR_TO es la columna de las rutas, no "
                f"'{describe_expression(arguments[0])}'"
            )
        column = arguments[0]
    query = constant_value(arguments[-1])
    if not isinstance(query, str):
        raise SimilaritySearchError(
            "la consulta de SIMILAR_TO es la ruta de un archivo, como 'foto.jpg', y es "
            f"'{describe_expression(arguments[-1])}'"
        )
    k = constant_value(keywords[K_ARGUMENT])
    if not is_number(k) or isinstance(k, bool) or k != int(k) or k < 1:
        raise SimilaritySearchError(f"k es cuántas filas devolver, un entero positivo, y es {k!r}")
    return column, query, int(k)


def _index_method(chosen: SearchMethod | None) -> IndexType | None:
    if chosen is None:
        return None
    if isinstance(chosen, IndexType) and chosen in SIMILARITY_METHODS:
        return chosen
    raise SimilaritySearchError(
        f"SIMILAR_TO se busca con USING HNSW o USING IVF, no con {chosen.value}"
    )


def _searched_index(
    statement: SelectStatement,
    tables: Mapping[str, Table],
    column: ColumnRef | None,
    method: IndexType | None,
) -> tuple[TableRef, IndexDefinition]:
    """La tabla y el índice de similitud que responden la búsqueda.

    Sin columna, es el único índice de similitud de las tablas de la consulta (del método
    pedido, si se pide uno).
    """
    if column is not None:
        source, slot = searched_source(statement, tables, column)
        definition = tables[source.name.lower()].definition
        candidates = [(source, index) for index in definition.indexes_on(slot.name)]
    else:
        candidates = [
            (source, index)
            for source in sources_of(statement)
            for index in tables[source.name.lower()].definition.indexes
        ]
    usable = [
        (source, index)
        for source, index in candidates
        if _answers_similarity(index) and (method is None or index.method is method)
    ]
    if len(usable) == 1:
        return usable[0]
    asked = "" if method is None else f" {method.value}"
    if not usable:
        table = statement.source.name
        raise SimilaritySearchError(
            f"no hay un índice{asked} de similitud que responda este SIMILAR_TO; créalo sobre "
            f"la columna de las rutas, como CREATE INDEX ON {table} USING HNSW (ruta)"
        )
    names = ", ".join(
        f"{index.name} ({index.method.value} en {index.column})" for _, index in usable
    )
    raise SimilaritySearchError(
        f"hay varios índices{asked} de similitud ({names}): elige uno con USING o nombra la "
        "columna, como SIMILAR_TO(ruta, 'foto.jpg', k = 10)"
    )


def _answers_similarity(index: IndexDefinition) -> bool:
    method = method_named(index.method)
    return method is not None and method.supports(Capability.SIMILARITY)


def _metric(options: Mapping[str, OptionValue], indexed: VectorMetric) -> VectorMetric:
    value = options.get(METRIC_OPTION)
    if value is None:
        return indexed
    if not isinstance(value, str):
        raise SimilaritySearchError(
            f"METRIC es el nombre de una métrica, como cosine, y llegó {value!r}"
        )
    return vector_metric_named(value)


def _effort(options: Mapping[str, OptionValue], kind: str) -> int | None:
    """El `ef_search` de HNSW o los `probes` de IVF que pide la consulta.

    Raises:
        SimilaritySearchError: si el `WITH` pide otra cosa, la del otro método, o un valor
            que no es un entero positivo.
    """
    allowed = EF_SEARCH_OPTION if kind == HNSW_KIND else PROBES_OPTION
    unknown = sorted(set(options) - {METRIC_OPTION, allowed})
    if unknown:
        raise SimilaritySearchError(
            f"el WITH de un SIMILAR_TO con {kind.upper()} admite {METRIC_OPTION} y {allowed}; "
            f"no {', '.join(unknown)}"
        )
    value = options.get(allowed)
    if value is None:
        return None
    if not is_number(value) or isinstance(value, bool) or value != int(value) or value < 1:
        raise SimilaritySearchError(f"{allowed} es un entero positivo y llegó {value!r}")
    return int(value)
